# SmdHmi 与固件契约兼容核对

文档修订：`2.0-doc.2`，2026-09-07。审查基线和版本关系见[修订记录](revisions.md)。结论：2.0 主机实现与主要消息/事务结构一致，但仍有下列实现或语义缺口，不能把文档升级或模拟器通过解释为双方已经完全匹配。目标STM32固件尚未交付。

“文档已修正”只关闭说明问题；“待实现/待决”需要单独的代码、契约决策与回归证据。固件应按规范实现，不应复制主机当前遗漏来取得表面兼容。

## 审查发现和处理

| ID | 发现及证据 | 本轮处理 / 后续责任 |
|---|---|---|
| ALIGN-01 | 部分文档仍称2.0主机/TLS“将来实现”；实际已有 [security](../../../backend/app/hostcomm/v2_security.py)、[client](../../../backend/app/hostcomm/v2_client.py) 与[运行验证](../../verification/2026-09-06-hostcomm-v2-runtime.md) | 文档已修正为主机已实现、固件/现场待验；保留历史ADR语境 |
| ALIGN-02 | hello/hello_ack是固定字段与design.1，四能力恰好各一次；没有帧大小/超时协商字段。见 [Hello/HelloAck模型](../../../backend/app/hostcomm/v2_contract/messages.py) | wire补齐字段及实际传输边界；固件不能宣告未实现能力或发送额外资源字段 |
| ALIGN-03 | 日志扫描水位可越过已确认源缺口，完整性区间不能。见 [V2SourceLogStore](../../../backend/app/services/v2_source_logs.py) | state说明字节ACK、完整记录、扫描与verified区间的区别；“扫描完成”不等于数据完整 |
| ALIGN-04 | 规范要求陌生运行建立待核查归档；当前 [V2ArchiveProjector](../../../backend/app/services/v2_archive.py) 对缺少V2RunBinding的运行保留raw-only，不自动建立TestSession或恢复绑定；有 [unknown-run回归](../../../backend/tests/test_v2_archive.py) | **主机待实现，HOST-2006**：明确孤立运行提示、人工核查/绑定、重放投影和恢复门禁。原始数据有保留不等于已能在实验页恢复和报告；固件仍保留真实run/边界 |
| ALIGN-05 | [HeartbeatAck模型](../../../backend/app/hostcomm/v2_contract/messages.py) 未校验lease_id与expiry成对一致；[传输](../../../backend/app/hostcomm/v2_transport.py)核对非空续期的ID，当前剩余租约主要由[新鲜status投影](../../../backend/app/hostcomm/v2_projection.py)判断 | **主机待对齐，HOST-2007**：补成对字段/时效回读及异常回归，确认heartbeat与status计算关系。固件不得利用缺失校验发送自相矛盾租约；板端仍按自身时钟执行租约 |
| ALIGN-06 | hello_ack.control_ready存在，但[投影](../../../backend/app/hostcomm/v2_projection.py)不直接采用它；主机依据恢复完成、新鲜status、profile批准与control角色判定 | **语义待决并实现，HOST-2007**：明确握手旗标是快照提示还是强制门禁，补“hello为false而后续status允许”的测试；固件不得把唯一禁止原因仅放在此旗标，应在当前状态、工程批准和实际执行点保持禁止 |
| ALIGN-07 | [transport](../../../backend/app/hostcomm/v2_transport.py)在上一心跳ACK后等待2秒；重连30秒是退避基数，±10%抖动后可达33秒，与严格周期/总上限的读法不同 | wire保留设计目标并公开现状；**HOST-2008**明确计时口径并补边界回归。板端失联处置依自己的8秒租约，不依赖“主机一定每2秒发包” |
| ALIGN-08 | [模拟器CLI](../../../backend/app/hostcomm/v2_simulator/__main__.py)只支持loopback；完整阶段推进使用Python测试方法，不存在finish_measurement等HostComm命令；安装包未提供独立模拟器可执行文件 | runtime/路线补开发环境和测试驱动边界。**HOST-2009**验证实际Windows安装包到TLS模拟器的完整业务，不能用安装成功或Mac浏览器记录关闭 |
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

以上为已审查源码及已有回归入口，本轮文档检查不重新认定目标板TLS、全部异常场景或Windows完整实验已通过。

## 联调放行边界

先用固定测试profile、独立数据和无执行器模型开展编码/握手/事务检查。进入真实控制联调前，双方关闭适用的ALIGN实现/语义项并绑定测试证据；FW-01的工程与安全资料必须具备。现场验收仍按FW-09/10及M5执行，禁止为了点亮“在线”而降级协议、伪造能力、忽略未知运行或替换未批准profile。
