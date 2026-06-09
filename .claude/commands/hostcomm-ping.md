---
description: 测试 HostComm 连接（向 Mock/真实控制板发送 hello 并打印 hello_ack）
---

测试 HostComm 连接连通性。

执行步骤：
1. 读取 `backend/.env` 中的 `HOSTCOMM_HOST` / `HOSTCOMM_PORT` / `HOSTCOMM_MOCK`。
2. 若 `HOSTCOMM_MOCK=true`，提示用户先在另一终端运行
   `python -m app.hostcomm.mock_server`。
3. 运行 `python -m app.hostcomm.client --ping`，建立 TCP 连接、发送 `hello`，
   等待 `hello_ack`，打印协议版本、固件版本和 capabilities。
4. 报告连接结果（成功/超时/拒绝）以及握手耗时。

注意：本命令只做只读握手，不发送任何控制命令。
