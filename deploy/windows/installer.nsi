Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "LogicLib.nsh"
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
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "仅支持 Windows 10/11 x64。"
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "最低要求 Windows 10 x64。"
    Abort
  ${EndIf}
  SetRegView 64
  ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" "Release"
  ${If} $0 < 528040
    MessageBox MB_ICONSTOP "需要 Windows 自带的 .NET Framework 4.8；请先完成离线系统组件安装。"
    Abort
  ${EndIf}
  ReadRegStr $1 HKLM "Software\SmdHmi" "InstallDir"
  ${If} $1 != ""
    StrCpy $INSTDIR $1
  ${EndIf}
FunctionEnd

Section "SMD HMI" SEC_MAIN
  SetShellVarContext all
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\payload"
  File /r "${PAYLOAD}\*"
  ExecWait '"$PLUGINSDIR\payload\SmdUpdate\SmdUpdate.exe" --package "$PLUGINSDIR\payload" --install "$INSTDIR"' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "安装或升级未完成。旧数据将保留。升级前请用管理员在系统维护中准备 ${VERSION}；查看 ProgramData\SmdHmi\updates 日志，并在管理员终端执行恢复程序。"
    SetErrorLevel $0
    Abort
  ${EndIf}
  CreateDirectory "$SMPROGRAMS\SMD HMI"
  CreateShortCut "$SMPROGRAMS\SMD HMI\SMD HMI.lnk" "$INSTDIR\versions\${VERSION}\SmdDesktop\SmdDesktop.exe"
  WriteRegStr HKLM "Software\SmdHmi" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\SmdHmi" "Version" "${VERSION}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi" "DisplayName" "SMD HMI"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  # 不从提权安装器启动桌面；用户从开始菜单以普通权限打开。
SectionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext all
  ReadRegStr $1 HKLM "Software\SmdHmi" "Version"
  ExecWait '"$INSTDIR\versions\$1\SmdUpdate\SmdUpdate.exe" --uninstall --install "$INSTDIR"' $0
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "卸载未完成。请先在系统维护中准备当前版本，确认没有未闭合实验。"
    SetErrorLevel $0
    Abort
  ${EndIf}
  RMDir /r "$SMPROGRAMS\SMD HMI"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\SmdHmi"
  DeleteRegKey HKLM "Software\SmdHmi"
  RMDir /r "$INSTDIR\versions"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
