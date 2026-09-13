# -*- coding: utf-8 -*-
Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "TextFunc.nsh"
!ifndef VERSION
  !error "VERSION must match source versions"
!endif
!ifndef PAYLOAD
  !error "PAYLOAD must be a verified complete offline bundle"
!endif
Name "SMD HMI ${VERSION}"
OutFile "${OUTPUT}"
InstallDir "$PROGRAMFILES64\SmdHmi"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
Var StagePath
Var UpdaterOptions
Var InstallerMutex
Var RegisteredInstall
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE DirectoryPre
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  System::Call 'kernel32::CreateMutexW(p0,i0,w "Global\SmdHmi.Installer") p.r0 ?e'
  Pop $1
  StrCpy $InstallerMutex $0
  ${If} $InstallerMutex == 0
    MessageBox MB_ICONSTOP "无法取得安装互斥锁，安装尚未开始。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} $1 == 183
    MessageBox MB_ICONSTOP "另一个 SMD HMI 安装器正在运行，请等待它结束。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "仅支持 Windows 10/11 x64。" /SD IDOK
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "最低要求 Windows 10 x64。" /SD IDOK
    Abort
  ${EndIf}
  SetRegView 64
  ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" "Release"
  ${If} $0 < 528040
    MessageBox MB_ICONSTOP "需要 Windows 自带的 .NET Framework 4.8；请先完成离线系统组件安装。" /SD IDOK
    Abort
  ${EndIf}
  ReadRegStr $1 HKLM "Software\SmdHmi" "InstallDir"
  StrCpy $RegisteredInstall $1
  ${If} $1 != ""
    StrCpy $INSTDIR $1
  ${EndIf}
FunctionEnd

Function un.onInit
  System::Call 'kernel32::CreateMutexW(p0,i0,w "Global\SmdHmi.Installer") p.r0 ?e'
  Pop $1
  StrCpy $InstallerMutex $0
  ${If} $1 == 183
  ${OrIf} $InstallerMutex == 0
    MessageBox MB_ICONSTOP "另一个安装或卸载正在运行，请稍后重试。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd

Function DirectoryPre
  ${If} $RegisteredInstall != ""
    StrCpy $INSTDIR $RegisteredInstall
    Abort
  ${EndIf}
FunctionEnd

Function BuildOptions
  StrCpy $UpdaterOptions ""
  IfSilent 0 +2
    StrCpy $UpdaterOptions "--non-interactive"
  ${GetParameters} $R0
  StrCpy $R1 ""
  ${GetOptions} $R0 "/PHYSICALSHUTDOWN=" $R1
  ${If} $R1 == "1"
    StrCpy $UpdaterOptions "$UpdaterOptions --confirm-physical-shutdown"
  ${EndIf}
  StrCpy $R1 ""
  ${GetOptions} $R0 "/CONFIRMRECOVERY=" $R1
  ${If} $R1 == "1"
    StrCpy $UpdaterOptions "$UpdaterOptions --confirm-recovery"
  ${EndIf}
FunctionEnd

