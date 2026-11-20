' Runs the nightly update with no window (2026-11-17: at 02:0x a person closed the console the task showed, taking it
' for a leftover, and the night stopped before the ingest call; exit 0xC000013A). Task Scheduler's action is
'   wscript.exe //B //Nologo C:\nknu-crawl\nightly_hidden.vbs
' Still interactive only (someone logged on: Docker Desktop needs it). Window style 0 is hidden, True waits, and the
' task's last result is the .cmd's exit code. An argument names another .cmd (the install's trial with a harmless one).
' ASCII only.
Dim sh, target, rc
target = "C:\nknu-crawl\nightly_update.cmd"
If WScript.Arguments.Count > 0 Then target = WScript.Arguments(0)
Set sh = CreateObject("WScript.Shell")
rc = sh.Run("cmd.exe /c """ & target & """", 0, True)
WScript.Quit rc
