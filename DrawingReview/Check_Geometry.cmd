@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Run Setup.cmd first.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" tests\test_native_geometry.py
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
if errorlevel 1 goto fail
echo Geometry regression checks passed.
pause
exit /b 0
:fail
echo Geometry diagnostics failed. See error above.
pause
exit /b 1
