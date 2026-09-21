# NSIS 3.11 便携版(自托管 Runner 专用)

`nsis-3.11.zip` 是 NSIS 官方 zip 发行版原件,随仓库下发,供网络受限的
Windows 自托管 Runner 在 CI 中免安装使用(解包到 runner 自有工具目录,
经 `SMD_MAKENSIS` 传给 `scripts/release/build-windows.ps1`,不做系统级
安装)。托管 Runner 不使用本文件,仍走镜像预装的标准路径 NSIS。

## 来源与完整性

- 官方下载:`https://downloads.sourceforge.net/project/nsis/NSIS%203/3.11/nsis-3.11.zip`
- SHA256:`c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1`
- 大小:2361546 字节
- 交叉核验:上述官方下载与 Chocolatey `nsis.portable 3.11.0` 包内嵌的
  `tools/nsis-3.11.zip` 逐字节一致(同一 SHA256)。
- CI 使用前会重新校验 SHA256(见 `.github/workflows/checks.yml` 的
  "Provision portable NSIS" 步骤),不匹配即失败。

## 升级方式

下载新版官方 zip,至少两个独立渠道核验同一 SHA256 后替换本文件,并同步
更新本 README 与 checks.yml 中的固定哈希。
