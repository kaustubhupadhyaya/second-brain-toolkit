' Starts the Gemini chat sync fully hidden - no console flash.
' Invoked hourly by the "SecondBrain-Gemini-HourlySync" scheduled task.
Dim q, cmd, sh
q = Chr(34)
cmd = q & "E:\SecondBrain\_tools\run_sync_gemini.cmd" & q
Set sh = CreateObject("WScript.Shell")
sh.Run cmd, 0, True
