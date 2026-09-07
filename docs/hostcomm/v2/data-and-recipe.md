# HostComm 2.0 数据、配方与工程配置

文档修订：`2.0-doc.3`；设计基线：`2.0-design.1`；协议：`2.0`。本次不改变握手设计版本。[Schema 与固定向量](../../../contracts/hostcomm/v2/README.md)定义字段形状，[状态契约](state-and-recovery.md)定义时序。下列数值倍率是协议编码选择，不是传感器精度声明，也不是设备安全量程。

## 1. 数据字典与时间

每个采样拥有 `{boot_id, sample_seq}` 源身份，sample_seq 由采样任务连续分配；查询和重传不能产生新身份。sample_uptime_ms 是本次板端启动的单调采样时刻，sample_timestamp 是可空 UTC 毫秒时间。相同样本再次传输必须保留原时刻和值，不能用发送时刻刷新旧数据。

| 物理量 | 编码单位 | 转换例子 / 使用对象 |
|---|---|---|
| 炉温 PV、炉温目标、料层温度 | m℃，摄氏度的 1/1000 | 600 ℃ → 600000；炉温和料温独立通道，不能互换 |
| 升温速率 | m℃/min | 10 ℃/min → 10000；仅为配方速率表示 |
| 压差 | Pa | 保留有符号原始差值，不能把负噪声静默截成零 |
| 位移、料层高度 | μm | 1 mm → 1000；工程配置规定方向、零点和标定 |
| 滴落累计质量 | mg | 1 g → 1000；不能用质量阈值代替首滴有效事件 |
| N₂ / CO 设定与反馈流量 | mL/min | 5 L/min → 5000；使用工程配置指定的参考温度/绝压 |
| 阶段时间、通道数据年龄、稳定时间 | ms | 1 s → 1000；阶段计时使用单调时钟 |
| 气体参考温度 | mK | 绝对温度，不与 m℃ 混淆 |
| 气体参考压力 | Pa，绝压 | 不能使用未注明的表压或当地大气压替代 |

Point 由可空 value、quality、age_ms 构成；quality 为 good、invalid、stale、unavailable、uncalibrated 之一。good 必须有合法数值且数据年龄不超过该通道批准的 freshness 限制；其他质量不能用于放行条件、首滴判定或安全完成。无法测量时 value=null，若保留最后已知数值必须明确 stale 及真实 age_ms。不能将断线、断偶或超量程编码为可用的 0。

对每个仪表/MFC，工程映射表至少记录：通道名、物理来源、现场总线/地址、原始单位/比例、量程、分辨率、符号方向、采样周期、超时、有效性/故障码、标定版本和气体参考条件。协议固定点整数允许精确传输编码值；不要求硬件具有 0.001 ℃精度。HMI 显示修约不改变原始数据。

时间质量与点质量独立：没有 UTC 同步时仍可按单调时钟控制和排序，但保留 timestamp=null。禁止上位机或板端为缺失的源 UTC 填入接收 UTC。超时、升温、保温和租约均不使用可能跳变的日历时钟。

### 当前主机的数据新鲜度检查

主机同时校验 session_id、boot_id、run_id 与 state_revision；遥测不能早于状态引用的 latest_sample。通道有效年龄按 `Point.age_ms + (包络 uptime_ms - sample_uptime_ms) + 主机接收后经过的单调时间` 计算，再与 profile.resources.channel_freshness_ms 比较。因此不能重发旧值时重置采样时刻或年龄。当前状态快照的主机新鲜度窗口为 5 秒，与通道时限分别判断；它是主机接入策略，不是固件安全动作时限。

源 Point、源 UTC/null 与源身份原样归档；为兼容旧表，SamplePoint.ts 在源 UTC 缺失时使用接收时间，并在 ext_json.v2.timestamp_basis 标记 received_at。这不是补造源 UTC，报告应读保留的源时间/序号。实现见[状态投影](../../../backend/app/hostcomm/v2_projection.py)、[归档投影](../../../backend/app/services/v2_archive.py)；对应[投影测试](../../../backend/tests/test_hostcomm_v2_projection.py)和[归档测试](../../../backend/tests/test_v2_archive.py)。

