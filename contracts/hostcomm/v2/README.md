# HostComm 2.0 可执行设计契约

设计修订：`2.0-design.1`。本包是固件和上位机适配的共同输入，**不是已完成的 STM32 固件或现有 1.0 客户端驱动**。规范解释、职责和迁移顺序见[设计入口](../../../docs/hostcomm/v2/README.md)。示例工程值全部为未批准的合成测试数据，不能用于控制实际设备。

## 文件与运行

| 文件 | 作用 |
|---|---|
| [messages.py](../../../backend/app/hostcomm/v2_contract/messages.py) / [types.py](../../../backend/app/hostcomm/v2_contract/types.py) | 26 种消息的封闭字段集合、严格整数与跨字段约束 |
| [recipe.py](recipe.py) | 显式加热模式、固定点数值与末尾冷却的不可变配方模型 |
| [message.schema.json](message.schema.json) | 从模型生成的 JSON Schema Draft 2020-12，按 type 区分消息 |
| [recipe.schema.json](recipe.schema.json) | 规范配方字节所表达的结构 |
| [log-record.schema.json](log-record.schema.json) | 持久日志内的 sample/event 原始记录结构 |
| [vectors.json](vectors.json) | 冻结的规范 UTF-8 字节、SHA256、有效和非法输入 |
| [codec.py](../../../backend/app/hostcomm/v2_contract/codec.py) | 严格解析、规范序列化、有界帧解码、配方分块校验和日志重组参考实现 |

在仓库根目录、安装现有 backend 开发依赖的 Python 3.13 环境运行：

```sh
python scripts/check_hostcomm_contract.py
python -m pytest backend/tests/test_hostcomm_v2_contract.py -q
node contracts/hostcomm/v2/verify-js.mjs
```

检查器会拒绝 Schema 漂移、固定摘要变化、缺失消息向量、非法帧误受理和不完整日志伪装成功。修改模型后显式运行 `python scripts/check_hostcomm_contract.py --write-schemas`，将模型、生成文件、向量和解释文档一并审查。该选项只生成 Schema，**不会重写固定向量**。

Schema 无法表达全部约束。固件至少还要实现本包参考校验器中的 uint64 上界、字节长度、重复键、有限深度、规范转义/整数词法、摘要及跨字段关系。不能仅通过通用 JSON Schema 就宣告契约一致。

Node 24 检查器使用独立的内置实现核对规范字节和固定 SHA256，验证 Python/JavaScript 的表示一致性；STM32 C、TLS 与实际设备互操作仍须另行实现和验证。

## 关键字节规则

- 一帧最多 8192 字节 JSON 加一个 LF；深度最多 16。超长行持续丢弃至 LF，不能把尾部解释成另一个请求。
- 所有字段必填；允许空值的字段也必须显式为 null。未知字段和消息拒绝。包络的响应关联、session/boot 与命令 boot 前置条件分别校验。
- 工程量为固定点整数：m°C、m°C/min、mL/min、Pa、µm、mg、ms。具体工程范围由已批准 profile 与完整工程配置控制，本包不代替工程批准。
- uint64 是十进制字符串；普通整数限定 ±9007199254740991。拒绝浮点、小数、指数、负零、NaN/Infinity、重复键、非法 UTF-8 和未配对代理项。
- 规范 JSON 使用 RFC8785 的安全整数/ASCII 键子集：键排序、UTF-8、固定 JSON 转义、不保留空白、不做 Unicode 归一化。普通报文接收可接受合法空白；配方和日志必须是精确规范字节。
- `recipe_digest` 对整件规范配方字节计算 SHA256，不含 LF。`request_digest` 对完整 command payload 去掉自身后计算，包含 lease 与 state 前置条件。`profile_digest` 对完整 profile payload 去掉自身后计算，包含 `engineering_config_digest`。

## 传输与记录形状

配方最多 65536 字节，每块最多 1536 原始字节，以标准带填充 Base64 表达。`recipe_begin` 使用 `transfer_id`；`activate_recipe.params` 引用同一 `transfer_id`、`recipe_digest` 和 `expected_active_digest`。收齐和校验只产生 validated；设备激活仍是独立持久操作。参考组装器在块冲突或校验错误后终止该传输；相同字节重传不增加偏移。

`log_request` 必须提供固定的 `requested={log_id,first_record_seq,last_record_seq}` 和 max_records/max_bytes。上位机从 `status_snapshot.log` 获取可读范围。本版不使用可变的“直到最新”游标。统一 record_seq 跨设备启动延续；重建日志存储必须更换 log_id。记录内保留原 sample.boot_id 或 event 的 boot_id，不能以本次传输的包络 boot_id 替换。

日志为规范 JSON Lines，每条记录包括 LF；记录允许跨块。所有 log_chunk 的 reply_to 始终关联原 log_request。log_ack 包含 `next_offset` 和 `committed_record_seq`；没有完整新记录时后者不推进。参考日志重组器校验块摘要、顺序、固定 highwater、全件摘要、原始记录身份和每个请求位置的覆盖。结果最多列 32 个缺口；更碎的范围应缩小请求或报 range_unavailable，不能遗漏缺口。超时断连不是源数据缺口，不得伪造 log_result。

运行字段使用原始 `measurement_start`、`measurement_end`、`safe_boundary`；每个边界是 boot_id/sample_seq。measurement_complete 指自然测定完成，safe_complete 必须有安全边界。重启后的安全边界可属于新 boot，原测定边界保持原 boot；只在相同 boot 内比较序号。状态字段本身不代替物理条件验收。

首滴检测尝试通过 event 保存 `is_valid`、原事件时间及事件样本的料层温度。无效证据照常归档，但不得作为 Td；telemetry 中的 first_drip_latched 只用于状态显示。报警以 alarm_id/occurrence_seq 标识发生实例；get_alarms 以固定 revision、每页至多 16 条恢复当前报警，后续页必须携带 expected_revision，不能仅依赖可能已淘汰的历史事件。

## 覆盖边界

参考代码完成离线结构、字节、摘要、组装与完整性校验。它没有 TCP/TLS、配对密钥、硬件 I/O、真实计时器、持久化日志、租约状态机或安全执行器。3 次连续非法帧关闭连接、5 秒半帧超时、持久序号去重、ACK 窗口、断电原子激活、工程许可和控制队列公平性由将来的驱动/固件实现并单独验证。不能把这批离线测试计为 STM32、Windows 网络互操作或国标验收通过。

Python 规范实现由 `backend/app/hostcomm/v2_contract` 统一维护，本目录旧导入路径保留为兼容包装；运行检查器会显式加载 backend，安装包从 app 包导入同一实现。Schema 和固定向量继续保留在本目录。
