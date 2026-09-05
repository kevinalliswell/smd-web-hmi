# HostComm 契约：当前约定、目标要求与冻结边界

维护角色：后端负责人、STM32 固件负责人。历史审查基线 `dev@855c84d`；当前软件约定于2026-09-06更新，候选版本0.3.0-rc.2。这里的“当前约定”指仓库客户端和 Mock，尚未证明与真实固件一致。原 V0.1 需求全文已[归档](archive/dev-855c84d/docs/GB_T34211_HostComm上位机通信协议开发需求说明.md)；供应商冻结表、数据字典、SOP 等仍见[外部依赖清单](待确认事项与接口对齐清单.md)。

## 1. 硬件与控制边界

STM32 工控板是硬件中间件；HostComm 是上位机后台与该板之间的协议名称。浏览器/桌面 → 唯一后台 → STM32 → 仪表、传感器、MFC。安全联锁、实时配气、温控和异常处置属于板端及硬接线系统。

后台只发送请求，不提供强制 CO、绕过安全继电器、直接改写 I/O 或绕过板端任务直写仪表寄存器的能力。网络中断不代表停止命令已执行；网络恢复不自动恢复加热或 CO。板端断线策略须经固件安全设计和真机验收确认。

## 2. 当前帧及会话约定

| 项目 | 仓库约定 | 验证来源 |
|---|---|---|
| 传输 | STM32 TCP Server，后台 Client；默认端口 34211 | client / mock_server |
| 编码与边界 | UTF-8 JSON Lines，每帧 JSON 对象，以 `\n` 分隔，处理粘包/拆包 | protocol.FrameParser |
| 通用字段 | protocol_version、msg_id、type、timestamp、payload | protocol.make_frame |
| 当前版本 | `1.0` | 配置与 Mock，非供应商确认版本 |
| 心跳 | 默认 2 秒，连续超时阈值 3；主动断开失联连接并重连 | client / config |
| 报文边界 | 有长度与解析容错；最终帧长、采样周期和超时值待 Q2/Q10 | protocol / 外部冻结表 |

实现入口：[协议解析](../backend/app/hostcomm/protocol.py)、[客户端](../backend/app/hostcomm/client.py)、[Mock](../backend/app/hostcomm/mock_server.py)。

```json
{
  "protocol_version": "1.0",
  "msg_id": "pc-hello-001",
  "type": "hello",
  "timestamp": "2026-09-05T00:00:00+00:00",
  "payload": {"client_name": "smd-web-backend", "client_version": "0.3.0-rc.2"}
}
```

`hello_ack.payload` 的现有约定包含 result、fw_version、hw_version、device_profile_version、parameter_crc 和 capabilities。已实现result=accepted、protocol_version=1.0和合法能力列表校验，必需status_snapshot/command；拒绝或不兼容握手不进入online。能力仍须覆盖请求操作，TCP建立不等于设备可控制。

## 3. 请求、响应和副作用

| 消息 | 当前行为 | 目标及阻塞 |
|---|---|---|
| hello / hello_ack | 建连握手 | 校验结果、版本和类型；冻结能力名 |
| heartbeat / heartbeat_ack | 活性探测与超时重连 | 活性不替代新鲜有效的状态 |
| get_status / status_snapshot | request_correlation_v1按request_msg_id关联，旧协议串行等待 | 冻结请求关联字段；推送与响应不可混淆 |
| command / command_result | 按 payload.request_msg_id 关联 | 固定报文 ID、逻辑操作 ID、结果查询和板端去重语义 |
| get_parameters / parameters_snapshot | 按已协商请求关联回读，旧协议保留串行兼容 | 回读须来自匹配请求并核对参数及版本 |
| event | 推送事件，报警落库 | 事件码、序号、重连活跃报警和缺口恢复 |
| log_request / log_chunk / log_result | 需求存在，基线未接入 | Q7 冻结格式、序号、校验和补传 |

`command_result=accepted` 只能表示设备受理；实际完成由明确终态或可关联结果确认。结果丢失时记录 unknown，不能把 HTTP 超时写成“设备未执行”。上位机现已持久化operation_id和固定请求msg_id，发送前提交记录；同ID重试返回原结果，超时/重启不确定记unknown。真实板端去重窗口、跨重启查询未冻结，有副作用请求不自动重发。

诊断命令维持只读 get_status/get_parameters。所有控制请求走认证、权限、后台状态、二次确认（适用时）和操作审计。

## 4. 状态、时间、质量与队列

规范状态位置为 `payload.state_machine.current_state`；通过统一缓存读取；旧system.current_state兼容时若双字段冲突则返回未知。前端阶段别名只决定显示，不能放行操作。完整枚举、End 与冷却完成的区别、故障复位条件及实验 ID 生命周期由 Q1 冻结。

快照分组仍为 system、state_machine、temperature、gas、measurement、io、safety、alarm、log。关键量包括炉温 PV/SV、料层温度、位移、压差、滴落重量、N2/CO 设定与实测流量及其质量。要求每个采样字段明确单位、无效值、更新周期和校准引用，不能将无效标志丢弃后参与报告。

保存设备原始时间和 RTC 质量、后台接收 UTC 时间、接收单调时间、设备重启标识及序号。旧记录不能由接收时间补造设备时间；序号用于识别重复、乱序和缺口，不能只按秒精度时间去重。

现已使用有界通信回调、持久化和WS处理队列；遥测允许按明确策略合并最新显示值，但原始采样丢失必须标记。报警/事件与命令回执走独立优先路径；超载应暴露容量、队列年龄、丢弃计数和持久化错误，不能静默丢弃后报告正常。

## 5. 参数、配方与兼容

基线参数分组为 process、mfc、temp_program、ai_calib；字段值见 Mock，真实量程和含义以 Q5 冻结表为准。尤其不能把工艺 N2 5 L/min 保护与结束后 N2 2 L/min 置换混为一个默认值。

当前上位机 CRC 使用排序 JSON 的 CRC32，参数所有写入口复用回读事务；它不证明供应商使用相同字节序列。Q4 必须给出位宽、多项式、初值、反射、最终异或、字符编码、浮点规范化、覆盖范围及双方黄金向量。回读的设备 CRC、值和参数版本均需明确核对。

标准/非标可执行配方、操作结果查询、明确实验终态、遥测序号、设备日志导出通过协商能力逐项启用。新增可选字段可兼容旧客户端；改名、单位变化和语义变化使用新版本。旧固件缺少能力时禁用对应功能并显示原因，不能将主机模拟当成板端已经部署。

## 6. 已实现候选扩展

配方、测定/安全终态、有效首滴和设备采样序号已在软件及可选Mock中实现。完整字段、单位、能力缺失处理和持久证据集中见[实验扩展候选契约](hostcomm-experiment-extensions.md)及[ADR-006](decisions/ADR-006-candidate-experiment-contracts.md)。REST操作/控制权/维护/元数据API见[系统接口](上位机软件开发规格说明书.md)。不向真实固件擅自假定存在字段；Mock的安全范围与首滴生成不能反向证明固件正确。

## 7. 冻结与验收交付

冻结交付包括版本化 JSON/CSV 数据字典、状态转换表、消息样本、CRC 黄金向量、错误/事件码、能力清单、日志导出样本和固件校验值。每次契约变更同步客户端、模拟器、故障测试与兼容表。

验收至少覆盖拒绝握手、版本不符、粘包拆包、超长/非法帧、断线、回执丢失、命令重复、参数回读冲突、设备重启、事件丢失、背压和完整实验终态。软件与 Mock 通过仅证明仓库约定自洽；真实固件对齐另列证据，见[验证基线](verification.md)。
