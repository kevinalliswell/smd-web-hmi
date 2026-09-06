# 固件候选仓库只读发现记录

> 后续澄清（2026-09-06）：用户确认本项目固件尚未开发。以下是此前对相关仓库的只读发现记录，保留历史证据；其中“确认现有运行固件”的下一步假设已不适用。新固件以项目自己的 [HostComm v2.0 设计](../hostcomm/v2/README.md) 为依据，硬件型号、修订及物理点位仍需独立确认；不默认采用任一 TMH 仓库或板型。

记录日期：2026-09-06（Asia/Shanghai）。用途：为 SMD 外部依赖补充可追溯的候选资料，不能作为固件接口冻结或真机验收证明。

## 范围与方法

经当前任务授权，使用已认证的 GitHub CLI，在 `kevinalliswell` 可访问仓库的名称和描述中筛选 SMD、STM32、HostComm、工控、铁矿石实验等相关信息。对两个明显相关的候选，只读取仓库目录元数据、README、协议入口及 HEAD 提交标识。

没有克隆或下载仓库，没有读取或修改固件实现代码，没有运行仓库示例或测试，没有连接设备，也没有向 GitHub 写入消息。文档中的指令和测试声明均作为待核对资料，没有执行或视作本次验证结果。

## 候选一：tmh-stm32-bsp

- 仓库：`kevinalliswell/tmh-stm32-bsp`（本次已认证账号可访问的私有仓库）。
- 核查提交：`0195767925c8da856290dee6dcb3ec66578071a3`。
- GitHub 返回的提交时间：`2026-07-08T10:38:39Z`。

实际读取的入口：

