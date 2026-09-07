"""UI lifecycle scenarios. Device-side boundary injections are explicitly synthetic."""

import asyncio
import json
import re
import secrets

import httpx
from playwright.async_api import expect

from .browser import eventually
from .reporting import check_metrics, inspect_document, report_metrics


class Scenarios:
    def __init__(self, browser, worker, database, run_id, result, installation=None):
        self.ui, self.worker, self.database, self.run_id = browser, worker, database, run_id
        self.result, self.installation = result, installation
        self.standard = self.custom = None
        self.last_report_metrics = None

    def passed(self, name, **details):
        self.result["assertions"].append({"name": name, "status": "passed", **details})

    def stage(self, name):
        self.result["last_stage"] = name
        self.ui.current_stage = name
        print(json.dumps({"stage": name}), flush=True)

    async def state(self, expected):
        return await eventually(
            lambda: self.worker.request("snapshot"), lambda value: value["run"]["state"] == expected
        )

    async def activate(self, recipe=None):
        ui = self.ui
        if recipe:
            await ui.go("/recipes")
            await ui.page.get_by_label("已保存配方", exact=True).select_option(recipe["recipe_id"])
            await ui.page.get_by_label("不可变版本", exact=True).select_option(str(recipe["version"]))
        await ui.button("校验设备能力与安全约束").click()
        await ui.page.get_by_text("设备校验通过，尚需下发并回读", exact=True).wait_for()
        await expect(ui.button("下发并回读配方")).to_be_enabled(timeout=60000)
        await ui.button("下发并回读配方").click()
        # Match the actual resource shape through the observed response, not a fabricated UI result.
        async with ui.page.expect_response(
            lambda r: r.url.endswith("/activate") and r.request.method == "POST"
        ) as pending:
            await ui.button("确认下发").click()
        response = await pending.value
        data = (await response.json()).get("data", {})
        if response.status != 200 or data.get("operation_status") != "verified" or data.get("readback_ok") is False:
            raise AssertionError("recipe activation was not verified by raw readback")
        await ui.page.get_by_text("设备已回读一致；启动时仍会核对版本与现场许可。", exact=True).wait_for()
        return data

    async def recipes(self):
        self.stage("recipe_ui")
        ui = self.ui
        await ui.go("/parameters")
        await ui.page.get_by_role("heading", name="设备工程配置（只读）").wait_for()
        if await ui.page.locator("section.profile input, section.profile select, section.profile textarea").count():
            raise AssertionError("engineering profile exposes editable physical settings")
        await ui.shot("02-engineering-profile")
        await ui.go("/recipes")
        await ui.button("从标准候选模板新建").click()
        await ui.page.get_by_label("配方名称", exact=True).fill("SmdBench 合成标准候选")
        self.standard = await ui.click_response("保存配方", "/api/recipes")
        if self.standard["definition"]["mode"] != "standard":
            raise AssertionError("unmodified template lost its standard mode")
        await self.activate()
        self.passed("standard_recipe")
        await ui.shot("03-standard-recipe")
        await ui.page.get_by_label("N₂ (L/min)", exact=True).first.fill("4")
        await ui.page.get_by_text("非标实验", exact=True).wait_for()
        self.custom = await ui.click_response("保存为新版本", "/api/recipes/" + self.standard["recipe_id"])
        if self.custom["version"] != 2 or self.custom["definition"]["mode"] != "custom":
            raise AssertionError("editing a standard stage did not create a new custom version")
        await self.activate(self.custom)
        self.passed("custom_recipe")
        await ui.shot("04-custom-recipe")

    async def start(self, test_id, recipe, *, expected=200):
        ui = self.ui
        await self.worker.request(
            "sample",
            values={"furnace_mc": 500000, "burden_mc": 490000, "displacement_um": 20000, "pressure_drop_pa": 100},
        )
        await ui.go("/test")
        await expect(ui.button("▶ 启动试验")).to_be_enabled(timeout=60000)
        await ui.button("▶ 启动试验").click()
        await ui.page.get_by_label("试验编号", exact=True).fill(test_id)
        await ui.page.get_by_label("原始料层高度 H (mm)", exact=False).fill("20")
        await ui.page.get_by_label("样品标识", exact=True).fill("SmdBench 合成数据，无实体试样")
        await ui.page.get_by_label("已保存配方", exact=True).select_option(recipe["recipe_id"])
        await ui.page.get_by_label("不可变版本", exact=True).select_option(str(recipe["version"]))
        await ui.button("下一步").click()
        result = await ui.click_response("确认启动", "/api/commands", expected=expected)
        await self.state("measuring")
        if expected == 200 and await ui.button("查询控制板结果").count():
            await ui.button("查询控制板结果").click()
        if expected == 200:
            await ui.page.get_by_role("heading", name="启动试验", exact=True).wait_for(state="hidden")
        return result

    async def curve(self, *, drip=False, invalid=False):
        for furnace, burden, displacement, pressure in (
            (600000, 590000, 20000, 100),
            (900000, 890000, 18000, 100),
            (1300000, 1290000, 12000, 500),
            (1600000, 1580000, 10000, 1000),
        ):
            await self.worker.request(
                "sample",
                values={
                    "furnace_mc": furnace,
                    "burden_mc": burden,
                    "displacement_um": displacement,
                    "pressure_drop_pa": pressure,
                },
            )
            if invalid and burden == 890000:
                await self.worker.request(
                    "sample", values={"pressure_drop_pa": {"value": 100, "quality": "invalid", "age_ms": 0}}
                )
                await self.worker.request("sample", values={"pressure_drop_pa": 100})
            if drip and burden == 1290000:
                await self.worker.request("first_drip")

    async def cool_and_ack(self):
        await self.state("safe_disposal")
        before = await self.worker.request("snapshot")
        await self.worker.request("sample")
        await self.worker.request("complete_purge")
        await self.state("cooling")
        await self.worker.request("complete_cooling")
        complete = await self.state("completed")
        if not complete["run"]["safe_complete"] or int(complete["run"]["safe_boundary"]["sample_seq"]) <= int(
            before["sample"]["sample_seq"]
        ):
            raise AssertionError("stop or natural finish did not retain cooling samples and a later safe boundary")
        ui = self.ui
        await ui.go("/test")
        if complete.get("fault_reset_required"):
            await expect(ui.button("复位已解除的故障")).to_be_enabled(timeout=60000)
            await ui.button("复位已解除的故障").click()
            await ui.page.get_by_label("故障复位依据", exact=True).fill(
                "SmdBench 合成故障已解除、所需报警已确认且冷却完成；不恢复原实验"
            )
            reset = await ui.click_response("确认复位", "/api/commands")
            if reset.get("operation_status") == "rejected":
                raise AssertionError("explicit fault reset was rejected")
        await expect(ui.button("确认本次实验结束")).to_be_enabled(timeout=60000)
        await ui.button("确认本次实验结束").click()
        await ui.click_response("确认结束", "/api/commands")
        await self.state("idle")

    async def recover_logs(self):
        ui = self.ui
        await ui.go("/operations")
        await ui.page.get_by_label("起始源记录序号", exact=True).fill("1")
        await expect(ui.button("补传设备日志")).to_be_enabled(timeout=60000)
        await ui.button("补传设备日志").click()
        await ui.page.get_by_text("源日志扫描完成；请在报告中核查完整性，已有缺口仍可能存在。", exact=False).wait_for(
            timeout=90000
        )

    async def reports(self, test_id, scenario, *, formats=("html",)):
        ui = self.ui
        self.stage("report_" + scenario)
        await self.recover_logs()
        await ui.go("/reports")
        await ui.page.get_by_label("试验", exact=True).select_option(test_id)
        collected = []
        for fmt in formats:
            await ui.page.get_by_label("报告格式", exact=True).select_option(fmt)
            submitted = await ui.click_response("生成报告", "/api/reports/generate", expected=202)
            task = await eventually(
                lambda: ui.api(f"/api/reports/tasks/{submitted['task_id']}"),
                lambda item: item["status"] in {"completed", "failed"},
                timeout=90,
            )
            if task["status"] != "completed":
                raise AssertionError("report generation task failed")
            rows = await ui.api("/api/reports")
            report = next(row for row in rows if row["test_id"] == test_id and row["format"] == fmt)
            row = (
                ui.page.locator("table.rep-table tbody tr")
                .filter(has_text=test_id)
                .filter(has=ui.page.get_by_role("cell", name=fmt, exact=True))
                .first
            )
            async with ui.page.expect_download() as download:
                await row.get_by_role("button", name="下载", exact=True).click()
            artifact = await download.value
            destination = ui.evidence / f"{test_id}.{fmt}"
            await artifact.save_as(destination)
            if await artifact.failure():
                raise AssertionError("browser report download failed")
            collected.append(inspect_document(destination, test_id))
            metrics = report_metrics(self.database, report["id"])
            check_metrics(metrics, scenario)
            self.last_report_metrics = metrics
        self.result.setdefault("reports", []).extend(collected)
        await ui.shot("report-" + scenario.replace("_", "-"))

    async def complete_experiment(self, scenario, recipe, *, formats=("html",)):
        self.stage(scenario)
        await self.activate(recipe)
        test_id = "BENCH-" + self.run_id[:8] + "-" + scenario.upper().replace("_", "-")
        await self.start(test_id, recipe)
        if scenario == "abort":
            await self.worker.request(
                "sample",
                values={"furnace_mc": 600000, "burden_mc": 590000, "displacement_um": 20000, "pressure_drop_pa": 100},
            )
            await self.ui.button("■ 停止试验").click()
            await self.ui.click_response("确认停止", "/api/commands")
        else:
            await self.curve(drip=scenario == "valid_drip", invalid=scenario == "invalid_sample")
            await self.worker.request("finish_measurement")
        await self.cool_and_ack()
        await self.reports(test_id, scenario, formats=formats)
        self.passed(scenario)
        return test_id

    async def permissions(self):
        self.stage("permissions")
        ui = self.ui
        username, password = "bench_view_" + self.run_id[:8], secrets.token_urlsafe(24)
        await ui.api("/api/users", method="POST", body={"username": username, "password": password, "role": "observer"})
        replacement = secrets.token_urlsafe(24)
        async with httpx.AsyncClient(base_url=ui.base, trust_env=False, timeout=30) as api:
            signed = await api.post("/api/auth/login", json={"username": username, "password": password})
            if signed.status_code != 200:
                raise AssertionError("observer fixture login failed")
            api.headers["Authorization"] = "Bearer " + signed.json()["data"]["token"]
            await ui.api(
                "/api/users/change-password",
                method="POST",
                body={"old_password": password, "new_password": replacement},
                client=api,
            )
            signed = await api.post("/api/auth/login", json={"username": username, "password": replacement})
            if signed.status_code != 200:
                raise AssertionError("observer fixture second login failed")
            api.headers["Authorization"] = "Bearer " + signed.json()["data"]["token"]
            context = await ui.browser.new_context()
            try:
                page = await context.new_page()
                await ui.login(username, replacement, page=page)
                await page.get_by_role("link", name=re.compile("^实验配方")).wait_for()
                await page.goto(ui.base + "/test")
                await page.get_by_text("当前角色（仅查看）无操作权限。", exact=True).wait_for()
                if await page.get_by_role("button", name="▶ 启动试验", exact=True).count():
                    raise AssertionError("observer sees a control action")
                await ui.api(
                    "/api/commands",
                    method="POST",
                    body={"command": "start_test", "params": {"test_id": "BENCH-OBSERVER", "original_height_mm": 20}},
                    expected=403,
                    client=api,
                )
            finally:
                await context.close()
        self.passed("permissions")

    async def full(self):
        await self.recipes()
        await self.complete_experiment("valid_drip", self.standard, formats=("html", "pdf", "xlsx"))
        self.passed("report_formats")
        await self.complete_experiment("no_drip", self.standard)
        await self.complete_experiment("abort", self.custom)
        await self.complete_experiment("invalid_sample", self.standard)
        await self.permissions()
