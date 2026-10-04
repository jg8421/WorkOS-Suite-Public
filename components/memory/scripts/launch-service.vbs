Option Explicit

Dim shell, fileSystem, scriptDirectory, installRoot, serviceScript, powershellPath, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
installRoot = fileSystem.GetParentFolderName(scriptDirectory)
serviceScript = fileSystem.BuildPath(scriptDirectory, "start-service.ps1")
powershellPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = """" & powershellPath & """ -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & serviceScript & """ -InstallRoot """ & installRoot & """ -Foreground -Automatic"
shell.Run command, 0, False
