@echo off
setlocal
rem Χρησιμοποιεί την εγκατάσταση του midirooms.cmd (ίδιος φάκελος)· αν δεν έχει γίνει, την κάνει πρώτα.
if not exist "%~dp0.venv\.midirooms_ready" call "%~dp0midirooms.cmd" --setup || exit /b 1
"%~dp0.venv\Scripts\python.exe" "%~dp0midi_player_recorder.py" %*
exit /b %errorlevel%
