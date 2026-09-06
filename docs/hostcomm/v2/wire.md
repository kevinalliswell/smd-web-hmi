# HostComm 2.0 报文、连接与事务

适用 `2.0-design.1`；实现状态与入口见 [README](README.md)。本文定义目标行为，当前 1.0 客户端和 Mock 不自动满足它。字段名称、必填项、枚举和消息样例以[机器契约](../../../contracts/hostcomm/v2/README.md)一并校验。

## 1. 传输与身份

STM32 作为 TCP 服务端，唯一上位机后台为客户端；默认端口 `34211`，现场配置可更改。2.0 生产端口只接收 TLS，不进行明文探测或自动降级。首期至少支持一个控制连接和一个诊断连接，数量有界；浏览器全部复用后台连接。

本设计选用 TLS 1.3 外部 PSK 配对：`TLS_AES_128_GCM_SHA256`、`psk_dhe_ke`、`secp256r1`，禁用 0-RTT、首次基线不启用会话恢复。双方通过标准 TLS 库认证和加密，不自行实现密码算法或以 JSON 中的 controller_id 代替身份认证。这是项目选型，需在指定 MCU、TLS 库与 Windows Python 3.13 运行时证明互操作；[Python PSK API](https://docs.python.org/3.13/library/ssl.html#ssl.SSLContext.set_psk_client_callback)仅说明可用接口，不代表当前程序已配置它。

每一对“板卡—后台实例”离线配置独立的 32 字节随机 PSK；不得使用操作员密码、STM32 UID 派生值或全设备共享密钥。非空 PSK identity 使用 `smd2/<device_id>/<controller_id>/<controller_epoch>`，三个 ID 均为 32 位小写十六进制，绑定固定客户端/服务端角色。设备只信任已配对的 identity；host 的 hello 身份必须与 TLS 身份一致。密钥通过受控本地工装/维护流程写入，不通过未认证 HostComm 获取；Windows 仅服务身份和管理员可读。日志、报告、示例和仓库不保存密钥。

配对清单包含身份、授权角色、版本及撤销状态。角色只有 control 和 diagnostic：control 可申请租约、提交普通命令及无需租约的 stop_run；diagnostic 仅可读取/心跳，不能上传或停止。hello_ack.granted_role 由配对清单决定，不接受客户端自授角色。轮换时设备待机、吊销旧配对、清理旧租约并确认新配对；旧 controller_epoch 不得重新启用。恢复数据库不能降低设备的操作防重放水位。配对凭据遗失必须本地维护重新配对，不能提供网络后门。以上选择依据 [TLS 1.3](https://www.rfc-editor.org/rfc/rfc8446) 和[外部 PSK 使用指南](https://www.rfc-editor.org/rfc/rfc9257)，现场网络隔离作为额外控制。

只有显式测试配置、隔离网络、未接危险执行器的参考测试可使用明文传输，且必须标记 test 模式；生产固件不能由网络请求切换到它。TLS 不可用、未知配对或版本不兼容时保持诊断失败和板端自主安全功能，不能退回 1.0 取得控制权。

## 2. 帧与数值

每帧是一行 UTF-8 JSON 对象，末尾恰好一个 `0x0A`。不含 BOM；发送方不发空行、CRLF 或非对象。TCP 拆包/粘包不具有消息语义。

| 限制 | 设计值 / 处理 |
|---|---|
| 单帧 JSON 长度 | 最多 8192 字节，不含 LF；发送和接收双方都检查 |
| 最大嵌套 | 16 层；禁止依靠无限递归解析不可信输入 |
| 单次配方内容 | 最多 65536 字节规范 UTF-8，不含尾 LF |
| 分块数据 | 每块最多 1536 个原始字节，RFC 4648 标准带填充 Base64，无换行 |
| 超长行 | 丢弃该行直到 LF；不得清空缓存后把同一行尾部当作新指令；记录计数 |
| 非法帧 | 不执行；成功握手后能安全识别请求时返回 error，否则计数并丢弃；连续 3 帧非法关闭连接 |
| 不完整帧 | 从首字节开始 5 秒仍无 LF 关闭连接；不能被不断输入字节无限延长 |
| 未知消息/字段 | 本设计严格拒绝，不能默默忽略控制参数；新增字段随修订与协商更新 |

JSON 禁止重复键、NaN/Infinity、浮点、小数/指数词法、`-0`、非法 UTF-8 和未配对代理项；布尔值不能当整数。数值限于 `[-9007199254740991,9007199254740991]` 的整数，具体物理量另受 Schema/工程配置限制。uint64 用无正号、无前导零的十进制字符串，范围 0 到 18446744073709551615。所有对象键为 ASCII；文本不进行隐式 Unicode 归一化。收到合法 JSON 仍须通过消息 Schema 和执行时条件检查。

## 3. 包络、关联与握手

包络必含 `protocol_version`、`msg_id`、`reply_to`、`session_id`、`boot_id`、`timestamp`、`uptime_ms`、`type`、`payload`。UUID 均为 32 位小写十六进制完整 UUID，不能截断。`msg_id` 每次发送新建，`operation_id` 标识持久业务操作，二者不混用。

`timestamp` 为 UTC 毫秒格式 `YYYY-MM-DDTHH:MM:SS.sssZ`，时钟未同步时为 null；`uptime_ms` 为发送方本次启动的单调毫秒计数。主机另存接收 UTC/单调时间。时间校正不能改变阶段计时或采样顺序。报文中的时间不是消息过期许可；板端自行校验租约和安全测量的新鲜度。

hello 的 session_id、boot_id 和 reply_to 为 null，uptime_ms 固定为 "0"（握手哨兵值）；hello_ack 产生新的 session_id，提供本次板端 boot_id 并通过 reply_to 指向 hello。此后双向帧必须使用该 session_id 和板端 boot_id；重连或重启后旧会话不能写入。请求/自主遥测/event 的 reply_to 为 null；所有直接响应必须指向本连接尚未完成的具体请求，不能仅匹配 type。operation_snapshot 仅响应 get_operation，异步进展用 event 提醒上位机查询。

流程：TLS → hello/hello_ack → 核对 device_id、精确协议版本、设计修订、身份与能力 → get_profile/get_status/get_operation/log 读取资源并对账 → acquire_lease → 允许满足条件的控制。设备接受 hello 不等于实验可启动。基线能力不齐时拒绝握手；能力完整但工程未就绪时只进入诊断范围，不得猜测旧字段。hello_ack 成功前的身份、版本或非法 hello 失败直接关闭连接（TLS 层可返回标准 alert），不伪造带未协商 session_id 的 JSON error；主机记录握手失败且不降级。

本版基线能力必须成组实现：操作查询与持久序号、租约、配方分块原子激活、明确运行边界、质量/首滴事件和日志补传。能力字符串固定为 durable_operations、atomic_recipe、sample_log、alarm_log，必须各出现一次；租约、运行边界和质量属于 2.0 基础语义。未经实现不能宣告。可选新增能力先分配新名字与设计修订，本修订对未知能力严格拒绝，不能视为已经启用。

## 4. 心跳、租约与队列

- 心跳周期 2000 ms，每次请求独立关联；每个 hello/普通响应默认等待 3000 ms，TLS/连接建立限时 5000 ms。超时是“未收到结果”，不能推导动作失败。
- 写租约持续 8000 ms，基于板端单调时钟；只有持有当前租约的已认证会话在 heartbeat 携带匹配 lease_id 才续期，heartbeat_ack 回读板端单调时钟下的 lease_expires_uptime_ms，主机结合响应时钟计算剩余有效期。无关消息、诊断读取和错误心跳不能续期。
- heartbeat 的 lease_id 为 null 时仅探测连通性，不续期；heartbeat_ack 可返回板端当前租约信息，主机不能据此取得或替换租约。申请租约的回执归档及所有权回读可能与这种心跳并行。请求携带非空 lease_id 时，ACK 必须确认同一租约；不同标识或 null 均不能作为有效续期，主机关闭连接并撤销控制就绪。
- TCP 断开立即撤销该连接的租约；检测不到的半开连接在租约到期时撤销。运行中进入批准的安全处置，重连不取消停止。8000 ms 是网络租约界限，不是 CO/急停的硬件响应指标。
- acquire_lease 只在没有有效租约且对账条件允许时授予新的不可复用标识。不能网络强占；旧连接释放、断开或过期后新会话再申请。状态中的 owner 与服务端身份一致。
- `state_revision` 仅在会影响控制许可的状态、配置、报警/联锁、租约授予/撤销/所有权或运行身份改变时递增，不因续期或每个温度样本变化而递增；任何温度条件仍在执行点重新检查。
- 上位机同一控制身份最多一个普通副作用操作在受理等待中，未知结果未对账前停止普通写请求；stop_run 使用独立安全通道。只读在途请求最多 4 个，上传事务和日志传输各最多 1 个/会话。
- 板端通信任务不得执行阻塞的仪表 I/O、擦 Flash 或配方循环。优先调度本地安全、远程安全停止、命令结果/心跳、报警、实时遥测、历史日志；普通队列满返回 busy，不能阻塞安全入口。采样日志丢失必须可见，显示合并不能删除原始记录。
- 重连按 1、2、4、8、16、30 秒上限退避，加入抖动；重连后先对账，不自动重发 start/activate 等副作用请求。

## 5. 消息清单

| 请求/推送 | 响应 | 用途 |
|---|---|---|
| hello | hello_ack | 协议、身份、设计修订、能力和资源 |
| heartbeat | heartbeat_ack | 连通性与当前写租约续期 |
| get_status | status_snapshot | 已存在的最新采样、运行、安全和租约状态；不分配新样本 |
| get_alarms | alarms_snapshot | 按固定报警修订分页恢复活跃及待确认报警 |
| get_profile | profile_snapshot | 只读、带摘要的工程配置与设备能力 |
| command | command_result | 有持久身份的控制、租约和配方激活请求 |
| get_operation | operation_snapshot | 查询包括重启后的受理、应用、拒绝或未知结果 |
| recipe_begin / recipe_chunk | recipe_transfer_result | 创建暂存事务、顺序写入和确认字节偏移 |
| get_recipe | recipe_snapshot | 活动配方身份与分块原始内容回读 |
| telemetry | 无 | 采样任务产生的原始样本 |
| event | 无 | 原始报警、首滴、阶段、运行和操作状态变化；可靠性由日志补传保证 |
| log_request | log_chunk / log_result | 固定范围的历史记录流 |
| log_ack | 后续 log_chunk / log_result | 主机持久化后的传输窗口推进 |
| 任意可关联非法请求 | error | 一致的机器错误码与可读说明，不代替已存在操作结果 |

## 6. 操作、去重与结果

command.payload 包含 `operation_id`、`controller_epoch`、`command_seq`、`lease_id`、`expected_boot_id`、`expected_state_revision`、`request_digest` 和受限命令参数。request_digest 对去掉自身字段后的完整规范 payload 计算 SHA-256，包含 lease、预期版本和参数。TLS 配对绑定 controller_epoch，不能由请求临时声明一个新 epoch 绕过防重放。

每个 controller_epoch 的 command_seq 严格递增，由主机持久分配；设备持久化最高已消费序号及保留的操作结果。主机备份恢复后先读取水位，再从大于两端已知最大值的位置分配，不可回退。序号耗尽时进入本地维护重新配对，不回绕。

处理顺序：认证与会话核对 → 严格报文/摘要校验 → 查询既有身份 → 执行许可校验 → 原子持久化序号水位和受理/拒绝记录 → 有界任务队列 → 执行动作与持久结果。普通排队任务在实际应用点重新检查停止锁存、租约、状态和实时安全条件；排队期间条件变化必须中断，不执行过期决定。合法、可识别操作的业务拒绝也消费序号；无法验证身份/结构的请求不消费水位。意图写失败时不执行普通命令。stop_run 的存储故障例外见[状态与恢复](state-and-recovery.md)。

| 再次收到请求 | 必须的处理 |
|---|---|
| 相同 epoch/seq/operation_id/摘要，结果仍在 | 返回原记录；不能重新执行 |
| 相同身份但内容、序号或摘要冲突 | operation_conflict；不能覆盖旧记录 |
| seq 不高于水位且明细已回收 | result_expired；永不再次执行 |
| 大于水位的新操作 | 满足当前许可和存储条件才受理 |
| 进程/板端重启后执行结果不确定 | unknown 或可证明的 interrupted；不自动重做 |

最低保留 128 条操作结果，并固定保留 accepted/仍在执行的操作及未确认运行关联的结果；不得为新操作回收它们。资源不足拒绝普通操作并保留安全停止能力。安全对账可以完成而历史动作结果仍保持 unknown：确认当前运行身份、安全状态、停止锁存、活动配置和日志依据，并由操作者有审计地完成恢复后，可用新序号执行 reset_fault/ack_run 等允许的恢复请求，不得为了放行而虚构旧结果。完成安全对账且不再执行、也不再关联未确认运行的历史 unknown 可按普通历史结果保留/回收，上位机保留原 unknown 和对账依据，水位永不回退。结果缓存容量不是防重放窗口，永久高水位在结果回收后继续有效。防重放的权威身份是 controller_epoch/command_seq，operation_id 在保留记录中必须与它一一绑定，主机永久不得复用 operation_id。结果回收后的保证是“旧序号绝不执行”，不声称有限缓存还能识别被恶意改写为新序号的任意旧 UUID。设备每次运行以首次 start 的身份建立，旧请求不能仅通过更换 msg_id 再执行。

| 操作状态 | 解释 |
|---|---|
| accepted | 意图与防重放水位已持久化，已排入受限任务；不代表执行器完成 |
| applied | 该命令的明确提交点已完成，见下表；不代表实验完成 |
| rejected | 可证明未受理该新动作，有明确机器原因 |
| interrupted | 曾受理，后续执行被已知事件中断；不能解释为没有副作用 |
| unknown | 无法证明最终动作结果，必须读取运行/配置/日志对账 |
| result_expired | 已消费的历史操作明细不再保留，绝不允许重新执行 |
| not_found | 当前保留记录中不存在该身份；主机还需核对水位/epoch，不能据此重发 |

start_run 的 accepted 提交必须同时保留 run_id、配方/工程配置绑定，并发布 preparing 状态；在流程任务应用前不打开危险输出。stop_run 可命中这个准备中的运行，先锁存停止并阻止队列中的启动继续执行。启动排队或执行失败保留运行身份与 interrupted 证据，进入适当处置，不能撤销预约后让停止落空。

命令名称及参数是封闭集合：

| 命令 | applied 的提交点 / 主要前置条件 |
|---|---|
| acquire_lease | 新租约已绑定当前会话；无有效他方租约；lease_id/expected_state_revision 为 null |
| release_lease | 当前租约撤销；运行中释放将锁存安全处置 |
| start_run | 已预约运行的预检请求被实时任务接受；应用点重新检查停止/租约等许可，仍处 preparing，预检尚可失败 |
| stop_run | 对明确活动 run_id 锁存单向安全处置；lease_id/expected_state_revision 必须为 null，仍核对 boot_id；绝不重置置换计时 |
| activate_recipe | 校验完整暂存内容后原子切换活动配方及配置版本；仅无活动运行、工程配置批准且预期版本一致 |
| ack_run | 安全终态及恢复条件满足，保留运行记录并转 idle；不删除数据或解除活跃联锁 |
| ack_alarm | 记录该次报警 occurrence 的确认；不清除物理条件、不启动实验 |
| reset_fault | 批准恢复条件满足且活跃故障原因消失；不能恢复原实验或加热/CO |

首期没有任意 set_parameters、直接写 I/O、远程工程标定、远程维护进出、自由脚本或远程固件刷写命令；这些能力若需要必须另行设计受控接口。工程配置通过本地维护工装交付，不能借配方上传修改。

## 7. 配方分块与原子激活

recipe_begin 声明 transfer_id、当前 lease_id、总字节长度和整体 SHA-256；每个块仍在执行点核对当前会话拥有此租约，租约失效立即撤销暂存。最多一个会话绑定的暂存事务，120 秒无有效块进展过期。每块按确认偏移顺序写，重发相同偏移/字节只返回当前 next_offset；不同内容重叠拒绝并终止暂存。禁止越界、稀疏写和未经长度校验的分配。单块必须完全解码并校验后才能推进偏移。

最后一块仅代表内容收齐，不代表活动配方改变。activate_recipe 必须引用完整 transfer_id/摘要和预期活动配置版本，检查全部字节摘要、规范 JSON、Schema、阶段可执行性和工程安全配置，再通过双槽或等价事务一次性提交活动身份及操作结果。内容解析、传输错误或断电必须保留原活动配方。暂存允许丢失；新会话需重新上传，不继承未验证的上传会话。

主机通过 get_recipe 分块读取活动原始规范字节，对全件重新计算 SHA-256，核对 recipe/version、工程配置与活动版本；只有一致才显示部署完成。start_run 再次携带预期配方/安全配置摘要，设备在同一个启动提交点比较并绑定不可变快照，避免回读后被并发替换。

## 8. 原始日志与补传

设备在持久日志内按 record_seq 为采样和事件分配统一位置，顺序号跨 TCP 和 boot 延续；日志存储世代变更必须使用新的日志身份，不能把清空后的序号复用成旧记录。每条记录保留原 boot_id、run_id、sample_seq 或 event_seq，以及源时间/质量。日志是不可变规范 JSON Lines 字节；CRC 可作存储内部检错，线上块统一 SHA-256，不沿用 1.0 参数 CRC。

log_request 指定日志身份、起点、明确终点与限额；先通过 status.log.newest_record_seq 取得最高已提交记录再请求，服务端锁定这一 cut，不把传输中新样本追加进已有响应范围。公布可读最早/最新位置、实际选择范围及缺失区间。请求闭区间长度必须不大于 max_records（即使其中有缺口），每次最多 10000 条、16 MiB，超过请求限额时拒绝并要求缩小范围，不能无标记截断；最多 32 个有序不重叠缺口，更多则返回 range_unavailable 并要求拆分请求。未知日志身份或无法提供可信 highwater 时返回 error，不能制造一个高水位来填充 log_result。越界、已淘汰、掉电丢失和介质损坏必须明确返回，不能伪造空的“完整成功”。

每个 log_chunk/log_result 的 reply_to 均指向最初 log_request；log_ack 是该传输的窗口确认，reply_to=null，不创建另一个读取请求。log_chunk 传输至多 1536 原始字节，Base64 与 SHA-256 校验。允许一条记录跨块，但必须按 byte offset 重组；只有完整合法记录才可提交。传输按一个未确认块的窗口推进，收到匹配 transfer_id、字节偏移及持久记录水位的 log_ack 才发下一块。3000 ms 未确认可重发同一块，最多 3 次后关闭该传输连接，保持未完成，不生成伪造源数据缺失的 log_result；这类只读重传不能套用于控制命令。后续 log_result 必须给出明确终态、缺失范围、整流 SHA-256 与实际结束位置。返回记录数加各缺失区间长度必须恰好等于请求闭区间长度；complete 全量、partial 至少一条记录和至少一个源缺口、unavailable 没有记录且缺失覆盖整个请求。主机核对实际重组记录的 ID/顺序/数量和整流摘要，不能只信任终态计数。

主机校验每块摘要后，先持久化已收原始字节及 next_offset，再 ACK；跨块的记录尾部先写入有界、可恢复的暂存区，允许确认字节进展，但 committed_record_seq 保持上一条完整、合法且已落盘的记录位置（首次可为 null）。这样一条大于 1536 字节的记录不会与单块窗口互相等待。确认丢失后以同一源日志身份/record_seq 去重；字节接收位置、最高完整记录位置和无缺口完整性水位分别保存，不能用字节 ACK 越过尚未判明的源记录缺口。相同身份不同字节是完整性故障，不覆盖。解析未完成的尾部不推进记录游标；断连后可从上一完整记录重新请求。主机不得将历史消息喂入实时状态/命令模块，回填仅用于归档与重新判定。

ACK 只是接收持久化证明，不直接授权永久擦除全部历史。固件按批准的容量/保留策略回收已经确认、非活动运行、已完成归档保留要求的记录；临界水位和缺口持久上报。操作日志/运行边界必须有预留空间，不能被遥测填满。完整离线保存时长由实际采样率、最坏记录长度和可用持久空间计算；未具备容量时拒绝对应记录预算的启动，不能声称支持任意时长离线实验。

## 9. 错误与实现验证

错误由固定小写 snake_case code 和供人阅读的说明组成；程序判断只依赖 code。至少区分 invalid_frame、unsupported_version、unsupported_capability、identity_mismatch、permission_denied、stale_session、boot_mismatch、lease_required、state_conflict、busy、operation_conflict、result_expired、profile_unapproved、profile_mismatch、recipe_invalid、digest_mismatch、offset_mismatch、storage_unavailable、range_unavailable 和 internal_error。实际可发送集合以 Schema 为准；retryable 仅表示前置条件纠正后可重新评估，不授权自动重发有副作用命令或把 unknown 改成失败。不把裸异常、密钥或任意底层寄存器信息写入返回值。

参考检查覆盖字段/编码/摘要与所列边界；TLS、租约计时、持久化原子性、队列公平性、停机动作和断点补传状态机必须分别实现并故障注入。收发日志记录消息 ID、操作 ID、结果与统计，不记录 PSK。验收至少包含分字节拆包、粘包、超长行尾伪指令、损坏 UTF-8、重复键、重复命令、丢 ACK、缓存淘汰重放、跨重启旧租约、配方断电和补传损坏；详见[实施清单](firmware-plan.md)。
