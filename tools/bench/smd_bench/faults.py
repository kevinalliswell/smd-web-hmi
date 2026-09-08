"""Explicit inert faults, with board receipts and real UI recovery assertions."""

import asyncio
import re

from playwright.async_api import expect

from .browser import eventually


async def acknowledge_alarms(scenes):
    ui, worker = scenes.ui, scenes.worker
    snapshot = await worker.request("snapshot")
    for alarm in snapshot["alarms"]:
        if alarm["active"]:
            await worker.request("clear_alarm", alarm_id=alarm["alarm_id"])
    expected_ids = {alarm["alarm_id"] for alarm in snapshot["alarms"]}
    if expected_ids:
        await eventually(
            lambda: ui.api("/api/alarms/history"),
            lambda rows: expected_ids <= {row.get("wire_alarm_id") for row in rows},
        )
    await ui.go("/alarms")
    await ui.button("历史报警").click()
    await ui.button("刷新").click()
    for _ in range(20):
        snapshot = await worker.request("snapshot")
        if all(a["acknowledged"] for a in snapshot["alarms"]):
            break
        button = ui.page.get_by_role("button", name="确认", exact=True).filter(visible=True).first
        await expect(button).to_be_enabled(timeout=60000)
        await button.click()
        await eventually(
            lambda: worker.request("snapshot"),
            lambda value: sum(not a["acknowledged"] for a in value["alarms"])
            < sum(not a["acknowledged"] for a in snapshot["alarms"]),
        )
    else:
        raise AssertionError("alarm acknowledgment did not finish within bounded occurrences")
    await ui.shot("fault-alarms-acknowledged")


async def unknown_run(scenes):
    ui, worker = scenes.ui, scenes.worker
    scenes.stage("unknown_run_recovery")
    await scenes.activate(scenes.standard)
    await worker.request(
        "sample", values={"furnace_mc": 500000, "burden_mc": 490000, "displacement_um": 20000, "pressure_drop_pa": 100}
    )
    seeded = await worker.request("seed_unknown_run")
    run_id = seeded["run"]["run_id"]
    rows = await eventually(
        lambda: ui.api("/api/run-recoveries"), lambda rows: any(row["run_id"] == run_id for row in rows)
    )
    case = next(row for row in rows if row["run_id"] == run_id)
    await ui.go("/test")
    await expect(ui.button("▶ 启动试验")).to_be_disabled()
    await scenes.curve(drip=True)
    await worker.request("finish_measurement")
    await worker.request("complete_purge")
    await worker.request("complete_cooling")
    await scenes.state("completed")
    await expect(ui.button("确认本次实验结束")).to_be_disabled()
    await scenes.recover_logs()
    await ui.api("/api/status")
    await ui.go("/run-recoveries")
    await ui.page.get_by_role("button").filter(has_text=run_id).click()
    await ui.page.get_by_label("核查原因", exact=True).fill(
        "SmdBench 私有管道注入的合成运行；核查配对设备及运行标识一致，无实体输出"
    )
    await ui.click_response("确认归属并回放", f"/api/run-recoveries/{case['id']}/binding")
    await ui.page.get_by_role("status").filter(has_text="现有原始记录回放完成").wait_for(timeout=90000)
    detail = await ui.api(f"/api/run-recoveries/{case['id']}")
    if detail["review_state"] != "bound" or detail["replay_status"] != "complete" or not detail["source_count"]:
        raise AssertionError("unknown run did not preserve and replay its original records")
    if detail["test_id"] != "REC-" + str(case["id"]):
        raise AssertionError("unknown run acquired a fabricated ordinary test identity")
    await ui.shot("unknown-run-replayed")
    await ui.go("/test")
    await expect(ui.button("确认本次实验结束")).to_be_enabled(timeout=60000)
    await ui.button("确认本次实验结束").click()
    await ui.click_response("确认结束", "/api/commands")
    await scenes.state("idle")
    # The recovery archive has no original H0/sample/profile snapshot: report evidence must stay incomplete.
    await ui.api(
        f"/api/tests/{detail['test_id']}/metadata",
        method="PATCH",
        body={
            "report_context": {
                "abnormal_operations": "模拟实验；SmdBench 私有测试控制注入，无实体试样或执行器，未经真机认证（not_certified）。"
            },
            "reason": "核查本次控制板运行由 SmdBench 私有管道生成，补录合成来源说明；不补造高度、配方或工程配置。",
        },
    )
    await scenes.reports(detail["test_id"], "recovered_unknown")
    scenes.passed("unknown_run_recovery")


