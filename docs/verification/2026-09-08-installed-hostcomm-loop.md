# Windows安装版与HostComm实验闭环验证

实施基线：`89e5ec1ed18c7232ad07a222b2f1c49059bcdcec`，上一已发布候选rc.4。
本轮候选：`0.3.0-rc.5`；文档修订：`2.0-doc.3`；线协议：`2.0 / 2.0-design.1`。

## 状态

首个集成提交为 `c6c2a0697c07b378011e3929e4d5aaf3a4af5c56`，见[PR #75](https://github.com/kevinalliswell/smd-web-hmi/pull/75)。第十一轮共享检查、实际Windows安装版业务、报告、清理、严格封装及下载产物核验均通过，完成HOST-2006—2009的软件范围。结论仅覆盖下方记录的构建和实际字节；候选交付状态、最终提交与字节以对应标签流水线及 Release 附件为准。Win10/11桌面人工与固件/真机验收仍未完成；前十轮失败证据继续保留。

| 层次 | 验证内容 | 当前证据 |
|---|---|---|
| 本机软件 | 心跳/租约/重连，未知运行审查与回放，报告空值/完整性门禁，工具归属与发布门禁 | `e27c32ef7e27b20f612e5705bb26b4c5b400512a`在Python3.13.12/macOS后端759通过、2项Windows限定跳过，覆盖率86.88%；Node24前端123通过，类型/lint/build通过；发行门禁补字节绑定反例后8通过 |
| 模拟器 | 真实TLS、合成阶段、故障注入、日志/报警关联和实际导出 | macOS源码后台与Chromium通过15项业务断言，下载并检查10份实际报告；未替代Windows冻结程序验收 |
| Windows安装版业务 | 实际NSIS/LocalService；旧安装冒烟；独立工具重新安装并执行TLS/页面/报告 | 第十一轮17项业务断言、10份实际报告及清理全部通过；页面/API/console非预期错误均为0，`acceptance.json`为passed且`cleanup_complete=true` |
| 候选封装与交付 | 运行后工具文件清单、安装器/工具摘要及发行ZIP门禁 | 第十一轮冻结自检后清单、最终严格封装及下载产物独立核验通过；摘要见下文。每次标签重新验收实际构建，不能复用不同字节的结论 |
| Win10/11桌面 | WebView2、关闭窗口持续采集、中文路径、显示缩放、下载及重开 | 未验收，单列人工复验 |
| 固件/真机 | STM32H750、TLS栈、外设/联锁、工艺时长、完整实验与24小时 | 未验收；固件尚待开发 |

CI发现并复现了两项问题：[首轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34144808314)的报告测试使用系统默认编码读取UTF-8文件，已显式指定编码；[第二轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34145624716)在Windows及Python3.11发现同版本心跳先于状态查询归档时，已有有效租约被误判为未确认。后者通过模型、真实TCP与HTTP报警流程稳定复现；修复只复用仍有效且身份、状态版本匹配的已确认租约，不修改其截止时间。修后106项相关测试通过。以上失败均阻止了安装版打包步骤，需要后续完整CI通过才可发布。

[第三轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34146529304)的Linux两组、前端、规范和审计通过；Windows新增诊断测试在私有目录ACL设置时失败，安装包步骤仍未执行。该路径调用系统PowerShell 5，需与安装器共用隔离PowerShell 7继承模块的执行器；对应Windows测试继续作为门禁，不能以本机通过替代。

[第四轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34147661742)的实际PR合并快照为`63f408f59e77102829490af324ca82255488addb`（分支提交`ef5e31dc5b57f2ec216bc3f9dbda5e5eba6c0fe1`）。Windows Server 2025/Python3.13.15后端757通过、4项平台限定跳过，覆盖率87.69%；桌面185通过、2项跳过，工具35通过。安装器SHA256为`d4ca75aaeeadb5da00e02ff70ad4a493423b79a563048d4fd95bb293bc47a30f`，实际安装与既有smoke全部通过，包含旧资料备份及空库重装。独立工具冻结后在创建Job Object时触发TypeError，尚未安装其自身测试实例；失败记录的`cleanup_complete=true`。该构建不是合格联调发行包。

[第五轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34150511408)在打包前执行新增真实Windows Job测试，发现工具独立依赖清单未声明`pywin32`，四项测试在导入阶段失败。现将已由桌面锁固定的312版本及其哈希显式加入工具锁，保留Windows环境标记；不跳过平台测试。Job命名、正常退出与父死子清的最终结果仍需后续Windows执行确认。

[第六轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34151183177)工具41通过、1条测试失败：真实退出码、超时、正常owner退出和父死子清通过；原测试用已关闭且归零的句柄查询Job，Windows按当前进程Job返回信息，不能据此断言旧Job仍存在。该测试改为以唯一名称核对Job关闭后的内核对象不存在；仍须后续整轮通过。

额外并发审查以真实SQLite复现了人工补录覆盖已归档首滴依据的问题，且已归档标志使后续回放不会修复被覆盖的投影。补录现先预约数据库writer、刷新依据并原子提交审计，失败回滚；缓存旧对象、源写入交错、失败释放writer及邻接回归54通过。该修复防止后续覆盖，不静默改写旧档案。

[第七轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34151503455)的PR合并快照为`efcd7bb01a6e9a7d2d749e8cb7c8d781fff6cf35`（分支`024c15767167d1f0982d682e57a81c885065e826`）。Windows后端760通过/4跳过、覆盖率87.73%，桌面185通过/2跳过，工具42通过；前端124通过。实际安装、冻结工具自检与业务17项断言全部通过，10份HTML/PDF/XLSX报告已逐文件核对摘要，页面/API/console意外错误均为0。安装器SHA256为`42f3128654a7910b01c1560d735b4145395550197496bff41bba61bdce72872d`。最后清理目录发生PermissionError，`cleanup_complete=false`，因此整体状态仍为failed，发布门禁已拦截。公开证据保留在该轮诊断artifact；私有数据库和完整诊断未上传。

清理路径发现SQLite连接上下文只提交事务而不关闭连接，删除时仍持有源库及备份库句柄。现使用显式closing；真实连接回归修前失败、修后通过，工具本机40通过/6项Windows限定跳过，另保留真实Windows目录删除和备份完整性测试。失败诊断只追加固定清理阶段及数字错误码，不公开路径或异常消息。后续Windows执行结果分别见第八、九轮记录。

[第八轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34153937234)的实际PR合并快照为`a9d5d564155389ae811718d89c22396fe86f31d9`（分支`0e76723a3af69da4c0436ac5399f15ca086f55dc`）。Windows后端760通过/4跳过、覆盖率87.72%，桌面185通过/2跳过，工具46通过，包含真实Windows SQLite删除与备份完整性回归；安装冒烟及冻结工具自检通过。完整流程13项断言及7份报告通过，随后在模拟板重启后的下一实验启动等待中超时；最终登记清理也失败，`cleanup_complete=false`，没有发行包。安装器SHA256为`5b16202512560c1479b47a6a7a854b1c0092b114d3886449a1300b6ffcb930a9`。

该启动问题通过真实loopback模拟器与可控单调时钟复现：重启将设备uptime归零，但自动采样仍沿用旧boot的时间水位，导致新实验停在preparing，直到新uptime追平旧值。采样水位改为在启动和重启时重新设定；回归不重发启动，也不放宽心跳或租约期限。第八轮清理异常只有`registration / NotSpecified`证据，不能认定数据库死锁；新增固定清理子阶段、受限异常类型和数字HRESULT以区分后续原因。第八轮整体验收未通过的记录保留。

[第九轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34156913521)的实际PR合并快照为`e80a65df19c03b8967eca34cd206871b037c8774`（分支`7d57e3cb83cb4edd886bee5fe7acb877ed512337`）。Windows后端761通过/4跳过、覆盖率87.74%，桌面185通过/2跳过，工具53通过。实际NSIS/LocalService安装冒烟、冻结工具自检、独立安装版TLS与全部17项业务断言通过；10份实际HTML/PDF/XLSX报告已逐文件核对SHA256与大小，页面/API/console非预期错误均为0。`acceptance.json`记录`status=passed`、`cleanup_complete=true`；安装器SHA256为`451686b98622b23c9950aab2e8209f14b18e92b1d2caaf0878f3b7b33103f77e`，工具清单SHA256为`4c04bff4536a50e70caa7fb387fd536c91f1eaab5f98d34a6e8cf5148fda366c`。

随后`package_bench.py::verify_inventory`报`Bench bytes changed after freezing`：运行后的工具文件清单与冻结清单不一致，严格校验拦截了发行ZIP封装。因此第九轮的软件业务及清理验收通过，但封装和整轮CI未通过，不能发布该构建。原始acceptance、脱敏页面证据及10份合成报告保存在该轮诊断artifact；私有资料未上传。清单差异与后续完整构建分别核查，不以重用本轮acceptance为不同字节的工具背书。

[第十轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34159811183)的前端、规范、审计和两组Linux检查通过；Windows桌面187通过/2跳过，工具53通过。后端在`test_v2_control_runtime.py::test_queued_recipe_writes_recheck_lease_context[release-recipe_chunk]`的共享`connected`夹具初始化中失败：就绪轮询采用100次10ms等待，结束时`is_online=true`但`_ready=false`；日志同时记录收到6帧、非法帧和丢弃回调均为0。该项业务测试尚未开始执行，后端在613通过/4跳过后以1项setup错误停止，`windows-package`未执行。

共享夹具现使用10秒有界条件等待完整恢复，在启动前登记资源清理，并为新测试库显式开启DDL事务；生产通信期限不变。慢归档、部分启动失败、真实超时和取消的5项新增回归修前全部失败，修后连同原客户端与控制测试26项通过；第十一轮Windows完整检查也已通过。第九轮17项业务、10份报告及清理通过的证据仍适用于其记录的构建；第十轮未重新运行封装，也未取得工具运行后字节清单变化的具体差异，该轮未关闭交付项目。

[第十一轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34160870535)完整CI通过，安装包任务为`101863391426`。从实际checkout日志独立核对的PR合并快照为`d24150ed7e3a1bd44df083a09a2a479e5d792758`，不是PR分支提交。Windows后端766通过/4跳过，覆盖率87.70%（日志TOTAL行修约为88%）；桌面187通过/2跳过，工具53通过。前端、规范、审计及两组Linux检查全部通过。

该轮冻结工具自检及自检后清单校验、实际NSIS/LocalService安装冒烟、独立安装版TLS与全部17项业务断言通过；10份实际HTML/PDF/XLSX报告逐文件核对SHA256与大小。`acceptance.json`记录`status=passed`、`cleanup_complete=true`；页面/API/console非预期错误均为0。最终`package_bench`严格封装通过，原始acceptance、脱敏页面证据和合成报告保存在该轮诊断artifact。

实际产物下载后再次独立核验版本、完整提交、验收与工具清单关联，978个工具inventory文件全部匹配；`SHA256SUMS`严格包含4个完整且唯一的预期条目，各文件摘要均匹配。核验结果为`verified`，实际摘要如下：

- 安装器SHA256：`a6d755cd6e988e9926394317875b5223833571c8c0a718865ddd81382ca8da91`。
- 工具manifest SHA256：`d22391e92c463fbda4d14fba52877e7157c9ddf4afb2e55daf6ab6105f6195f8`。
- SmdBench ZIP SHA256：`d14a8c39ffadc00deb8e8deb90765610863deb5c2d727c51343cfc8792a2de40`。

第九轮工具清单变化在本轮未重现，具体差异和根因仍未定位，不能将本轮通过表述为该问题已明确修复。本轮证据证明上述实际字节通过了完整流程和严格核验；后续标签或重新冻结的产物必须重新验收，不能只引用本轮结果。

[第十二轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34163979969)的实际PR合并快照为`0aebf914f8464363f01ae952d80e90d27ef7fdf8`（分支`8c069e22e6bdd97c84f12bdd068bb97e0d21861d`）。六项基础检查通过，Windows后端766通过/4跳过、覆盖率87.76%，桌面187通过/2跳过、工具53通过。实际安装冒烟、冻结工具自检和自检后清单、17项业务断言、10份实际报告及归属清理再次通过；报告大小和SHA256已独立复核，三类非预期页面错误为0。

最终封装再次被严格清单拦截，此次有限差异诊断明确显示仅新增`browsers/chromium_headless_shell-1234/chrome-headless-shell-win64/debug.log`，原文件没有改变或删除。该轮没有合格发行ZIP；acceptance中的安装器摘要为`efb88693175fe00b60dcdba149cdc770f11f8eade117e53b95fbeec75b747bd7`，不能用其业务通过替代封装验收。浏览器运行日志必须定向本轮受限诊断目录，并在冻结自检中核对实际日志归属；禁止通过忽略、删除该文件或重算原清单掩盖变化。第九轮没有留下具体路径，不能把本次定位倒推成第九轮已被证实的原因。

对照 Chromium `151.0.7922.34` [Headless Shell 初始化](https://chromium.googlesource.com/chromium/src/+/refs/tags/151.0.7922.34/headless/lib/headless_content_main_delegate.cc)与[完整 Chromium 日志实现](https://chromium.googlesource.com/chromium/src/+/refs/tags/151.0.7922.34/chrome/common/logging_chrome.cc)，前者的 Windows 子进程跳过日志初始化，后者处理继承的日志句柄。工具改用已经随包分发的完整 Chromium 新 headless 模式，将参数及浏览器子进程的 `CHROME_LOG_FILE` 同时指向私有绝对路径，不修改父进程该环境变量。常态保留日志；冻结自检额外开启详细日志，要求实际写入非空文件，并在关闭浏览器后删除其受限临时目录。

本机真实 Chromium 探针确认私有日志非空、外来日志和公开证据未改变、335个浏览器文件摘要不变，自检临时目录成功移除。工具测试65通过、7项既有Windows限定跳过；严格清单没有忽略项或运行后重算。这些结果仅验证本机修复，完整冻结 Windows 场景及最终封装仍须在后续构建重新通过。

[第十三轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34166933891)实际PR合并快照为`8cd484d22d9cea10a77fcdcad5d5bea034724836`（分支`1f61418b135e0c05054943747a8091f1e12b6280`）。六项基础检查通过；Windows后端766通过/4跳过、覆盖率87.76%，桌面187通过/2跳过，工具72通过。真实冻结完整Chromium自检验证非空私有日志、临时目录清理及未改变工具清单，实际安装/备份空库重装冒烟也通过。

完整场景14项断言和8份实际报告通过，随后`fault_host_service_restart`调用`WindowsPlatform.stop()`未在现有60秒期限内取得服务Stopped状态。清理又在`service_stop`阶段收到Win32错误1061，记录`cleanup_complete=false`，未进入最终工具封装。该轮安装器摘要为`7e7d3359272505050fee3b57a699acd7a9fa9a5f74dbe4a626e17723287ee69f`；不能将冻结自检通过表述为完整业务、清理或发行包通过。后续须分别核查首次停止超时及对已停止中服务重复发送停止请求的问题，不强制杀进程或放宽验收门禁。

真实FastAPI/Uvicorn进程探针复现了一种停止阻塞：客户端未发送完整HTTP请求体时，默认无限期等待连接排空；仅关闭该测试客户端后进程才正常清理退出。[锁定版Uvicorn源码](https://github.com/Kludex/uvicorn/blob/5279296e620fad6c6839263c279ff23b4be8df32/uvicorn/server.py#L261-L294)确认请求排空默认无界。服务现将该阶段限定为15秒，随后取消未结束请求并继续原有应用清理，保留60秒SCM检查及包住整个`server.run()`的进程锁。没有保存第十三轮的具体悬挂连接，不能将此探针表述为已经精确还原该轮触发原因。

清理流程另修正StopPending状态和1061竞态：刷新确认仍在停止中或已经停止时等待同一Stopped终态；其他错误及等待超时仍失败，之后才允许删除。实际PowerShell状态序列覆盖9项边界。失败证据新增对已归属服务日志尾部的有界读取，只公开固定退出阶段；真实服务格式日志验证通过，原日志仍不上传。工具本机合并回归79通过、7项既有Windows限定跳过；后续Windows完整流程仍须重新验收。

两项真实进程回归使用生产服务工厂和实际FastAPI生命周期：半包请求在排空期限后正常退出；暂停未提交的命令回执写入时，进程仍持有外层互斥锁，最终保留原始操作ID、固定报文ID、序号、摘要和请求。未提交回执回滚后保持`unknown`，不补写成功；新进程使用受控线协议结果查询原操作，确认后重复提交同ID也不再发送第二条控制命令。退出后SQLite可重新写入。两项通过，既有数据库取消回归10项通过；受控协议响应不替代下一轮安装版真实TLS场景。

本机合并后的桌面回归165通过、26项平台/打包工具限定跳过；文档链接与格式检查通过。下一轮Windows必须实际执行对应平台用例，不能把本机跳过视为通过。

## 合并前完整复验与主线复验

[第十四轮](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34170790263)七项必要检查全部通过。分支为`4448f5b116ebe0ac6be3cc197959e81841f224c5`，实际PR构建快照为`053bdf07c6241dc5d8b975b6370d63ee00813d2d`。Windows后端766通过/4跳过、覆盖率87.79%，桌面189通过/2跳过，工具86通过。实际安装、备份空库重装、17项业务/故障断言、10份报告和归属清理均通过，包括服务重启和未知运行恢复；页面/API/控制台非预期错误均为0，冻结自检前后及最终严格清单与ZIP封装通过。

下载后的验收摘要与构建清单一致，10份实际报告大小和SHA256逐一匹配。安装器摘要为`c027819c407dd7de052f45c06dc6e4a739866a3e8d7dd05557ee1beda791fff9`，工具清单摘要为`4602f704b8526c53b829268fece5f25c4da33e33275f2ab5ce4cd3f16a90208f`。实际PDF为6517字节、SHA256 `0174f89eb42589f26f5315ca76a9e8531b252b18c2aa359240ed10b6a4b1a506`，文本门禁、字体映射和两页目视复核通过。此处记录PR构建结果，最终发行字节仍须由标签流水线重新验收。

PR #75合入`main@9cc47c0d57135ba1ddde2b6a6d9de2a5ebd90d04`后，[主线检查](https://github.com/kevinalliswell/smd-web-hmi/actions/runs/34173118792)在Windows后端的`test_concurrent_allocators_keep_unique_sequences`遇到SQLite `database is locked`，发生于操作意图的第一条序号写入保留语句。其余五项基础检查通过，Windows安装包阶段按门禁跳过，尚未创建RC.5标签。日志不包含当时锁拥有者或完整调度轨迹，不能推断发生序号重复或主机重复发送命令。

受控慢提交探针保持5000ms锁等待不变：DELETE加独立写锁的12路请求有1项锁超时，WAL加独立写锁有2项，WAL加生产共享写锁则12项全部成功、序号1至12连续唯一。探针说明独立写入者的有限等待会受竞争影响，WAL本身不消除这一问题；人为慢提交不等于精确重现该CI锁调度。正式服务已有WAL连接配置和共享写入协调器，本次不改变生产代码、等待期限或发送策略。测试现分别验证12路生产协调命令及两独立分配器的受控事务竞争：两个真实SQLite连接交错保留写入权，释放首个事务前没有命令发送；最终序号1、2及身份水位、操作请求和回执摘要一致持久化。刻意删除写入保留语句的仓库外反例触发重复序号唯一约束冲突，正常代码18项操作测试通过；关联取消、元数据并发、命令生命周期及数据库回归共45项通过。并发任务全部排空后才断言结果，失败不遗留数据库工作。完整Windows结果仍以修订后的CI为准。

## PDF 导出目视核查

第十一轮实际 Windows 报告（SHA256 `09e7d7498b9f3b6d0c10aa0efb62d53fafe6b5de434909d58c78d620f504c71d`）经 Poppler 渲染后发现三处温度差减号和页尾中点显示为方框，且“结果指标”标题孤立在前页。独立核对 `UniGB-UCS2-H` 字体映射确认 U+2212、U+00B7 无对应 CID；该报告其他非 ASCII 字符均有映射。`Heading2` 默认不保持与后文同页。这些问题未被原有文本内容断言识别，原 CI 通过记录不代表版式没有问题。

修正仅限 PDF 渲染：展示标签采用 ASCII 减号、页尾使用竖线，标题与后续表格保持同页。用同一合成案例的实际显示值重绘后仍为两页，减号、页尾和中文正常，标题与首个指标同页；除上述显示字符外，所有提取文本和数值保持一致，未改变共享指标、HTML 或 XLSX。两人独立目视及字体映射复核通过，既有报告生成与恢复报告回归 32 项通过。独立 SmdBench 的实际 PDF 下载验收已增加三个 ASCII 温差标签、页尾分隔符及标题/首指标同页检查；旧验收器误放行的 11 项负例现被拒绝，含原有指标用例的定向检查 14 项通过。实际旧 Windows PDF 被拒绝，修正后重绘文件通过。最终 Windows 与标签构建须重新检查实际导出的 PDF，不以本机重绘替代发行字节验收。

第十三轮实际 Windows PDF（6530字节、SHA256 `c5a8d6c673a2ddd965754cc4504fe37f2254ac4a653edf242472dc19715f4362`）已通过文本门禁、字体映射及两页渲染目视核查：减号、页尾和中文正常，“结果指标”与首项同在第二页，未见缺字或裁切。该轮8份报告的大小和摘要均匹配；这是报告专项证据，服务停止失败使整轮仍未通过。

## 测试方法与证据限制

- 模拟器拥有独立SQLite和受限配对资料，通过真实TLS与安装后的后台连接；页面操作不修改后台内部状态或数据库。
- 驱动以私有JSONL动作注入阶段和故障，不加速网络时钟。报告中的模拟数据保留`not_certified`。
- 数据预期使用固定人工核算样本，不用被测指标计算器生成预期。
- `acceptance.json`绑定版本、SHA、场景、断言和清理结果。发布门禁要求全部固定场景通过，不接受跳过。
- 公开证据仅包含白名单摘要、必要截图和合成实验导出，不包含真实配置、数据库、PSK、口令或会话凭据。
- CI先完成原安装冒烟及其清理，再运行工具自己的完整安装流程。两次安装增加执行时间，避免工具接管另一流程的对象。
- 生产程序先冻结，随后才安装独立工具依赖；工具冻结后的进程/浏览器自检前置于较长的安装冒烟，尽早发现冻结环境问题，完整业务流程仍在两者之后执行。

实施依据：[ADR-010](../decisions/ADR-010-installed-hostcomm-loop.md)、[任务清单](../../tasks/todo.md)。

## 故障覆盖层次

| 验证层次 | 用例与证据入口 |
|---|---|
| 传输/业务软件回归 | [租约代次](../../backend/tests/test_v2_lease_context.py)、[时限与单帧非法ACK](../../backend/tests/test_v2_control_timing.py)、[写锁与释放竞态](../../backend/tests/test_v2_control_runtime.py)、[真实TLS上传中断](../../backend/tests/test_v2_tls_application.py) |
| 恢复与数据软件回归 | [审查冲突/去重/工作进程恢复](../../backend/tests/test_v2_run_recovery.py)、[旧库迁移](../../backend/tests/test_run_recovery_migration.py)、[报告生成期版本漂移](../../backend/tests/test_recovery_reports.py)、[当前运行与停止归属](../../backend/tests/test_v2_command_lifecycle.py) |
| 安装版实际场景 | [SmdBench正常流程](../../tools/bench/smd_bench/scenarios.py)和[故障流程](../../tools/bench/smd_bench/faults.py)；结果须读取对应构建acceptance.json，软件单元测试不冒充安装后的场景执行 |

公开安装版证据还记录协议/design/场景版本、实际安装器SHA256及工具清单SHA256。相同源码提交的不同冻结字节不能共用验收结论。
