# HostComm 实验扩展候选契约

> 适用范围：本文保留 `0.3.0-rc.2` 的 HostComm **1.0** 客户端与 Mock 行为，供兼容维护和历史核查。当前候选 `0.3.0-rc.3` 已增加不兼容的 [HostComm v2.0](hostcomm/v2/README.md) 上位机适配与无执行器模拟器，见[运行与配对](hostcomm/v2/runtime.md)、[软件验证](verification/2026-09-06-hostcomm-v2-runtime.md)。本文中的待冻结字段及供应商假设是旧实现背景；固件尚未开发，不能将1.0字段或既有软件测试视为真实固件验收。

维护角色：后端与固件负责人。以下约定记录2026-09-06的 `0.3.0-rc.2` 兼容实现字段，**不是供应商已冻结的固件接口**，下文“当前”均指该历史范围。决策见 [ADR-006](decisions/ADR-006-candidate-experiment-contracts.md)，基础帧与外部交付见 [HostComm 1.0 总览](GB_T34211_HostComm上位机通信协议开发需求说明.md)和[待确认清单](待确认事项与接口对齐清单.md)。

## 能力与来源

`hello_ack.payload.capabilities` 必须为字符串列表。基础必需 `status_snapshot`、`command`；以下能力逐项协商，不接受仅出现同名数据就启用。协议仍为 `protocol_version: "1.0"`。实际来源：[握手校验](../backend/app/hostcomm/contracts.py)、[配方模型](../backend/app/services/recipe_definition.py)、[生命周期](../backend/app/services/test_session_service.py)、[连续性](../backend/app/services/telemetry_integrity.py)。

| 能力 | 新增含义 | 缺失时的软件行为 |
|---|---|---|
| `recipe_v1` | 参数中携带配方身份/定义，设备返回只读安全范围，启动核对执行版本 | 禁止选定配方激活/启动；旧参数实验记 custom / legacy_unverified |
| `run_lifecycle_v1` | 状态快照包含匹配 test_id 的测定完成和安全终态 | 不依据状态别名自动闭合历史实验；保留未闭合、核查记录 |
| `measurement_events_v1` | 首滴布尔事件及独立有效性 | 不用滴落重量阈值替代首滴，不签发有效无滴落值 |
| `telemetry_sequence_v1` | 每次设备启动内的采样连续序号 | 数据完整性保持 unknown；TCP 连通不是完整性证明 |

另有 `request_correlation_v1`：设备快照响应在 payload 回显 `request_msg_id`。未声明时客户端串行等待同类型快照，保留旧协议兼容限制，不能声称已精确区分推送与回读。操作持久 ID 是上位机资源，尚无真实板端跨重启去重保证。

## recipe_v1

设备 `parameters_snapshot.payload` 返回 `params`、`parameter_crc` 和顶层只读 `safety_profile`。`params.recipe` 必须且仅有以下四个键：

```json
{
  "recipe_id": "保存后的配方ID",
  "version": 1,
  "digest": "定义规范JSON的SHA-256十六进制摘要",
  "definition": {
    "schema_version": 1,
    "name": "配方名称",
    "mode": "custom",
    "description": "非标目的与偏离说明",
    "stages": []
  }
}
```

上例省略阶段内容以展示结构；空 `stages` 不可提交。定义允许 1—64 段，设备范围还可收紧。每段字段为 `name`、`kind`（ramp/hold/gas/cool）、可选 `furnace_target_c`、`ramp_c_min`，必填 `n2_l_min`、`co_l_min`、`exit` 和 `timeout_s`。`exit={signal,comparison,value}`：signal 为 furnace_c/burden_c/elapsed_s，comparison 为 gte/lt。温度单位 ℃、流量 L/min、速率 ℃/min、时间 s；气体标态待 Q2/Q5 对齐。时间条件必须早于 timeout；升温段有目标/速率，保温段有目标及 elapsed_s 条件。不能自由执行脚本、跳转、循环或解除联锁。