1. [firmware/README.md](https://github.com/kevinalliswell/tmh-stm32-bsp/blob/0195767925c8da856290dee6dcb3ec66578071a3/firmware/README.md)：将项目定位为 STM32F407ZGT6 继电器工控板的 portable BSP skeleton，列出协议映射和集成入口。
2. [firmware/BSP/README.md](https://github.com/kevinalliswell/tmh-stm32-bsp/blob/0195767925c8da856290dee6dcb3ec66578071a3/firmware/BSP/README.md)：描述板级通道、引脚与外设；说明以太网方案为 STM32 ETH MAC + LAN8720A PHY + RMII；明确尚缺完整 CubeMX `.ioc` / HAL 工程，不能独立编译运行，部分采样、总线和网络功能仍需目标工程集成。
3. [STM32F407ZGT6_Modbus_RTU_TCP寄存器映射表.md](https://github.com/kevinalliswell/tmh-stm32-bsp/blob/0195767925c8da856290dee6dcb3ec66578071a3/docs/STM32F407ZGT6_Modbus_RTU_TCP%E5%AF%84%E5%AD%98%E5%99%A8%E6%98%A0%E5%B0%84%E8%A1%A8.md)：只读了开头约 100 行；文档自述为通用默认映射，真实项目点位名称、传感器量程和报警定义仍需补充。不能将其通用 IO/Modbus 定义直接当作 SMD 实验数据字典。

根目录 README 请求返回 404；以上两个固件目录 README 是本次实际使用的说明入口。

判断：可作为板型、引脚和底层接口的候选资料。当前证据不能证明本台 SMD 工控板就是该型号/修订，也不能证明现场已经运行此仓库对应固件。

## 候选二：TMH-Web-STM32

- 仓库：`kevinalliswell/TMH-Web-STM32`（本次已认证账号可访问的私有仓库）。
- 核查提交：`2d6ac5a676a9f248dbd7739682820ca9d3e06238`。
- GitHub 返回的提交时间：`2026-06-27T07:46:05Z`。

实际读取的入口：

1. [README.md](https://github.com/kevinalliswell/TMH-Web-STM32/blob/2d6ac5a676a9f248dbd7739682820ca9d3e06238/README.md)：定位为 TMH-LPF-900 的三层架构重构，上位机为 React/Node 网关，STM32 中间层使用 FreeRTOS/lwIP，下接 9 通道温控、4 路 MFC（H2/N2/CO2/CO）及天平。
2. [firmware/stm32-middle/README.md](https://github.com/kevinalliswell/TMH-Web-STM32/blob/2d6ac5a676a9f248dbd7739682820ca9d3e06238/firmware/stm32-middle/README.md)：描述 proto、orchestrator、safety、ringbuf 的纯 C / host 测试部分，标准计划为 GB/T 13241、13242、13240；目标 MCU 工程、总线驱动、交叉编译和烧录仍依赖硬件选型。该页顶部仍有 M0 占位表述，后文又列出若干 host 实现状态，不能把这些不同层级的声明合并成“目标固件已完成”。本次没有复跑其测试。
3. [protocol/tmh-tcp-protocol.md](https://github.com/kevinalliswell/TMH-Web-STM32/blob/2d6ac5a676a9f248dbd7739682820ca9d3e06238/protocol/tmh-tcp-protocol.md)：实际读取其分帧、版本、HELLO/ACK、SNAPSHOT、LOAD_PLAN、状态与回灌消息定义。

判断：架构和仪表领域相关，但它描述的是 TMH 还原系统，不能直接认定为 GB/T 34211 软熔滴落装置的对应固件。

## 与当前 SMD 通信入口的差异

以下 SMD 项基于本次已读取的本地 `backend/app/hostcomm/protocol.py` 与 `backend/app/core/config.py`；TMH 项基于上述固定提交的协议文件。

| 项目 | 当前 SMD 上位机 | TMH-Web-STM32 候选协议 |
|---|---|---|
| 默认端口 | `34211` | `9760` |
| 分帧 | UTF-8 JSON Lines，以换行分隔 | 4 字节头：大端 uint16 LEN + uint8 VER + uint8 TYPE；后接 JSON payload |
| 版本表达 | `PROTOCOL_VERSION = "1.0"` | `PROTO_VERSION = 1`，帧头 VER 字节 |
| 帧大小约定 | `MAX_FRAME_BYTES = 8192` | payload `LEN ≤ 4096` |
| 实验扩展 | SMD 的 `run_lifecycle_v1`、`recipe_v1` 等候选契约 | TMH 的 `EXP_STATE`、`LOAD_PLAN`、`SNAPSHOT`、`BACKFILL_DATA` 等消息 |

仅调整端口不能使这两种分帧和消息契约兼容。BSP 仓库的通用 Modbus RTU/TCP 映射也不能替代任一实验级协议。

## 对 Q1 / Q2 / Q15 的影响

| 依赖 | 新发现可提供的线索 | 仍不足以关闭的原因 |
|---|---|---|
| Q1：状态枚举、转换、终态与复位 | TMH 固件 README 和 EXP_STATE 协议提供其他实验系统的状态/编排入口 | 未与 SMD 状态、测定终帧、安全完成规则及现场运行固件绑定；没有本台设备带版本的状态回放证据 |
| Q2：传感器/MFC/仪表数据字典 | BSP 通用 IO/量程框架；TMH 温度、流量和重量快照 | BSP 明示真实项目点位待补；TMH 字段不能证明 SMD 的料温、压差、位移、首滴、质量标记和采样周期契约 |
| Q15：配方执行、原子切换/回读与超时 | TMH LOAD_PLAN 与纯 C 阶段执行器的文档入口 | 没有证明支持 SMD `recipe_v1` 的能力声明、版本/摘要回读、原子部署和失败回滚，也没有对应目标固件验证 |

结论：已有可访问的相关资料候选，应避免继续笼统表述“完全没有固件资料”。但仓库与本台设备的对应关系仍未确认，Q1/Q2/Q15 保持待确认，不能宣称接口冻结或真机通过。

下一步由固件/项目负责人确认实际固件仓库、工控板型号/硬件修订和现场运行版本，并按[接口对齐清单](../待确认事项与接口对齐清单.md)提交关闭证据。
