@echo off
setlocal
rem Χρησιμοποιεί την εγκατάσταση του midirooms.cmd (ίδιος φάκελος)· αν δεν έχει γίνει, την κάνει πρώτα.
if not exist "%~dp0.venv\.midirooms_ready" call "%~dp0midirooms.cmd" --setup || exit /b 1
"%~dp0.venv\Scripts\python.exe" "%~dp0midirooms_p2p.py" %*
exit /b %errorlevel%