## 2. 运行边界、首滴和报警

状态快照中的运行记录保存 run_id、配方/工程配置摘要、阶段编号、outcome、measurement_complete、safe_complete 和起止源样本引用。所有跨重启边界都保存其原 boot_id；当前快照的 boot_id 不覆盖它们。measurement_complete 仅表示自然测定结束；人为停止也有实际截止样本，但不能设置为自然有效完成。

首滴事件必须给出发生时的 event_seq、run_id、源样本引用、event_uptime_ms/event_timestamp，以及当时料层温度和 is_valid。无效检测也保留原始事件；只有 is_valid=true、料温 good 且满足工程对齐规则时才可产生有效 Td。检测器边沿与温度采样若不同时，工程配置必须规定最大对齐偏差和采用规则；超差时事件保留但不得产生有效 Td。重连后看到 first_drip=true 只能证明曾锁存，不能采用当前料温。

每个报警 occurrence 有独立 alarm_id/occurrence_seq 身份，并通过原始 boot_id/event_seq 定位发生事件；报警码标识原因，不能充当发生次数身份。发生、条件恢复、确认和复位分别追加事件。raised 必须 active=true/acknowledged=false；cleared 必须 active=false，保留此前确认状态；acknowledged 必须 acknowledged=true，允许在条件恢复之前或之后确认，不能借确认改变 active。active 条件恢复不抹掉发生记录；ACK 只记录人类确认，不解除硬接线/软件联锁。get_alarms/alarms_snapshot 按固定 active_alarm_revision 分页（单页最多 16 项且完整报文不得超过 8192 字节，装不下时返回更少项目并推进实际 next_offset）；读取期间修订改变返回 state_conflict，主机重新开始，不能混合两版页。状态快照、报警分页与日志重放共同恢复断线期间的报警，不假定一帧能装下所有历史事件。

报警分页视图以当前 boot_id 加 revision 定位，不能跨启动比较裸 revision。发生身份仍保留原 raised_boot_id/raised_event_seq；run_id=null 的全局报警不能丢弃。当前主机把 trip/warning/info 映射为页面 3/2/0 级，页面数字 ID 仅是本地路由，板端确认仍使用 alarm_id/occurrence_seq。条件已恢复但未确认的历史 occurrence 仍会阻止 ack_run；必须显式确认，不能在快照中静默删掉它。对应[报警归档实现](../../../backend/app/services/v2_alarm_archive.py)、[归档回归](../../../backend/tests/test_v2_archive.py)及[完整应用历史确认用例](../../../backend/tests/test_v2_application.py)。

本版核心报警码如下，Schema 拒绝表外报警码。warning 可按批准工程配置提升为 trip，但不能降低最低级别；trip 表示必须触发对应的启动禁止/安全监督，具体硬切和置换时序由工程配置决定，不能等网络 ACK。

