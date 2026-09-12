@echo off
"%~dp0..\work\venv\Scripts\python.exe" "%~dp0training-control.py" start
if errorlevel 1 goto fail
start "" "http://127.0.0.1:8002"
exit /b
:fail
pause
