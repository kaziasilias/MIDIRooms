@echo off
chcp 65001 >nul
setlocal
rem ==== midirooms.cmd ====
rem Πρώτη εκτέλεση: φτιάχνει .venv, εγκαθιστά τα πακέτα και προσθέτει τον φάκελο στο PATH.
rem Μετά ανοίγει την εφαρμογή. Με παράμετρο --setup κάνει μόνο την εγκατάσταση.
rem Τα midirooms_p2p.cmd και midiplayerandrecorder.cmd του ίδιου φακέλου χρησιμοποιούν την ίδια εγκατάσταση.

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"
set "VPY=%REPO%\.venv\Scripts\python.exe"

if not exist "%REPO%\midirooms.py" (
    echo Δεν βρέθηκε το midirooms.py στον φάκελο %REPO%
    exit /b 1
)

if exist "%REPO%\.venv\.midirooms_ready" goto :ready

echo Πρώτη εκτέλεση: εγκατάσταση, μπορεί να κρατήσει λίγα λεπτά...
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist "%VPY%" (
    %PY% -m venv "%REPO%\.venv" || goto :fail
)
"%VPY%" -m pip install --upgrade pip || goto :fail
"%VPY%" -m pip install -r "%REPO%\requirements.txt" || goto :fail

powershell -NoProfile -Command "$b='%REPO%'; $p=[Environment]::GetEnvironmentVariable('Path','User'); if(-not $p){$p=''}; if(($p -split ';') -notcontains $b){[Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$b),'User')}"

echo ready> "%REPO%\.venv\.midirooms_ready"
echo.
echo Η εγκατάσταση τελείωσε. Από νέο Command Prompt, οπουδήποτε, γράψτε: midirooms, midirooms_p2p ή midiplayerandrecorder

:ready
if /i "%~1"=="--setup" exit /b 0
"%VPY%" "%REPO%\midirooms.py" %*
exit /b %errorlevel%

:fail
echo Η εγκατάσταση απέτυχε.
exit /b 1
