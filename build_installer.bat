@echo off
setlocal
cd /d "%~dp0"
if not exist "dist\JetVideo.exe" exit /b 1
set "JETVIDEO_ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "JETVIDEO_ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "JETVIDEO_ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined JETVIDEO_ISCC for %%I in (ISCC.exe) do if not "%%~$PATH:I"=="" set "JETVIDEO_ISCC=%%~$PATH:I"
if not defined JETVIDEO_ISCC exit /b 1
"%JETVIDEO_ISCC%" installer.iss
exit /b %errorlevel%
