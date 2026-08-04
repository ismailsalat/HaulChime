@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m flask --app app run --port 5002
