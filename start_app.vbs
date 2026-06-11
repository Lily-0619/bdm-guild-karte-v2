Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = projectDir

comspec = shell.ExpandEnvironmentStrings("%ComSpec%")

command = comspec & " /d /c ""cd /d """ & projectDir & """ && python src\app.py"""
exitCode = shell.Run(command, 0, True)

If exitCode = 0 Then
    WScript.Quit 0
End If

command = comspec & " /d /c ""cd /d """ & projectDir & """ && py src\app.py"""
exitCode = shell.Run(command, 0, True)

If exitCode <> 0 Then
    MsgBox "BDM Guild Karte Tool could not start." & vbCrLf & _
           "Please try start_app_debug.bat once.", _
           vbExclamation, "Startup error"
End If
