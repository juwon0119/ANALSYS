@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Run Setup.cmd first.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" tests\test_native_geometry.py
if errorlevel 1 (
 echo Geometry diagnostics failed. See error above.
) else (
 echo Geometry regression checks passed.
)
pause
