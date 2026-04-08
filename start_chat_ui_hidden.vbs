Set shell = CreateObject("WScript.Shell")
scriptPath = CreateObject("Scripting.FileSystemObject").BuildPath(CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName), "start_chat_ui.bat")
shell.Run "cmd.exe /c """ & scriptPath & """", 1, False
