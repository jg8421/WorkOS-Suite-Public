Option Explicit

Dim shell, fileSystem, scriptDirectory, installRoot, trayScript, powershellPath, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fileSystem.GetParentFolderName(WScript.ScriptFullName)
installRoot = fileSystem.GetParentFolderName(scriptDirectory)
trayScript = fileSystem.BuildPath(scriptDirectory, "tray.ps1")
powershellPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
command = """" & powershellPath & """ -NoProfile -NonInteractive -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & trayScript & """ -InstallRoot """ & installRoot & """"
shell.Run command, 0, False
