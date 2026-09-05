# Windows 工控机离线部署说明（源码脚本基线）

> 本文描述 `dev@855c84d` 的源码+wheels/计划任务部署，不代表独立桌面安装器已完成。当前目标与发布规则统一见[发布与维护指南](../../docs/发布与维护指南.md)。旧升级脚本的完整依赖环境回退、自定义数据库路径和启动验活存在已知缺口；M4验收前不得将下面的脚本流程当作经过验证的现场升级方案。

> ⚠️ **本目录脚本尚未在真实工控机上验证**。进入 D4 冷态验收之前，务必先在一台
> 干净的 zh-CN Windows 机器上完整演练一遍安装 → 升级 → 回滚流程。
>
> `.bat` 脚本刻意只用纯 ASCII（英文提示）：zh-CN Windows 的 cmd 以 GBK 解析
> 批处理，UTF-8 中文会乱码甚至破坏解析（与 alembic.ini 的 ASCII 修复同一教训）。
> 全部中文说明集中在本文件。

## 离线包结构

发布流水线（`.github/workflows/release.yml`，打 tag 自动触发）产出
`smd-web-hmi-vX.Y.Z-win64.zip`，解压后：

```
smd-web-hmi-vX.Y.Z-win64\
├─ app\
│  ├─ backend\            ← 后端源码（含 alembic 迁移；.env 安装时生成于此）
│  └─ frontend_dist\      ← 前端构建产物（后端同源托管，无需单独 Web 服务器）
├─ wheels\                ← 全部 Python 依赖（cp311 / win_amd64），离线安装用
├─ install.bat            ← 首次安装
├─ upgrade.bat            ← 升级已有安装（在新包目录里运行）
├─ run_server.bat         ← 前台启动（调试/试运行，读取 .env 的 host/port）
├─ start_server.ps1       ← 计划任务后台入口（日志写入 logs\server.log）
├─ register_autostart.ps1 / unregister_autostart.ps1
├─ health_check.ps1       ← liveness/readiness 验证
├─ backup_database.py     ← 升级前 SQLite online backup + integrity_check
├─ .env.example
├─ CHANGELOG.md
└─ README.md              ← 本文件
```

安装后目录中另有：`venv\`、`data\`（**smd.db + reports/exports/backups**）、
`rollback\`（升级时保留的上一版程序）、`logs\` 和 `initial-admin-password.txt`。

## 前置条件

1. Windows 10/11 或 Server x64；
2. 已安装 **Python 3.11 x64**（官方安装器，勾选 *Add python.exe to PATH*；机器离线时提前用 U 盘拷入安装器）；
3. 使用“以管理员身份运行”的命令提示符执行安装与升级；
4. 解压路径**不要包含空格与中文**（如 `C:\smd-web-hmi`）；
5. 防火墙放行 TCP 8000（局域网浏览器访问）；与控制板通信走 TCP 34211（出站）。

## 首次安装

```bat
cd C:\smd-web-hmi
install.bat
```

浏览器访问 `http://<工控机IP>:8000`。账户为 `admin`，一次性随机口令见
`initial-admin-password.txt`。首次登录只能改密；成功后安全删除该文件。
如需连接 Mock（无控制板调试），编辑 `app\backend\.env` 设 `HOSTCOMM_MOCK=true`。
安装器会把 `.env`、一次性口令文件和 `data\` 的 ACL 限制为安装管理员与 SYSTEM。

安装器会注册并立即启动 `smd-web-hmi` SYSTEM 计划任务；需要重建任务时，以管理员身份执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\register_autostart.ps1
.\health_check.ps1
```

## 升级

1. 把新版 zip 解压到**另一个**目录（如 `C:\smd-upgrade\smd-web-hmi-v0.3.0-win64`）；
2. 关闭手工启动的 `run_server.bat` 窗口（计划任务由升级器主动停止）；
3. 在新包目录运行：

```bat
upgrade.bat C:\smd-web-hmi
```

脚本顺序：撤销自动重启任务 → SQLite online backup 到 `data\backups\` 并执行完整性检查 →
保留 `.env` 和上一版程序 → 安装新版 → 离线更新依赖 → `alembic upgrade head` → 重建并启动任务。
基线脚本尝试恢复旧程序和默认路径数据库，但没有完整恢复被原地修改的依赖环境，也未完整支持自定义数据库路径及启动验活。升级前须按实际配置验证整包备份与隔离恢复；目标事务升级见发布与维护指南。

## 回滚

1. 停止服务；
2. 使用 `rollback\app-<时间戳>` 中保留的上一版程序，或用上一版 zip 的 `app\` 覆盖当前 `app\`；
3. **如新版本执行过数据库迁移**：将 `data\backups\smd-upgrade-<时间戳>.db` 复制回 `data\smd.db`。
   不要使用 `alembic downgrade`——日志类表只追加（安全红线 7），降级迁移不可靠，
   整库还原备份才是安全路径（代价：丢失升级后新写入的数据，回滚前先确认）。

## 开机自启

安装器默认注册 SYSTEM 计划任务：开机启动，进程异常退出后 1 分钟重启，最多 999 次，
日志写入 `logs\server.log`，达到 20 MB 时轮转并保留最近 5 份。停用或卸载时以管理员身份运行
`unregister_autostart.ps1`。

## 日常运维要点

- `data\smd.db` 是全部试验归档；服务默认每天 online backup 到 `data\backups` 并校验，
  保留 30 天；升级前备份也写入该目录并带 `smd-upgrade-` 前缀。
- 版本查询：登录后系统设置页，或 `http://<IP>:8000/health`。
- 就绪巡检：`powershell -File health_check.ps1`；管理员可调用 `POST /api/system/backup` 手工备份。
- 完整发布/维护流程见仓库 `docs/发布与维护指南.md`。