| code | 最低级别 | 条件与后果 |
|---|---|---|
| emergency_stop | trip | 急停输入有效；硬接线优先，安全任务保持锁存 |
| co_leak | trip | CO 检测报警；按工程气路/排风设计处置 |
| exhaust_lost | trip | 必需排风反馈失效；撤 CO 许可并安全处置 |
| furnace_overtemperature / burden_overtemperature | trip | 相应温度对象超过工程上限；不得混用两个测温对象 |
| temperature_sensor_invalid | trip | 控制或保护温度无效/过期；不能依赖旧温度继续放行 |
| measurement_sensor_invalid | warning | 非控制测量无效，保留质量并降低测定有效性；工程要求关键时提升 trip |
| n2_mfc_fault / co_mfc_fault | trip | 对应 MFC 通信/设定/反馈不满足批准条件 |
| gas_supply_lost | trip | 必需气源不足或反馈失效；按工程配置处置 |
| actuator_feedback_fault | trip | 阀门、加热许可等要求的反馈与期望不一致 |
| lease_lost | trip | 控制租约失效；测定未自然结束时 outcome=aborted，不由报警级别改成自然完成 |
| storage_unavailable | trip | 关键持久化介质故障；新普通操作拒绝，安全停止仍可执行 |
| log_capacity_low | warning | 到达预警水位，禁止不足预算的新实验，保留安全预留空间 |
| log_capacity_exhausted | trip | 实际记录容量不能继续保证；安全处置并标明缺失范围 |
| recipe_stage_timeout | trip | 阶段到期未满足条件；不得作为正常阶段完成 |
| clock_unsynced | warning | UTC 不可信，timestamp=null；单调控制仍独立有效 |
| engineering_profile_invalid | trip | 工程配置缺失、未批准、损坏或与实装不符；禁止启动 |

设备核心原因码由契约定义；仪表厂商寄存器码通过工程映射表转换为核心原因及诊断 detail，不可创造具有控制语义的任意字符串。各安全输入的正常/故障极性、去抖、恢复条件和响应时限属于批准工程配置，不由 HMI 编辑。

## 3. 规范配方字节

配方摘要计算对象是完整线模型 Recipe（schema_version=2）的规范 UTF-8 字节，不包括网络包络、Base64、transfer_id、分块边界或尾 LF，也不是旧页面 RecipeDefinition 的摘要。算法标识为本版的整数规范 JSON，遵循 [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) 的以下受限子集：

1. 所有字段显式出现；不可用的可空字段写 null，不依靠某种语言补默认值。对象键限 ASCII，按键的字节升序排列；数组保持阶段顺序。
2. 整数使用最短十进制，禁止 -0、小数和指数，限定 JSON 安全整数范围。布尔、null 使用 JSON 小写字面量。
3. 对象/数组分隔符无空白；字符串使用 UTF-8。双引号、反斜线及控制字符按 JSON 规范转义，不能随意把中文转成另一组转义字节。禁止非法 Unicode，不做 NFC/NFD 转换。
4. 对完整字节作 SHA-256，以 64 位小写十六进制表示。C 端可以边上传边计算 exact bytes SHA，但激活前还必须验证完整内容确实为规范字节及合法配方；摘要相同本身不是安全批准。

规范样例、原始字节和固定 expected digest 随机器契约提交。检查工具重算并比对，不在每次运行时重新生成期望摘要。旧配方浮点转换必须精确可表示：例如 0.0005 ℃ 无法表达为整数 m℃时拒绝并要求创建明确修约的新版本，不能暗中改变已批准值。

### 上传、激活与回读的交接顺序

当前主机仅允许在无运行身份的 idle 激活完整配方；工程配置只读，通用参数入口不能下发零散气体/加热字段。主机保留已保存页面配方的 source_digest，另保存编译后的 wire recipe_digest 及绑定，旧记录不被覆盖。

1. 主机从已认证设备读取批准的 profile，校验投影摘要，编译并校验完整配方；不能采用 HTTP 请求自行携带的工程配置作为批准依据。
2. 获取写入租约，以 recipe_begin 声明 transfer_id、recipe_digest 和总长；recipe_chunk 按确认偏移上传。返回 receiving/validated 只表示暂存进展/校验，不改变 active_recipe_digest。
3. 主机发送持久操作 activate_recipe，携带 transfer_id、recipe_digest、expected_active_digest 及当前状态/租约前置条件。板端在提交点再次检查，只有原子切换成功才有新的活动配方。
4. 主机用 get_recipe 分块回读，核对每块的 recipe_digest、offset、稳定的 byte_length、完整规范字节及总摘要，再读取 status_snapshot 确认 active_recipe_digest。回读不符或活动摘要未确认不能显示部署成功。
5. 启动再次核对活动配方与本地保存绑定，固定 run_id、recipe_digest、safety_profile_digest。断线后的未知激活先查询原操作，不用另一个新命令覆盖不确定结果；新会话如需上传，按 [wire 上传规则](wire.md)重新建立暂存事务。

