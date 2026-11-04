@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title BG_ALPHA_STUDIO - EXE Builder

echo ========================================
echo   BG_ALPHA_STUDIO - EXE Builder

echo ========================================
echo.
REM Auto-follow the filename. Keep exactly one bg_alpha_studio_*.pyw in this directory.
set "SOURCE="
set "APP_STEM="
for %%F in ("%~dp0bg_alpha_studio_*.pyw") do (
  if exist "%%~fF" (
    if defined SOURCE (
      echo ERROR: Multiple bg_alpha_studio_*.pyw files were found.
      echo Keep only the version you want to build in this folder.
      pause
      exit /b 1
    )
    set "SOURCE=%%~fF"
    set "APP_STEM=%%~nF"
  )
)
if not defined SOURCE (
  echo ERROR: bg_alpha_studio_*.pyw was not found next to this BAT.
  pause
  exit /b 1
)
echo Source: !SOURCE!
echo EXE:    !APP_STEM!.exe
echo.

set "PY=python"
where py >nul 2>nul
if not errorlevel 1 (
  py -3.10 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PY=py -3.10"
)
%PY% -c "import sys; print('Python:',sys.version)"
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer is required on the build PC.
  pause
  exit /b 1
)

set "FFMPEG="
if exist "%~dp0ffmpeg.exe" set "FFMPEG=%~dp0ffmpeg.exe"
if not defined FFMPEG if exist "C:\ffmpeg-2024-11-21-git-f298507323-full_build\bin\ffmpeg.exe" set "FFMPEG=C:\ffmpeg-2024-11-21-git-f298507323-full_build\bin\ffmpeg.exe"
if not defined FFMPEG if exist "C:\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe" set "FFMPEG=C:\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
if not defined FFMPEG for /f "delims=" %%F in ('where ffmpeg.exe 2^>nul') do if not defined FFMPEG set "FFMPEG=%%F"
if not defined FFMPEG (
  echo ERROR: ffmpeg.exe was not found.
  echo Copy ffmpeg.exe next to this BAT and run it again.
  pause
  exit /b 1
)
echo Bundling FFmpeg: !FFMPEG!
echo.

echo Installing CustomTkinter, TkinterDnD2 and PyInstaller...
%PY% -m pip install --upgrade "customtkinter>=5.2,<6" "tkinterdnd2>=0.7,<1" "pyinstaller>=6,<7"
if errorlevel 1 (
  echo ERROR: Package installation failed.
  pause
  exit /b 1
)

echo Building EXE...
REM Collect CustomTkinter theme and font assets, as well as the FFmpeg executable.
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --collect-all customtkinter --collect-all tkinterdnd2 --name "!APP_STEM!" --add-binary "!FFMPEG!;." "!SOURCE!"
if errorlevel 1 (
  echo ERROR: EXE build failed. Check the console messages above.
  pause
  exit /b 1
)

echo.
echo DONE: %~dp0dist\!APP_STEM!.exe
explorer "%~dp0dist"
pause
