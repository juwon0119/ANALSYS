@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] Preparing Python virtual environment...
if not exist ".venv\Scripts\python.exe" (python -m venv .venv || goto fail)
echo [2/4] Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto fail
echo [3/4] Installing PDF / DXF / rendering modules...
".venv\Scripts\python.exe" -m pip install --upgrade ezdxf PyMuPDF Pillow numpy opencv-python-headless || goto fail
echo [4/4] Installing Gmsh STEP/STP converter...
".venv\Scripts\python.exe" -m pip install --upgrade gmsh || goto fail
echo Checking 3D converter...
".venv\Scripts\python.exe" stp_mesh.py --check || goto fail
echo Setup complete. Starting program...
call Run.cmd
exit /b 0
:fail
echo.
echo Setup failed. Check 64-bit Python, internet and errors above.
pause
exit /b 1
