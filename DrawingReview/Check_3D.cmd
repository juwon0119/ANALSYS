@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Python environment not found. Run Setup.cmd first.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" stp_mesh.py --check
if errorlevel 1 (
 echo.
 echo STEP/STP converter check failed. Run Setup.cmd and review error details.
) else (
 echo.
 echo STEP/STP converter check passed.
)
pause
