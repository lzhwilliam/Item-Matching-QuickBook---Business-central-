@echo off
REM Double-click to start the Imperial Label Matcher on Windows.
cd /d "%~dp0"
if not exist .venv (
  echo First run: creating Python environment...
  py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
start "" http://localhost:8765
python -m uvicorn app.main:app --port 8765
pause
