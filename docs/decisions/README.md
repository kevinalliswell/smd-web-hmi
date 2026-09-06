# 架构决策索引

| ADR | 状态 | 主题 |
|---|---|---|
| [001](ADR-001-forced-password-change.md) | 已被 004 替代 | 初始口令及 JWT 受限权限的旧论证 |
| [002](ADR-002-utc-timestamps-on-sqlite.md) | 继续有效，新增采集语义见 005 | SQLite 规范化 UTC 文本 |
| [003](ADR-003-project-baseline.md) | 已接受 | 文档真源、主干整合和版本依据 |
| [004](ADR-004-authentication-lifecycle.md) | 已接受；描述基线已实现行为 | 一次性口令、数据库会话撤销和 WS 认证 |
| [005](ADR-005-shared-runtime-and-evidence.md) | 已接受；交付能力待任务验收 | 共用后台、桌面安装、设备操作和实验追溯 |
| [006](ADR-006-candidate-experiment-contracts.md) | 继续解释 1.0；新固件设计由 008 替代 | 配方/生命周期/首滴/序号与测定完整性 |
| [007](ADR-007-device-gateway-lease.md) | 软件已实现；Windows专用用例见软件证据，现场待验收 | 所有生产入口按设备端点取得同机 OS 租约 |
| [008](ADR-008-hostcomm-v2-design.md) | 2.0 设计基线已接受；双方运行实现与真机待办 | 项目自有协议、固定点配方、持久操作、板端恢复与补传 |

已接受表示选型成立，不代表实现或真机验收完成。旧 ADR 保留历史，在新 ADR 中说明变化。
