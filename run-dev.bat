@echo off
rem ============================================
rem  dsh-pet dev launcher (uses repo-local .venv)
rem  First time: .venv\Scripts\python.exe -m pip install -r requirements.txt
rem ============================================
cd /d "%~dp0"
.venv\Scripts\pythonw.exe -m pet
