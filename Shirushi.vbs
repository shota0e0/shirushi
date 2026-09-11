Option Explicit

Dim shell, fileSystem, projectRoot, pythonwPath, launcherPath, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonwPath = fileSystem.BuildPath(projectRoot, ".venv-py312\Scripts\pythonw.exe")
launcherPath = fileSystem.BuildPath(projectRoot, "scripts\creator_gui.py")

If Not fileSystem.FileExists(pythonwPath) Then
    MsgBox "Shirushi runtime was not found.", vbCritical, "Shirushi"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectRoot
command = """" & pythonwPath & """ """ & launcherPath & """"
shell.Run command, 1, False
