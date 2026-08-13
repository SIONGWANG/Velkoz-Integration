@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
set "PY_CMD=py"
set "PORT=8502"

if exist "%PYTHON_EXE%" (
    set "PY_CMD=%PYTHON_EXE%"
) else (
    py --version >nul 2>nul
    if errorlevel 1 (
        echo Python 3.11 was not found.
        if exist "python-3.11.9-amd64.exe" (
            echo Please install Python first: python-3.11.9-amd64.exe
            echo Keep "Add python.exe to PATH" enabled if the installer asks.
        ) else (
            echo Please install Python 3.11, then run this file again.
        )
        pause
        exit /b 1
    )
)

:find_port
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
    set /a PORT+=1
    goto find_port
)

echo Starting Streamlit on http://localhost:%PORT%
echo.

"%PY_CMD%" -c "import streamlit, pandas, PIL, xlsxwriter, streamlit_paste_button" >nul 2>nul
if errorlevel 1 (
    echo Missing Python dependencies.
    if exist "requirements.txt" (
        echo Installing dependencies from requirements.txt ...
        "%PY_CMD%" -m pip install -r requirements.txt
    ) else (
        echo requirements.txt not found. Please install streamlit pandas Pillow XlsxWriter streamlit-paste-button.
    )
)

"%PY_CMD%" -c "import streamlit, pandas, PIL, xlsxwriter, streamlit_paste_button" >nul 2>nul
if errorlevel 1 (
    echo.
    echo Dependency check failed. Please install dependencies manually:
    echo "%PY_CMD%" -m pip install -r requirements.txt
    pause
    exit /b 1
)

"%PY_CMD%" -m streamlit run app.py --server.port=%PORT%

if errorlevel 1 (
    echo.
    echo Startup failed.
    echo Please keep this window open and send me the error text above.
    pause
)