`safety_profile` 字段为 `version`、`temperature_max_c`、`ramp_max_c_min`、`n2_max_l_min`、`co_max_l_min`、`co_min_furnace_c`、`safe_end_burden_c`（≤200）、`max_stages`、可选 `rules_reference`。调用者不能通过参数覆盖该对象。最后一段必须撤 CO、通正流量 N2，并以 `burden_c lt` 不高于安全上限的条件结束。实际温度联锁必须由板端持续判断，上位机的静态配方校验不能替代它。

省略 `furnace_target_c` 的真实板端语义尚待 Q15 确认。当前 Mock 在此情况下不推进模拟炉温，不表示继续执行前段目标。语义冻结前，上位机采取保守静态限制：记录最近明确声明的炉温目标；低目标的限制跨后续省略目标的阶段保留，不能仅凭该阶段 `furnace_c gte` 出口解除。须明确声明不低于 CO 门限的目标，并由炉温达标出口重新建立下界，才允许后续阶段申请 CO；升温段自身仍须满足入段温度许可。该记录是软件校验约束，不代表已知的固件有效设定值。

摘要算法：对 `RecipeDefinition.model_dump()` 的完整定义使用 UTF-8、`ensure_ascii=False`、排序键、无额外空格的 JSON 计算 SHA-256。跨语言浮点序列化必须由双方黄金向量冻结；该摘要与参数 CRC 是不同对象。版本不可覆盖；标准候选模板变更阶段后必须存为 custom 并返回 `deviations`。

激活复用 `set_parameters` 参数事务，将保存的四字段 bundle 合并到实际参数；核对参数 CRC、回读内容和设备安全范围后才返回 `readback_ok`。启动前再次回读并核对所选版本，`command.payload.params` 为：

```json
{"test_id":"ore-001","expected_recipe":{"recipe_id":"配方ID","version":1,"digest":"摘要"}}
```

装样、实验室与报告信息属于上位机归档，不发给设备。固件必须在真正受理启动处比较 expected_recipe 与当前执行配方；比较和启动不可被另一配方切换插入。原子生效、失败保留旧版本、重启持久化仍需真实固件验证。

## run_lifecycle_v1 与测定窗口

设备在 `status_snapshot.payload.state_machine` 发送：

```json
{
  "current_state": "N2Replace",
  "test_id": "ore-001",
  "measurement_complete": true,
  "safe_complete": false,
  "phase": "safe_disposal",
  "recipe_digest": "执行摘要",
  "stage_index": 5,
  "fault_reason": null
}
```

`current_state` 与 test_id 是身份/状态依据；`measurement_complete` 和 `safe_complete` 必须是 JSON 布尔值。phase、recipe_digest、stage_index、fault_reason 当前用于候选诊断，不能代替后台的身份与温度检查。确切状态枚举、事件保持时间和重启行为待 Q1/Q10 冻结。

后台首先保存本帧原始采样，再提交生命周期：首次匹配的 measurement_complete 保存 `measurement_completed_at`、`measurement_end_sample_id`、`measurement_device_timestamp` 和 `measurement_data_integrity`。边界帧属于测定，后续冷却数据留在原实验内，但不再参与指标；已进入冷却后返回 Standby 也不重新打开窗口。

自动安全闭合还要求 measurement.burden_temp_valid 为 true、burden_temp_deg_c 是有限数值且严格低于200。safe_complete 单独出现、温度缺失/无效、身份不匹配或缺能力均不能闭合。stop_test 被受理只记 `stop_requested_at` 与 stopping 阶段。测定未完成就安全停止，记录仍为 incomplete。

业务 phase 为 awaiting_device、measuring、stopping、safe_disposal、needs_review、completed；人工核查关闭使用 archived_incomplete。phase 是归档阶段，不等于数据有效性。`end_reason` 区分 completed、operator_stop、safe_end_incomplete、manual_review_incomplete 等来源；已有 end_time 也不自动意味着国标有效完成。

