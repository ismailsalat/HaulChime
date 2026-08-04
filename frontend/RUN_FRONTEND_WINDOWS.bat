@echo off
cd /d "%~dp0dist"
"..\..\backend\.venv\Scripts\python.exe" -m http.server 8080
