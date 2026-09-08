# SmdHmi 与固件契约兼容核对

文档修订：`2.0-doc.3`，2026-09-08。审查基线和版本关系见[修订记录](revisions.md)。本轮在rc.4审查结果上落实HOST-2006—2009；代码完成状态与Windows执行证据见[本轮验证](../../verification/2026-09-08-installed-hostcomm-loop.md)。不能把文档升级或模拟器通过解释为双方已经完全匹配。目标STM32固件尚未交付。

“文档已修正”只关闭说明问题；“待实现/待决”需要单独的代码、契约决策与回归证据。固件应按规范实现，不应复制主机当前遗漏来取得表面兼容。

## 审查发现和处理

| ID | 发现及证据 | 本轮处理 / 后续责任 |
|---|---|---|
| ALIGN-01 | 部分文档仍称2.0主机/TLS“将来实现”；实际已有 [security](../../../backend/app/hostcomm/v2_security.py)、[client](../../../backend/app/hostcomm/v2_client.py) 与[运行验证](../../verification/2026-09-06-hostcomm-v2-runtime.md) | 文档已修正为主机已实现、固件/现场待验；保留历史ADR语境 |
| ALIGN-02 | hello/hello_ack是固定字段与design.1，四能力恰好各一次；没有帧大小/超时协商字段。见 [Hello/HelloAck模型](../../../backend/app/hostcomm/v2_contract/messages.py) | wire补齐字段及实际传输边界；固件不能宣告未实现能力或发送额外资源字段 |
| ALIGN-03 | 日志扫描水位可越过已确认源缺口，完整性区间不能。见 [V2SourceLogStore](../../../backend/app/services/v2_source_logs.py) | state说明字节ACK、完整记录、扫描与verified区间的区别；“扫描完成”不等于数据完整 |
| ALIGN-04 | rc.4陌生运行仅保留raw-only，缺少归属和回放入口 | HOST-2006增加持久发现、管理员/维护员审查绑定、可恢复回放及关联原报警。真实开始未知不补造；报告和结束核查按源边界门禁。实现：[恢复服务](../../../backend/app/services/v2_run_recovery.py)、[回归](../../../backend/tests/test_v2_run_recovery.py) |
| ALIGN-05 | rc.4心跳租约字段未联合校验，控制主要依据status期限 | HOST-2007增加同空约束、8秒范围、保守截止时间和会话/代次条件更新；矛盾回执断连，过期/旧回执不恢复许可。见[租约上下文](../../../backend/app/hostcomm/v2_lease.py)、[计时回归](../../../backend/tests/test_v2_control_timing.py) |
| ALIGN-06 | rc.4未明确hello_ack.control_ready与持续控制的关系 | [ADR-010](../../decisions/ADR-010-installed-hostcomm-loop.md)确定为握手时提示，持续权限依据当前角色、批准配置、新鲜状态与租约。固件持续禁止原因必须反映在当前状态和执行点，不能只放在握手布尔值 |
| ALIGN-07 | rc.4心跳每次ACK后另等2秒，重连抖动可能33秒 | HOST-2008采用心跳单一3秒预算、发送起点2秒调度、单个在途；实际重连不超过30秒，稳定连接后重置退避。板端8秒租约不变，见[wire](wire.md) |
| ALIGN-08 | rc.4安装包未提供独立模拟器，完整阶段仅能用源码测试方法注入 | HOST-2009增加独立[SmdBench工具](../../../tools/bench/README.md)，不包含在生产安装包。真实TLS/页面/报告/故障测试进入Windows CI；Win10/11 WebView2和真机继续单列未验收 |
| ALIGN-09 | 板卡资料已有W25Q128，但原理图、启动/分区、引脚复用和实装性能未核验 | data修正“外部Flash未知”的旧说明；**FW-01**继续阻塞目标板工程验收，不把16MiB或ADC标称速率当可用日志预算/网络频率 |

另一个已明确的主机范围：陌生活动配方可读取为wire_recipe诊断材料；没有本地配方绑定时不能直接当作可启动配方，见[data的兼容说明](data-and-recipe.md)。固件仍返回真实活动字节，不能改报某个本地已知摘要。

## 已有对应实现的主要链路

| 链路 | 主机实现与测试入口 | 固件交付应证明什么 |
|---|---|---|
| TLS、严格分帧、会话与响应关联 | [transport](../../../backend/app/hostcomm/v2_transport.py)、[codec](../../../backend/app/hostcomm/v2_contract/codec.py)、[传输测试](../../../backend/tests/test_hostcomm_v2_transport.py) | 指定TLS库与H750上的资源、拒绝和超时行为；不是仅在PC返回hello |
| 操作身份、持久序号、查询恢复 | [操作服务](../../../backend/app/services/v2_operations.py)、[操作测试](../../../backend/tests/test_v2_operations.py) | 持久提交点断电后旧序号不重做；unknown不伪造为成功 |
| 配方分块与完整回读 | [client](../../../backend/app/hostcomm/v2_client.py)、[编译测试](../../../backend/tests/test_v2_recipe_compiler.py)、[客户端测试](../../../backend/tests/test_v2_client.py) | 不以validated代替activated；活动字节、摘要及配置绑定一致 |
| 源日志、报警、归档 | [源日志测试](../../../backend/tests/test_v2_source_logs.py)、[归档测试](../../../backend/tests/test_v2_archive.py) | 原始身份、边界、缺口和全件摘要，完整采样与真实首滴依据 |
| REST到设备与停止后冷却 | [业务回归](../../../backend/tests/test_v2_application.py) | 板端自主推进安全处置，停止ACK后继续采样，明确终态再确认归档 |

以上为源码与回归入口，具体通过情况以本轮验证的提交和执行产物为准；目标板TLS和实体安全仍未验收。

## 联调放行边界

先用固定测试profile、独立数据和无执行器模型开展编码/握手/事务检查。进入真实控制联调前，双方关闭适用的ALIGN实现/语义项并绑定测试证据；FW-01的工程与安全资料必须具备。现场验收仍按FW-09/10及M5执行，禁止为了点亮“在线”而降级协议、伪造能力、忽略未知运行或替换未批准profile。
