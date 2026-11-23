' Runs probe.ps1 with no window (the task nknu-machine-probe, every 30 minutes), as unload_strata.vbs does.
Dim here : here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & here & "\probe.ps1""", 0, True
