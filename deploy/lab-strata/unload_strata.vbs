' Runs unload_strata.ps1 with no window (same way as C:\nknu-crawl\nightly_hidden.vbs). Task Scheduler's action is
'   wscript.exe //B //Nologo C:\nknu-strata\unload_strata.vbs
' Interactive only: the Strata desktop app runs in the logged-on session anyway. Window style 0 is hidden, True waits,
' and the task's last result is the script's exit code. ASCII only.
Dim sh, rc
Set sh = CreateObject("WScript.Shell")
rc = sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\nknu-strata\unload_strata.ps1""", 0, True)
WScript.Quit rc
