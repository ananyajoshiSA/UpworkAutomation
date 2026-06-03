' ===========================================================================
'  Start UpworkProposalStrategist.vbs
'
'  THE thing an end user double-clicks. It opens the app with NO console
'  window. On the very first run (before setup has completed) it shows the
'  one-time setup progress window so the user gets feedback; after that it
'  launches silently and the app window appears in a few seconds.
'
'  It just hands off to scripts\run.bat. If this .vbs is ever blocked by
'  antivirus/policy, double-clicking scripts\run.bat does the same thing.
' ===========================================================================
Option Explicit

Dim fso, sh, base, marker, style, q
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

' Folder this script lives in = the project root. Works with spaces in the path
' and after the whole folder is moved/renamed.
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

marker = base & "\runtime\.deps_installed"
q = Chr(34)   ' double-quote, to wrap the path safely

' First run (no marker yet) -> show the setup window (style 1) so the user sees
' "one-time setup" progress. Afterwards -> hidden (style 0) for a clean launch.
If fso.FileExists(marker) Then
    style = 0
Else
    style = 1
End If

' Run scripts\run.bat. Third arg False = don't wait (the window stays
' independent; closing the app later doesn't depend on this script).
sh.Run q & base & "\scripts\run.bat" & q, style, False