当前主机对 get_recipe 能读到但找不到本地版本绑定的配方保留 wire_recipe 诊断，不能据此虚构原页面配方或直接启动。接管其他控制器部署内容的导入流程尚未实现。实现见[配方编译器](../../../backend/app/services/v2_recipe_compiler.py)和 [V2Client](../../../backend/app/hostcomm/v2_client.py)；证据见[编译回归](../../../backend/tests/test_v2_recipe_compiler.py)、[原子激活与原版本回读测试](../../../backend/tests/test_v2_client.py)、[断线上传保留旧活动配方测试](../../../backend/tests/test_hostcomm_v2_simulator.py)。

## 4. 阶段配方

配方具有独立 recipe_id/version、schema_version=2、name、standard/custom 模式、rules_version、安全工程配置摘要及 1—64 个有序阶段。运行时只使用已原子激活的不可变内容；编辑产生新版本。修改标准模板必须生成 custom，并在 HMI 报告保留偏离项。

每个阶段都显式给出 kind、heater_mode、target_mc、rate_mc_per_min、N₂/CO 流量、exit 和 timeout_ms。kind 只允许 ramp/hold/gas/cool；不允许循环、跳转、脚本、用户变量或任意布尔表达式。所有阶段都有有限超时，不能用 0 表示无限等待。

| 字段/组合 | 执行语义 |
|---|---|
| heater_mode=off | 请求撤加热许可，target/rate 均为 null；不继承上一阶段目标 |
| heater_mode=ramp | target 与正 rate 必填；从阶段进入时已确认的当前炉温控制目标连续推进到 target，不跳变至终值；前置目标未知则拒绝执行 |
| heater_mode=hold | target 必填，rate=null；保持这个明确目标；不隐式继承 |
| kind=ramp | 必须使用 ramp 加热模式和温度退出条件；实际范围/斜率受工程配置限制 |
| kind=hold | 使用 hold 模式与 elapsed_ms 条件；计时语义见下文 |
| kind=gas | 仍须显式指定 off/ramp/hold 及其目标，不允许省略后继加热行为 |
| kind=cool | 只允许最后阶段；heater off、CO=0、N₂为批准的正流量，料温严格低于安全阈值后退出 |

进入阶段时先验证全部目标和执行许可，再一次性向实时流程任务提交整组期望值；各仪表物理响应不可能假定瞬时原子。设备确认所需反馈和保护前置条件后才能记为阶段有效执行；任一执行失败进入安全处置，不把部分成功伪装成阶段已达成。

exit 为单一信号 furnace_mc、burden_mc 或 elapsed_ms，加比较 gte/lt、阈值与 stable_ms。只使用 good 且新鲜的数据；温度条件须持续满足 stable_ms，任一次失效/越界重置稳定计时。elapsed_ms 只使用 gte，stable_ms 必须为 0，阈值必须小于 timeout_ms；温度稳定时间也必须小于阶段超时。温度到达与阶段超时在同一次调度发生时，超时优先，不能在截止点伪报成功。

计时从阶段整组目标被流程任务接受时开始，包含仪表达到设定的等待时间。需要“到温后保温 30 分钟”时拆成温度到达阶段和 elapsed_ms 保温阶段；不能把从下发时开始的计时标成到温保温。stable_ms 从首次连续满足条件时开始；超时从同一阶段起点计算，不因传感器异常或消息重传延长。

最后 cool 是用户可见的冷却请求，执行仍由不可编辑的安全子流程接管。进入该阶段前保存测定结束样本；先执行批准的撤 CO/置换，再冷却并判断 safe_complete。即使配方末阶段异常、传感器无效、用户停止或掉电，独立安全任务也必须继续可实施的处置。删除 cool、CO 非零、阈值大于 200000 m℃、使用 gte 退出冷却等配方必须拒绝。