async def faults(scenes):
    ui, worker = scenes.ui, scenes.worker
    scenes.stage("fault_half_recipe")
    await scenes.activate(scenes.standard)
    before = (await worker.request("snapshot"))["active_recipe_digest"]
    await ui.go("/recipes")
    await ui.page.get_by_label("已保存配方", exact=True).select_option(scenes.custom["recipe_id"])
    await ui.page.get_by_label("不可变版本", exact=True).select_option(str(scenes.custom["version"]))
    await ui.button("校验设备能力与安全约束").click()
    await ui.page.get_by_text("设备校验通过，尚需下发并回读", exact=True).wait_for()
    await worker.request("half_recipe")
    await ui.button("下发并回读配方").click()
    path = f"/api/recipes/{scenes.custom['recipe_id']}/activate"
    # A mid-upload disconnect is an expected failed activation; it cannot replace the previous recipe.
    ui.expected_statuses.update((path, code) for code in (409, 502, 503, 504))
    async with ui.page.expect_response(
        lambda r: r.url.endswith("/activate") and r.request.method == "POST", timeout=60000
    ) as response:
        await ui.button("确认下发").click()
    reply = await response.value
    if reply.status not in {409, 502, 503, 504}:
        raise AssertionError("half-upload was incorrectly reported as activated")
    after = await eventually(
        lambda: worker.request("snapshot"),
        lambda value: any(r["fault"] == "half_recipe" for r in value["fault_receipts"]),
    )
    if after["active_recipe_digest"] != before:
        raise AssertionError("half-upload replaced the active recipe")
    await ui.ready()
    scenes.passed("half_recipe_atomicity")

    scenes.stage("fault_lost_start_reply")
    await scenes.activate(scenes.standard)
    test_id = "BENCH-" + scenes.run_id[:8] + "-LOST-REPLY"
    await worker.request("drop_reply", command="start_run")
    ui.expected_statuses.add(("/api/commands", 504))
    await scenes.start(test_id, scenes.standard, expected=504)
    receipt = await eventually(
        lambda: worker.request("snapshot"),
        lambda value: any(r["fault"] == "lost_reply" for r in value["fault_receipts"]),
    )
    starts = [row for row in receipt["wire_commands"] if row["command"] == "start_run"]
    async with ui.page.expect_response(
        lambda r: r.url.endswith("/query") and r.request.method == "POST", timeout=60000
    ) as queried:
        await ui.button("查询控制板结果").click()
    queried_response = await queried.value
    queried_data = (await queried_response.json()).get("data", {})
    if (
        queried_response.status != 200
        or queried_data.get("wire_operation", {}).get("status", queried_data.get("wire_status")) != "applied"
    ):
        raise AssertionError("lost-reply query did not prove the board applied the original start")
    await ui.page.get_by_role("heading", name="启动试验").wait_for(state="hidden")
    after = await worker.request("snapshot")
    if [r for r in after["wire_commands"] if r["command"] == "start_run"] != starts:
        raise AssertionError("result query resent a side-effecting start")
    scenes.passed("lost_reply_not_resent")
    await worker.request("sample", values={"furnace_mc": 700000, "burden_mc": 690000})
    previous_boot = after["boot_id"]
    rebooted = await worker.request("reboot")
    if rebooted["boot_id"] == previous_boot or rebooted["run"]["state"] not in {"safe_disposal", "fault"}:
        raise AssertionError("reboot failed to preserve the running safety disposition")
    await acknowledge_alarms(scenes)
    await ui.go("/test")
    state = await worker.request("snapshot")
    if state["run"]["state"] == "fault":
        await expect(ui.button("复位已解除的故障")).to_be_enabled(timeout=60000)
        await ui.button("复位已解除的故障").click()
        await ui.page.get_by_label("故障复位依据", exact=True).fill("SmdBench 合成重启故障已解除；继续安全处置")
        await ui.click_response("确认复位", "/api/commands")
    await scenes.cool_and_ack()
    await scenes.reports(test_id, "abort")
    scenes.passed("board_reboot_safe_recovery")
    scenes.stage("fault_source_gap")
    await scenes.activate(scenes.standard)
    gap_test = "BENCH-" + scenes.run_id[:8] + "-SOURCE-GAP"
    await scenes.start(gap_test, scenes.standard)
    point = await worker.request("sample", values={"furnace_mc": 600000, "burden_mc": 590000, "displacement_um": 20000})
    missing = point["log"]["newest_record_seq"]
    injected = await worker.request("log_gap", sequences=[missing])
    if injected["fault_receipts"][-1] != {"fault": "log_gap", "sequences": [missing]}:
        raise AssertionError("source gap was not acknowledged by the test device")
    await scenes.curve()
    await worker.request("finish_measurement")
    await scenes.cool_and_ack()
    await scenes.reports(gap_test, "source_gap")
    scenes.passed("source_log_gap_remains_incomplete")
    if scenes.installation is None:
        raise AssertionError("host restart scenario requires an owned process/service adapter")
    scenes.stage("fault_host_service_restart")
    await scenes.activate(scenes.standard)
    restart_test = "BENCH-" + scenes.run_id[:8] + "-HOST-RESTART"
    await scenes.start(restart_test, scenes.standard)
    before_restart = await worker.request("snapshot")
    async with ui.stopped_service():
        await asyncio.to_thread(scenes.installation.stop)
        # The board and its source storage stay alive while the real host process stops.
        await asyncio.to_thread(scenes.installation.start)
    await ui.ready()
    recovered = await worker.request("snapshot")
    if (
        recovered["boot_id"] != before_restart["boot_id"]
        or recovered["run"]["run_id"] != before_restart["run"]["run_id"]
    ):
        raise AssertionError("host service restart changed the board boot or original run")
    if recovered["run"]["state"] not in {"safe_disposal", "fault"}:
        raise AssertionError("host disconnection did not retain the configured safe disposition")
    await acknowledge_alarms(scenes)
    await scenes.cool_and_ack()
    await scenes.reports(restart_test, "abort")
    scenes.passed("host_service_restart")
    await unknown_run(scenes)
    scenes.passed("faults")
