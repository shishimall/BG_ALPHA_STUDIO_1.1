@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed. Check Python/pip and network access.
) else (
  echo Dependency installation completed.
)
pause
