# Tauri 2 迁移任务台账

用户已批准 P0—P7 实施，技术栈及边界见 [ADR-012](../docs/decisions/ADR-012-tauri-modernization.md)。本文件是本轮迁移进度真源；原固件/现场任务继续在 [既有待办](todo.md) 维护，不因换壳关闭。

基线：`3b1552d7656fdee7df4beba3b4a05fbde485d5c9`。状态沿用 todo/doing/implemented/done/blocked；只有任务内全部退出条件取得证据才为 done。阶段独立提交、推送、草稿 PR，不自动合并或发布。

## 阶段及验收

| 阶段 | 分支 / PR | 退出条件 | 状态 / 证据 |
|---|---|---|---|
| P0 基线 | `codex/tauri-p0`；[草稿 PR98](https://github.com/kevinalliswell/smd-web-hmi/pull/98)，基线 main | 决策/台账、工具链、原版检查及隔离运行基线可复验 | done；[验证记录](../docs/verification/2026-10-22-tauri-p0.md)，既有失败已独列，CI结果单独记录 |
| P1 桌面与总览 | `codex/tauri-p1`；[草稿 PR99](https://github.com/kevinalliswell/smd-web-hmi/pull/99)，基线 `codex/tauri-p0` | 实际 WebView2、模拟器数据、新总览、其他页可用、无第二网关、样包 | doing；[验证记录](../docs/verification/2026-10-23-tauri-p1.md) |
| P2 任务可靠性 | `codex/tauri-p2`，依赖 P1 | 持久任务、渲染隔离、重启/故障/冲突及通信延迟验证 | todo |
| P3 控制与恢复 | `codex/tauri-p3`，依赖 P2 | 首次登录至控制、报警、结束及恢复的业务闭环 | todo |
| P4 配置维护 | `codex/tauri-p4`，依赖 P3 | 配方部署/回读、协议参数、HostComm 诊断、四角色 | todo |
| P5 数据报告 | `codex/tauri-p5`，依赖 P4 | 七通道/历史修订/八次比较/重复性/归档，全应用严格类型 | todo |
| P6 完整交付 | `codex/tauri-p6`，依赖 P5 | 同提交版本/清单、保留数据升级、失败恢复及签名限制 | todo |
| P7 候选验收 | `codex/tauri-p7`，依赖 P6 | 双端/故障/24h软件测量及实际产物；真机未验独列 | todo |

## 可执行 TODO

| ID | 工作与依赖 | 验收证据 | 状态 |
|---|---|---|---|
| P0-01 | ADR：进程、来源、权限、安装、旧壳退出 | ADR-012 + 文档检查；提交/PR见下方交付索引 | done |
| P0-02 | 本台账 + 路线图/待办/文档入口 | 全 P0—P7 编号可追踪；提交/PR见下方交付索引 | done |
| P0-03 | Python3.13环境、Node24、Rust锁、Windows工具链盘点 | 锁安装、版本记录；本机未发现原生编译链，Windows CI作为安装基准 | done |
| P0-04 | 原版检查、安装布局、隔离运行/内存/通信/报告夹具 | 基线SHA、命令、JSON、已知失败；不等于全部基线检查通过 | done |
| P1-00 | P0发现的依赖安全/类型生成/Windows子进程编码与测试空间问题；P0 | 单独提交；审计0项、前端129测试、冻结等43通过/1跳过，见P1记录 | done |
| P1-01 | Tauri薄壳、单实例、来源限制、固定WebView2；P0 | 已实现；Windows CI fmt/Clippy/6项Rust测试/release通过；窗口实际生命周期待验 | implemented |
| P1-02 | 内置诊断；P1-01 | 已实现配置/连接/TLS/运行时异常及重试；无服务控制；实际异常UI待验 | implemented |
| P1-03 | 设备/认证/HTTP/WS首批TS；P0 | 首批TS/运行时校验及非法WS回归已实现；见P1记录 | implemented |
| P1-04 | 布局/主题/总览；P1-03 | 155项前端测试；浏览器深浅主题/390px截图已验；实际WebView待验 | implemented |
| P1-05 | 同源API/store接入；P1-04 | 独立Mock/API/WS登录与13路由只读访问通过；桌面联调待验 | doing |
| P1-06 | 构建/自检/实际渲染及安装样包；P1-01—05 | 已接既有冻结/NSIS/清单/SBOM，新增4项打包回归；样包与实际生命周期待验 | doing |
| P2-01 | 持久任务与追加迁移；P1 | 旧库/空库、状态/操作者/来源/错误 | todo |
| P2-02 | 原任务路由兼容、新/api/jobs与权限；P2-01 | 本人/管理员、分页、旧客户端回归 | todo |
| P2-03 | 可序列化报告快照；P2-01 | 边界/修订/水位/版本固定，渲染不占事务 | todo |
| P2-04 | SmdRender单worker及冻结；P2-03 | 无DB/设备/密钥能力；真实PDF/XLSX/HTML | todo |
| P2-05 | interrupted/核验/归档/显式重试；P2-02—04 | 重启、进程崩溃、磁盘不足、来源冲突 | todo |
| P2-06 | 事件循环/队列/持久化/回执/空间指标；P2 | 大报告/慢WS不违反时序；包清单同步 | todo |
| P3-01 | 登录/初始改密/会话/路由TS；P2 | 失效、强制改密、角色边界 | todo |
| P3-02 | 当前试验/H/配方/能力/完成/复位；P3-01 | 与后端许可一致，受控停止例外保留 | todo |
| P3-03 | 报警发生实例/命令结果与对账；P3-02 | 消除未确认/未知/回读状态不混淆 | todo |
| P3-04 | 陌生运行核查/绑定/回放/冲突/审计；P3-03 | 不补造开始时间，不自动恢复输出 | todo |
| P4-01 | 后台候选配方与版本/校验/激活/回读；P3 | 失败/未知/不一致不显示已生效 | todo |
| P4-02 | v1事务/v2只读profile；P4-01 | 权限、过期、未知样例 | todo |
| P4-03 | HostComm及子设备诊断；P4-02 | maintainer门控，I/O未定义不虚构 | todo |
| P4-04 | 四角色/账户/密码/校时/维护；P4-03 | 账户与维护生命周期行为 | todo |
| P5-01 | 七通道趋势/历史质量/修订；P4 | 查询/降采样/原值/修订原因 | todo |
| P5-02 | 八次对比与2—4次重复性；P5-01 | 资格、顺序、采用值、补试 | todo |
| P5-03 | 三格式/ZIP/任务列表/重试/归档；P5-02 | P2状态驱动，产物可核验 | todo |
| P5-04 | 帮助、16页/store严格TS；P5-03 | 全应用检查，无全局any/ts-nocheck逃逸 | todo |
| P6-01 | 壳/服务/worker/schema/运行时清单；P5 | 同SHA/version/hash与SBOM | todo |
| P6-02 | 保留数据升级；P6-01 | 真实路径、账户、配置、密钥、历史 | todo |
| P6-03 | 迁移/低空间/中断/启动/版本失败；P6-02 | 可恢复，不用旧备份覆盖新业务 | todo |
| P6-04 | 删除仅旧壳依赖；P6-03 | 服务/配对/维护/离线工具回归 | todo |
| P6-05 | 签名/离线材料/升级恢复说明；P6-04 | 实际字节；无证书时明确未验限制 | todo |
| P7-01 | 桌面/LAN端到端；P6 | 同用户/角色/质量与结果一致 | todo |
| P7-02 | UI/服务/worker/网络/存储故障；P7-01 | 故障矩阵及恢复证据 | todo |
| P7-03 | 24h模拟器测量；P7-02 | 公开速率/资源/缺口/恢复，无真实设备声明 | todo |
| P7-04 | 候选包及验证归档；P7-03 | SHA/环境/日志/截图/报告/遗留项目 | todo |

## 实施规则

- 前阶段未合并时，后阶段以其分支为PR基线；合并后再更新基线。每项在验证记录中绑定实际提交，PR编号和最后验证提交在阶段收尾补记。
- 每阶段交付：任务状态、草稿PR、运行/检查命令、结果、截图或样包、未验事项和下一入口。
- 任务状态：pending → running → completed/failed/interrupted；服务重启遗留任务中断不自动重跑。仅导出显式重试创建关联新任务，不自动重发物理命令或源日志补传。
- /api/jobs分页，操作员本人、管理员以上全部；原任务路由兼容，权限不降低。P2随代码生成OpenAPI及迁移证据。
- 不自动合并、发布或安装到现场生产目录；所有基线夹具使用独立临时目录和loopback。

## 阶段交付索引

P0-01—04：实施提交 `cd5138522498073080d97959ec0da5fdcffb6956`，[草稿 PR98](https://github.com/kevinalliswell/smd-web-hmi/pull/98)，基线 `main@3b1552d7656fdee7df4beba3b4a05fbde485d5c9`。验证、未验及遗留事项均见[P0记录](../docs/verification/2026-10-22-tauri-p0.md)。可运行交付是 `tools/modernization/baseline.py`，不包含新桌面样包。下一入口 `P1-00`，随后 `P1-01/P1-03`；分支从本P0分支依赖创建，P0未合并前PR基线保持 `codex/tauri-p0`。

P1 进行中：[草稿 PR99](https://github.com/kevinalliswell/smd-web-hmi/pull/99)。首批前端 `c811d70`，Tauri/构建链 `f02e11f`，Rust检查修复 `787f2e4`，依赖审计修复 `006d50e`。后续入口为 **P1-06实际样包与WebView2验证**，并补P1-01/02/05验收；在本阶段退出前不进入P2。
