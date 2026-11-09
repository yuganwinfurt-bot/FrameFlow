Unicode True
!include "MUI2.nsh"
!include "x64.nsh"

!define APP "帧转 FrameFlow"
!define VERSION "1.1.0"
!define SETTINGS_KEY "Software\FrameFlow\Installer"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\FrameFlow.Setup"
!define START_FOLDER "$SMPROGRAMS\FrameFlow"
!define DESKTOP_LINK "$DESKTOP\帧转 FrameFlow.lnk"

Name "${APP} ${VERSION}"
Caption "${APP} 安装向导"
OutFile "${SETUP_OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\FrameFlow"
InstallDirRegKey HKCU "${SETTINGS_KEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APP}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=2052 "FileDescription" "FrameFlow Windows x64 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${VERSION}"

!define MUI_ABORTWARNING
!define MUI_ICON "FrameFlow.ico"
!define MUI_WELCOMEPAGE_TITLE "欢迎安装 ${APP}"
!define MUI_WELCOMEPAGE_TEXT "此向导将安装 FrameFlow 及其所需的运行组件。点击「下一步」继续。"
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE CheckDestination
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\FrameFlow.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 ${APP}"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要支持 x64 程序的 Windows。"
    Abort
  ${EndIf}
FunctionEnd

Function CheckDestination
  ReadRegStr $0 HKCU "${SETTINGS_KEY}" "InstallDir"
  StrCmp $0 $INSTDIR allowed
  ClearErrors
  FindFirst $1 $2 "$INSTDIR\*.*"
  IfErrors allowed
  inspect:
  StrCmp $2 "." next
  StrCmp $2 ".." next
  FindClose $1
  MessageBox MB_OK|MB_ICONEXCLAMATION "请选择一个空文件夹，或使用已有的 FrameFlow 安装目录。" /SD IDOK
  Abort
  next:
  FindNext $1 $2
  IfErrors closeSearch inspect
  closeSearch:
  FindClose $1
  allowed:
FunctionEnd

Section "FrameFlow 主程序" Main
  SectionIn RO
  SetShellVarContext current
  SetRegView 64
  Call CheckDestination
  ReadRegStr $0 HKCU "${SETTINGS_KEY}" "InstallDir"
  StrCmp $0 $INSTDIR 0 copyFiles
  ReadRegStr $0 HKCU "${UNINSTALL_KEY}" "DisplayVersion"
  StrCmp $0 "1.0.0" 0 copyFiles
  IfFileExists "$INSTDIR\_internal\icuuc.dll" 0 oldData
  ClearErrors
  Delete "$INSTDIR\_internal\icuuc.dll"
  IfErrors oldLocked
  oldData:
  IfFileExists "$INSTDIR\_internal\icudt78.dll" 0 copyFiles
  ClearErrors
  Delete "$INSTDIR\_internal\icudt78.dll"
  IfErrors oldLocked copyFiles
  oldLocked:
  MessageBox MB_OK|MB_ICONSTOP "无法更新旧版运行库。请关闭 FrameFlow 后重试。" /SD IDOK
  SetErrorLevel 2
  Abort
  copyFiles:
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "${START_FOLDER}"
  CreateShortcut "${START_FOLDER}\FrameFlow.lnk" "$INSTDIR\FrameFlow.exe"
  CreateShortcut "${START_FOLDER}\卸载 FrameFlow.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${SETTINGS_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\FrameFlow.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "https://github.com/yuganwinfurt-bot/FrameFlow"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section /o "创建桌面快捷方式" Desktop
  IfFileExists "${DESKTOP_LINK}" done
  CreateShortcut "${DESKTOP_LINK}" "$INSTDIR\FrameFlow.exe"
  WriteRegDWORD HKCU "${SETTINGS_KEY}" "DesktopShortcut" 1
  done:
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
FunctionEnd

Section "Uninstall"
  ReadRegDWORD $0 HKCU "${SETTINGS_KEY}" "DesktopShortcut"
  StrCmp $0 1 0 noDesktop
  Delete "${DESKTOP_LINK}"
  noDesktop:
  Delete "${START_FOLDER}\FrameFlow.lnk"
  Delete "${START_FOLDER}\卸载 FrameFlow.lnk"
  RMDir "${START_FOLDER}"
  !include "${UNINSTALL_MANIFEST}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "${SETTINGS_KEY}"
  DeleteRegKey /ifempty HKCU "Software\FrameFlow"
SectionEnd
