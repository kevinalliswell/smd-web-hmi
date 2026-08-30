# Windows 工控机离线部署说明

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
├─ run_server.bat         ← 前台启动（调试/试运行）
├─ .env.example
├─ CHANGELOG.md
└─ README.md              ← 本文件
```

安装后目录中另有（均不会被升级覆盖）：`venv\`（Python 环境）、`data\`
（**smd.db 数据库 + reports/exports，全部试验资产所在**）、`backups\`（升级前数据库备份）。

## 前置条件

1. Windows 10/11 或 Server x64；
2. 已安装 **Python 3.11 x64**（官方安装器，勾选 *Add python.exe to PATH*；机器离线时提前用 U 盘拷入安装器）；
3. 解压路径**不要包含空格与中文**（如 `C:\smd-web-hmi`）；
4. 防火墙放行 TCP 8000（局域网浏览器访问）；与控制板通信走 TCP 34211（出站）。

## 首次安装

```bat
cd C:\smd-web-hmi
install.bat
run_server.bat
```

浏览器访问 `http://<工控机IP>:8000`。默认账户 `admin/admin`，**首次登录后立即修改密码**。
如需连接 Mock（无控制板调试），编辑 `app\backend\.env` 设 `HOSTCOMM_MOCK=true`。

## 升级

1. 把新版 zip 解压到**另一个**目录（如 `C:\smd-upgrade\smd-web-hmi-v0.3.0-win64`）；
2. 停止正在运行的服务（关闭 run_server 窗口或停止服务/计划任务）；
3. 在新包目录运行：

```bat
upgrade.bat C:\smd-web-hmi
```

脚本顺序：备份 `data\smd.db` 到 `backups\` → 保留 `.env` → 替换 `app\` →
离线更新依赖 → `alembic upgrade head`。数据目录 `data\` 不会被触碰。

## 回滚

1. 停止服务；
2. 用上一版 zip 的 `app\` 覆盖当前 `app\`（或直接对旧版包再跑一次 `upgrade.bat`）；
3. **如新版本执行过数据库迁移**：将 `backups\smd-<时间戳>.db` 复制回 `data\smd.db`。
   不要使用 `alembic downgrade`——日志类表只追加（安全红线 7），降级迁移不可靠，
   整库还原备份才是安全路径（代价：丢失升级后新写入的数据，回滚前先确认）。

## 开机自启（可选）

试运行阶段用计划任务（以 SYSTEM 无窗口运行，日志见结构化输出重定向）：

```bat
schtasks /Create /TN "smd-web-hmi" /SC ONSTART /RU SYSTEM ^
  /TR "C:\smd-web-hmi\run_server.bat"
```

正式运行建议用 [WinSW](https://github.com/winsw/winsw) 或 NSSM 把
`venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
包装成 Windows 服务（可配日志文件、自动重启）。D4 验收前确定其一并演练。

## 日常运维要点

- `data\smd.db` 是全部试验归档（sample_point / event_log / alarm_log 只追加），
  **定期拷贝备份**；升级脚本只在升级时自动备份一次。
- 版本查询：登录后系统设置页，或 `http://<IP>:8000/health`。
- 完整发布/维护流程见仓库 `docs/发布与维护指南.md`。
