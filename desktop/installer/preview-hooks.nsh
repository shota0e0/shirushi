; v0.2 Preview only. Per-user ownership is NOT a no-admin prerequisite promise.
; No vendor payload, network request, automatic retry/reboot, or elevation.
!include "LogicLib.nsh"
!include "x64.nsh"
!include "${__FILEDIR__}\explorer-generated.nsh"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

!macro NSIS_HOOK_PREINSTALL
  ${IfNot} ${RunningX64}
    Abort "Shirushi Preview requires Windows x64."
  ${EndIf}
  SetRegView 64
  ClearErrors
  ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
  ${If} ${Errors}
  ${OrIf} $0 != 1
    Abort "VC Runtime x64 is not proven installed. Install the approved Microsoft prerequisite separately; then start a fresh session."
  ${EndIf}
  ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Major"
  ReadRegDWORD $1 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Minor"
  ReadRegDWORD $2 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Bld"
  ReadRegDWORD $3 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Rbld"
  ${If} ${Errors}
    Abort "VC Runtime version is unknown; installation stopped."
  ${EndIf}
  ${If} $0 < 0
  ${OrIf} $1 < 0
  ${OrIf} $2 < 0
  ${OrIf} $3 < 0
    Abort "VC Runtime version fields are invalid; installation stopped."
  ${EndIf}
  ${If} $0 < 14
    Abort "VC Runtime x64 14.51.36247.0 or later is required."
  ${ElseIf} $0 == 14
    ${If} $1 < 51
      Abort "VC Runtime x64 14.51.36247.0 or later is required."
    ${ElseIf} $1 == 51
      ${If} $2 < 36247
        Abort "VC Runtime x64 14.51.36247.0 or later is required."
      ${EndIf}
    ${EndIf}
  ${EndIf}
  Call ShirushiAssertRegistration
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Exact native readiness command exits before any WebView/UI/helper startup.
  ; An API/architecture failure never becomes READY from registry presence.
  ClearErrors
  ExecWait '"$INSTDIR\shirushi-desktop.exe" --shirushi-prerequisite-check' $0
  ${If} ${Errors}
  ${OrIf} $0 != 0
    Abort "Compatible WebView2 readiness was not proven. No Shirushi launch or Explorer registration. Prerequisite action requires a separate approved Microsoft installation."
  ${EndIf}
  !insertmacro SHIRUSHI_REGISTER_EXPLORER
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Preserve user profile even if a future stock template offers broad cleanup.
  StrCpy $DeleteAppDataCheckboxState 0
  !insertmacro SHIRUSHI_UNREGISTER_EXPLORER
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Tauri removes its exact installed files. No user images/profile/shared
  ; VC/WebView2 removal, machine-wide registry cleanup, or recursive wildcard.
!macroend
