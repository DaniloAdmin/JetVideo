@echo off
setlocal EnableExtensions
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (set "JETVIDEO_PY=python") else (set "JETVIDEO_PY=py -3.14")
if not exist ".venv\Scripts\python.exe" (
  %JETVIDEO_PY% -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-build.lock
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean JetVideo.spec
if errorlevel 1 exit /b 1
start /wait "" "dist\JetVideo.exe" --smoke-test "%CD%\verification\frozen-smoke.json"
if errorlevel 1 exit /b 1
echo Portable application created: dist\JetVideo.exe
echo Use package_release.py with the corresponding FFmpeg binaries and sources to assemble the release ZIP.