Section "SMD HMI" SEC_MAIN
  SetShellVarContext all
  InitPluginsDir
  Call BuildOptions
  ${If} $RegisteredInstall != ""
    StrCpy $INSTDIR $RegisteredInstall
  ${EndIf}
  # Run a separate updater bootstrap. The complete payload is extracted once,
  # onto the program volume, and then promoted by rename.
  SetOutPath "$PLUGINSDIR\bootstrap"
  File /r "${PAYLOAD}\SmdUpdate\*"
  DetailPrint "检查已有安装和磁盘空间；数据库、配置和密码将保留。"
  ExecWait '"$PLUGINSDIR\bootstrap\SmdUpdate.exe" --preflight --payload-bytes ${PAYLOAD_BYTES} --stage-output "$PLUGINSDIR\stage.txt" --install "$INSTDIR" $UpdaterOptions' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "安装预检未通过（退出码：$0），原版尚未停止。请查看 ProgramData\SmdHmi\logs\updater.log 的空间或路径原因，再运行安装器。" /SD IDOK
    SetErrorLevel $0
    Abort
  ${EndIf}
  FileOpen $1 "$PLUGINSDIR\stage.txt" r
  FileReadUTF16LE $1 $StagePath
  FileClose $1
  ${TrimNewLines} $StagePath $StagePath
  ${If} $StagePath == ""
    SetErrorLevel 1
    Abort
  ${EndIf}
  SetOutPath "$StagePath"
  File /r "${PAYLOAD}\*"
  # Current directory must not hold the payload directory open during promotion.
  SetOutPath "$PLUGINSDIR"
  ClearErrors
  DetailPrint "自动准备维护、备份数据并更新程序。修复同版前请关闭 SMD 桌面窗口。"
  ExecWait '"$PLUGINSDIR\bootstrap\SmdUpdate.exe" --package "$StagePath" --install "$INSTDIR" $UpdaterOptions' $0
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "无法启动安装更新器 SmdUpdate.exe。请核对安装包完整性及 Windows 的应用拦截记录。此时可能尚未生成 updater.log。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} $0 != 0
    ${If} $0 == 20
      MessageBox MB_ICONSTOP "设备状态未知，需要在现场确认安全停机后交互运行本安装器。无需登录旧版或填写版本号。$\r$\n现有数据和程序已保留。" /SD IDOK
    ${Else}
      MessageBox MB_ICONSTOP "安装或升级未完成（更新器退出码：$0）。$\r$\n请以管理员打开 %ProgramData%\SmdHmi\logs\updater.log 查看具体原因；若设置了 SMD_DATA_ROOT，请查看该数据目录下的 logs\updater.log。$\r$\n请保留数据、配置及 updates 中的恢复记录，按日志判断后再重试或恢复。" /SD IDOK
    ${EndIf}
    SetErrorLevel $0
    Abort
  ${EndIf}
  CreateDirectory "$SMPROGRAMS\SMD HMI"
  CreateShortCut "$SMPROGRAMS\SMD HMI\SMD HMI.lnk" "$INSTDIR\versions\${VERSION}\SmdDesktop\SmdDesktop.exe"
  WriteRegStr HKLM "Software\SmdHmi" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\SmdHmi" "Version" "${VERSION}"
  WriteRegDWORD HKLM "Software\SmdHmi" "UninstallBackendComplete" 0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi" "DisplayName" "SMD HMI"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  # 不从提权安装器启动桌面；用户从开始菜单以普通权限打开。
SectionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext all
  StrCpy $UpdaterOptions ""
  IfSilent 0 +2
    StrCpy $UpdaterOptions "--non-interactive"
  ${GetParameters} $R0
  StrCpy $R1 ""
  ${GetOptions} $R0 "/PHYSICALSHUTDOWN=" $R1
  ${If} $R1 == "1"
    StrCpy $UpdaterOptions "$UpdaterOptions --confirm-physical-shutdown"
  ${EndIf}
  ReadRegDWORD $2 HKLM "Software\SmdHmi" "UninstallBackendComplete"
  ${If} $2 != 1
    ReadRegStr $1 HKLM "Software\SmdHmi" "Version"
    ClearErrors
    ExecWait '"$INSTDIR\versions\$1\SmdUpdate\SmdUpdate.exe" --uninstall --install "$INSTDIR" $UpdaterOptions' $0
    ${If} ${Errors}
      MessageBox MB_ICONSTOP "无法启动卸载更新器 SmdUpdate.exe，卸载未完成。请核对程序文件及 Windows 的应用拦截记录。" /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "卸载未完成（更新器退出码：$0）。请确认实验已结束，再运行卸载程序；无需登录软件或填写版本号。详细原因见 ProgramData\SmdHmi\logs\updater.log。" /SD IDOK
      SetErrorLevel $0
      Abort
    ${EndIf}
    # 保留完成标记，版本文件删除中断后不再依赖可能已删除的更新器。
    ClearErrors
    WriteRegDWORD HKLM "Software\SmdHmi" "UninstallBackendComplete" 1
    ${If} ${Errors}
      MessageBox MB_ICONSTOP "无法保存卸载恢复标记，保留程序文件，请修复注册表访问后重试。" /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
  ${EndIf}
  ClearErrors
  IfFileExists "$INSTDIR\versions\." 0 versions_removed
  RMDir /r "$INSTDIR\versions"
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "后台已安全卸载，程序文件仍被占用。请关闭桌面窗口后再次卸载；实验数据保持不变。" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  versions_removed:
  RMDir /r "$SMPROGRAMS\SMD HMI"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi"
  DeleteRegKey HKLM "Software\SmdHmi"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