## measurement_events_v1

在 measurement 中发送 `first_drip: boolean` 和 `first_drip_valid: boolean`。首滴发生后候选 Mock 将 first_drip 保持 true；真实事件保持/时间戳规则须冻结，短脉冲不得因采样间隔丢失。Td 使用第一次有效 true 事件处的有效 burden_temp_deg_c。drip_weight_g 继续留作原始量，不能用 >0.5 g 等经验阈值代替事件。

有效完成而无滴落时，1580 ℃仅在测定完成、监测已验证、测定数据完整、有效到达1580 ℃的位移依据及完整监测窗口同时满足时使用，来源标为 completed_without_drip。缺首滴证据、失联、提前停止或测定窗口损坏均不能套用；ΔH 缺 Hs/Hd 仍未知。

## telemetry_sequence_v1 与完整性

每条 status_snapshot.payload 附带：

```json
{"telemetry":{"boot_id":"设备本次启动的稳定ID","sequence":42}}
```

boot_id 是非空字符串，长度≤128；sequence 是 0≤n<2^64 的 JSON 整数，布尔值无效。每个设备启动周期内，**采样序列**连续加1；重连不能重置，重启更换 boot_id。它不是各类消息共享的 msg_id。实时推送和 get_status 回读如何引用同一采样必须由固件固定，不得因查询凭空产生无法解释的样本缺口。

帧的 timestamp 是带时区的设备时间。后台额外生成 `_hostcomm`：msg_id、device_timestamp（原文保留）、received_at（UTC）、received_monotonic、session_id、dropped_callbacks、capabilities。这些是主机接收证据，**不是要求固件发送的字段**，且主机单调时钟只在本进程内解释。

游标保存在 `TestSession.measurement_basis_json.telemetry`：boot_id、sequence、device_timestamp、verified_samples、measurement_start_observed、gap_seen、unsupported_seen、callback_count。样本和游标同事务写入；重启后读持久游标检测缺口。重复、乱序、序号跳跃、boot变化、设备时钟回退、元数据损坏和回调丢弃增长均记录 HMI-TELEMETRY-INTEGRITY 事件及原始 `_hmi.telemetry_issues`。重复/乱序保留原始帧但不参与指标。

| 术语 | 当前含义 |
|---|---|
| 通信 online / fresh | 连接握手通过 / 状态在时效内；不保证测量有效或数据完整 |
| 量值 valid | 对应 `_valid` 及有限数值符合使用要求；不能由零值推定 |
| data_integrity=unknown | 尚无足够连续性证据，例如旧固件无能力 |
| data_integrity=incomplete | 已知缺口、损坏、身份待核查或不完整终止；后续正常帧不自动消除 |
| data_integrity=complete | 已有测定起点、至少两条有效源序样本、无已知缺口/不支持证据，且匹配测定完成；不是设备符合性认证 |
| measurement_data_integrity | 在测定边界冻结的完整性，区分后续安全处置期间缺口 |
| compliance=not_certified | 报告软件结果未经真实组合国标符合性签字 |

起点证据需观测炉温≤600 ℃的有效位移样本；缺少之前采样不能靠“从第一帧开始序号连续”弥补。尚未实现设备日志分块补传与可证明的缺口修复；相关数据始终保留限制。

## Mock 与验收边界

`python -m app.hostcomm.mock_server --extended-contract --time-scale 60` 可启用候选扩展与倍速；默认不启用扩展。模拟时钟独立于 TCP 连接，用于重连后观察阶段继续推进。其 mock-safety/1 范围、温度模型、首滴生成和时间倍速是测试夹具，不是设备校准或标准黄金轨迹。

冻结需验证：双端定义摘要/CRC、原子切换与实际配方、拒绝/丢回执/重启、测定与冷却终帧、首滴无效/无滴落、源序号重复/跳号/重启、气体标态与各安全策略。当前没有真实 STM32 固件冻结、干净断网现场 Windows 或 M5 的验收证据。
