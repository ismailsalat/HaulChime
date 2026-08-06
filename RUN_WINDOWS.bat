@echo off
setlocal
cd /d "%~dp0"

echo.
echo ==============================================
echo   HaulChime local setup
echo ==============================================

set "PYTHON_CMD=python"
where py >nul 2>&1
if %errorlevel%==0 set "PYTHON_CMD=py -3"

if not exist "backend\.venv\Scripts\python.exe" (
  echo Creating Python environment...
  %PYTHON_CMD% -m venv backend\.venv
  if errorlevel 1 goto :python_error
)

echo Installing backend packages...
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :install_error

pushd backend
".venv\Scripts\python.exe" seed.py
if errorlevel 1 (
  popd
  goto :seed_error
)
popd

start "HaulChime Backend" cmd /k call "%~dp0backend\RUN_BACKEND_WINDOWS.bat"
start "HaulChime Website" cmd /k call "%~dp0frontend\RUN_FRONTEND_WINDOWS.bat"

timeout /t 3 /nobreak >nul
start "" http://localhost:8080

echo.
echo Website: http://localhost:8080
echo Admin:   http://localhost:5002/admin
echo Login:   admin / haulchime123
echo.
echo Keep both new command windows open while testing.
pause
exit /b 0

:python_error
echo Python 3 was not found. Install Python 3.11 or newer and select "Add Python to PATH".
pause
exit /b 1

:install_error
echo Package installation failed. Check your internet connection and try again.
pause
exit /b 1

:seed_error
echo Local database setup failed. Review the error above.
pause
exit /b 1
