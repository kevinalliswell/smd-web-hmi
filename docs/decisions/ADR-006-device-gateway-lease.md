# ADR-006：同机生产网关以设备端点取得进程租约

状态：已实施，Windows跨账户运行验证由CI及现场记录确认。
日期：2026-09-06。

## 问题与决定

桌面服务的进程互斥只覆盖桌面入口，直接通过Python/Uvicorn运行的Web后台仍可能连接相同STM32。所有生产FastAPI生命周期必须在数据库恢复、参数/操作对账和HostComm连接之前取得网关租约；失败即拒绝启动。正常关闭先停止通信再释放租约，进程死亡由OS释放。Mock开发实例跳过生产租约。

身份只来自规范化的设备字面IP和端口，不包含数据目录、用户、工作目录或前端类型。IPv4映射IPv6与IPv4统一；生产DNS名称拒绝，避免别名或地址变化造成不同锁身份。域名支持需要另行定义解析结果固定与实际连接地址一致的契约。

## 平台边界

Windows使用`Global\SmdHmi.Gateway.<endpoint-sha256>`命名mutex，允许已认证用户、LocalService和NetworkService取得同步/释放权限，不授予普通用户修改DACL的权限。显式Medium完整性标签防止服务创建的对象仅因完整性级别而拒绝普通CLI。租约在同一生命周期线程获取与释放；另有进程内防递归检查。

POSIX使用固定`/var/lock/smd-hmi`目录内的`flock`。该目录必须由管理员预建，root所有、权限`01777`；应用不会自动创建这个生产目录，也不允许每个部署通过配置改到自己的数据目录。部署准备命令为：

```sh
sudo mkdir -p /var/lock/smd-hmi
sudo chown root /var/lock/smd-hmi
sudo chmod 1777 /var/lock/smd-hmi
```

运行账户需能读取目录中的普通锁文件。文件不含凭证，不在释放后删除；不能让清理作业删除正在使用的锁文件。程序拒绝符号链接、硬链接和不稳定inode。测试显式注入当前用户拥有且不允许其他用户写入的临时目录；生产启动不使用此注入入口。

租约协调同一OS命名空间中的正常应用实例，不阻止管理员删除锁/改变ACL，也不能阻止任意程序绕过本应用直接连接硬件。容器需共享同一命名空间/锁目录，当前不声明隔离容器或跨主机互斥。多个主机争抢同一STM32仍需固件独占连接/控制权契约，未通过OS锁代替板端安全控制。

## 验证

`tests/test_gateway_lease.py`验证独立进程持有、等价端点拒绝第二实例、强制结束后的再次取得、同进程递归拒绝、POSIX文件安全及生命周期顺序。Windows专用跨账户测试通过随机计划任务以LocalService持锁，再由CI账户尝试取得；它只在Windows GitHub Actions执行，不能把其他系统上的跳过结果计作通过。

## 官方依据

- [Windows全局内核对象命名空间](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces)
- [CreateMutexExW及所需访问权限](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createmutexexw)
- [Windows强制完整性控制](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control)