CO 的准入和持续许可由板端每个控制周期重新检查，包括批准的温度对象/阈值、排风、气源、阀门/MFC 反馈、联锁与质量；不能仅根据上一阶段达到温度就永久许可，也不能允许配方降低安全门槛。失去条件执行批准的安全动作，不能等待网络命令。

## 5. 工程配置与资源

工程配置是带版本/摘要、approved 状态、批准依据的只读交付物；profile_snapshot 提供协议需要的可读投影。engineering_config_digest 标识完整受控工程文件（含通道标定、安全动作/反馈/时序等），profile_digest 对除自身字段外的完整规范投影计算 SHA-256，包括 engineering_config_digest；完整工程配置变化必须改变投影摘要及配方绑定。批准必须关联实际硬件/仪表/标定和安全规程，不是任意客户端可修改的布尔开关。

| 类别 | 必须确定的内容 | 未确定时的行为 |
|---|---|---|
| 温度/加热 | 炉/料温量程、斜率、故障值、最大目标、CO温度条件、超温保护 | 禁止启动；保留诊断 |
| 气体/排风 | MFC类型、参考温度/绝压、量程、精度、最小保护流量、反馈与失联动作 | 禁止含气体流程，不用 Mock 值补齐 |
| 安全处置 | 阀门失电位、撤 CO/热源、N₂置换条件/时间/量、排风、冷却门槛/稳定时间、反馈 | 配置未批准，不能进入实验 |
| 传感器 | 通道字典、标定、采样/过期、断线/超量程/极性、首滴事件及温度对齐 | 相应数据无效，不能用于安全或指标 |
| 持久化 | 可用介质、原子写机制、掉电检测、耐久性、日志代际、保留和预留空间 | 不声明掉电可恢复；生产启动条件不满足 |
| 资源 | TLS峰值RAM、任务栈、解析/队列上限、阶段数、采样率/周期、存储吞吐 | 先进行选型和压力验证，能力不能超报 |

本协议固定单帧/分块/配方上界，其余采样频率和离线保存时长来自具体设备能力，不凭板型名称推定。配置给出允许采样周期和各通道 freshness；运行中采样周期固定，调整需新的记录配置与批准。日志预算至少覆盖计划测定、最坏处置/冷却、事件和持久日志开销；容量核算采用最坏编码长度，不能仅以原始 ADC 字节计算。

存储容量不足时停止接纳新实验；运行中接近预留边界按批准策略安全处置并记录不能保存的区间。安全日志预留耗尽或介质故障时上报故障、保持 unknown，仍执行可实施的单向安全动作。现有[板卡资料](../../hardware/stm32h750vbt6-board.md)标明 W25Q128 外部 NOR 接 SPI2、24C02 EEPROM，未提供 SD 卡信息；实装后缀、分区、寿命及掉电行为仍待验证，不能由容量或 RAM 缓存推定已具备可靠持久化。

## 6. 与国标的关系

GB/T 34211—2017 的条款和争议以[实验规范与原文映射](../../experiment-spec.md)为真源。标准模板应逐项映射炉温升温/切气、H600、料温条件和低温结束；首次可执行标准模板必须具有批准的 rules_version 和设备 profile，不能以本文件的编码例子取代国标模板。

炉温与料温通道同时保留；H600、高度变化、首滴 Td、压差和重复性计算由上位机基于合格原始样本/事件、真实测定边界和版本化规则完成。首滴缺失、检测器无效、未完成和有效完成未滴落必须分开。首滴未发生不自动给 Td=1580 ℃；替代值仍受国标判定规则及完整性前提约束。

焦炭总量冲突和 ΔT 编号冲突仍由 Q11/Q12 处理。标准实验的 safe_complete、协议测试通过、配方 mode=standard 均不意味着已证明国标符合性；报告保留规则版本、原始依据、偏离项和未批准项。
