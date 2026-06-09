---
description: 重置开发数据库（删除 SQLite 文件并重新 alembic upgrade head）
---

重置开发用 SQLite 数据库。

⚠️ 仅用于开发环境。生产/联调数据库严禁执行（会丢失试验数据）。

执行步骤：
1. 读取 `backend/.env` 中的 `SMD_DB_PATH`（默认 `./data/smd.db`）。
2. 确认当前不是生产环境（检查路径不含 `prod`，并向用户二次确认）。
3. 删除该 SQLite 文件。
4. 在 `backend/` 下运行 `alembic upgrade head` 重建全部表。
5. 重新插入初始 admin 账户（用户名 `admin`，首次登录提示修改密码）。
6. 报告已重建的表清单。

注意：`sample_point` / `event_log` / `alarm_log` 为只追加表，本命令仅在开发期
整库重置时清空，正常运行期间不得 DELETE 其中记录。
