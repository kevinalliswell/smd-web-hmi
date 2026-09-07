# 固件文档与SmdHmi对齐审查

日期：2026-09-07。文档修订 `2.0-doc.2`；线上仍为2.0/design.1，应用仍为rc.4。用户要求先审查docs、与主机实现匹配、更新已有资料并补交接文档。

## 范围和来源

源码基线为 `c45046e01f869b214cacfcbc75e0b6ddf2d708bb`。审查HostComm三份协议规范、运行/固件路线、板卡/profile、机器契约、历史1.0入口及根文档/任务状态；实现对照包括transport/security/client/projection、v2_contract模型/codec、operations/source_logs/archive、模拟器、桌面配对与对应测试。审查相关运行代码、模型和Schema/向量与 `v0.3.0-rc.4` 无差异。

新增[固件交接](../hostcomm/v2/firmware-handoff.md)、[版本记录](../hostcomm/v2/revisions.md)、[兼容核对](../hostcomm/v2/compatibility.md)。不将历史ADR或旧实验记录改写成当前实现，也不修改线上模型、Schema、向量、应用版本、配对材料或设备配置。

## 结果及仍需完成的工作

说明性差异已补齐：主机运行已实现、固定握手/能力字段、日志各水位、配方校验与激活/回读、板端自主阶段、Windows同机模拟器和真实固件的验收边界。已有板卡照片足以建立资料入口，不足以关闭FW-01。

主机仍有待对齐事项：陌生运行raw-only后的受控归档恢复、心跳租约字段联合检查、hello_ack.control_ready语义、心跳与重连计时口径；分别在ALIGN-04—07和HOST-2006—2008登记。Windows安装包到TLS模拟器的完整业务与独立轨迹驱动列HOST-2009。文档已反映这些差异，不宣称代码缺口已修复。

用户已确认本次rc.4安装成功，原始诊断与范围见[安装记录](2026-09-06-windows-installer-errors.md#2026-09-07-用户现场结果)。该反馈不关闭HostComm、空库、正常升级或真机验收。

## 可复现检查

在仓库根目录，使用现有Python3.13开发环境及Node24运行：

```sh
python scripts/check_docs.py
python scripts/check_hostcomm_contract.py
node contracts/hostcomm/v2/verify-js.mjs
git diff --check
```

模拟器CLI参数另以 `python -m app.hostcomm.v2_simulator --help` 在backend目录核对。runtime中的Windows启动模板按源码整理，未在用户Windows上执行，不作为TLS握手或完整实验测试证据。

本次本地结果（macOS，Python 3.13、Node 24）：

| 检查 | 实际结果 |
|---|---|
| 文档导航与链接 | 通过：61个Markdown文件、443个本地链接 |
| Python契约与向量 | 通过：26种消息、37个有效向量、21个无效向量 |
| JavaScript规范字节 | 通过：37个消息；配方SHA-256为`acf41dcfb17a1e0b1e57344d498db7146ecc92df80b17ce058f99ec0a248862f` |
| 源码基线对照 | 审查涉及的HostComm运行代码、服务、模型、Schema/向量与rc.4标签无差异 |
| 差异格式 | `git diff --check`通过；仅修改Markdown |
| 独立交叉审查 | 分别核对报文、状态/配方和运行/路线；修正重复操作语义、任务依赖循环、H3逻辑与H4物理集成的顺序、握手字段名称及过时安装状态说明。PR自动审查另指出响应匹配漏写关联error例外，已按传输实现及现有远端错误回归补齐；实现缺口保留为ALIGN/HOST任务 |

本次PR记录最终提交、上述检查结果和独立审查结论；仓库要求的CI仍按原必要检查执行。只把实际运行成功的检查标为通过，不用旧测试数量代替本次证据。固件人员交接记录尚待实际负责人确认，本文不是双方已签字的冻结单。
