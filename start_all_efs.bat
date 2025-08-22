@echo off
chcp 65001 >nul
title EFS Strategy - Start All Scripts

REM =============================================================================
REM EFS Strategy - Start All Scripts Script (Windows)
REM =============================================================================
REM This script starts all 4 data collectors and live trading scripts
REM in separate command prompt windows for the Entropy Fracture Strategy
REM =============================================================================

echo Starting EFS Strategy - All Scripts
echo ========================================
echo.

REM Check if we're in the right directory
if not exist "Live\Data" (
    echo Error: Please run this script from the LTSV root directory
    echo    Current directory: %CD%
    echo    Expected: LTSV root directory with Live\Data\ folder
    pause
    exit /b 1
)

REM Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH
    echo    Please install Python and try again
    pause
    exit /b 1
)

REM Check if required Python packages are installed
echo Checking Python dependencies...
python -c "import websocket, requests, pandas" 2>nul
if %errorlevel% neq 0 (
    echo Error: Required Python packages not found
    echo    Please install: websocket-client, requests, pandas
    echo    Run: pip install websocket-client requests pandas
    pause
    exit /b 1
)
echo Python dependencies OK
echo.

REM =============================================================================
REM STEP 1: Start Data Collectors
REM =============================================================================
echo STEP 1: Starting Data Collectors
echo -----------------------------------

REM Start ARKM data collector
echo Starting ARKM Data Collector in new window...
start "ARKM Data Collector - EFS" cmd /k "cd /d %CD% && python Live\Data\dataarkm.py"

REM Start DOGE data collector
echo Starting DOGE Data Collector in new window...
start "DOGE Data Collector - EFS" cmd /k "cd /d %CD% && python Live\Data\datadoge.py"

REM Start SEI data collector
echo Starting SEI Data Collector in new window...
start "SEI Data Collector - EFS" cmd /k "cd /d %CD% && python Live\Data\datasei.py"

REM Start THETA data collector
echo Starting THETA Data Collector in new window...
start "THETA Data Collector - EFS" cmd /k "cd /d %CD% && python Live\Data\datatheta.py"

echo All 4 data collectors started
echo.

REM =============================================================================
REM STEP 2: Wait for Data Collection
REM =============================================================================
echo STEP 2: Waiting 15 seconds for data collection to initialize...
echo    This allows the data collectors to download historical data
echo    and establish WebSocket connections
echo.

for /l %%i in (15,-1,1) do (
    echo    Starting live trading in: %%i seconds...
    timeout /t 1 /nobreak >nul
)
echo    Starting live trading now!
echo.
echo.

REM =============================================================================
REM STEP 3: Start Live Trading Scripts
REM =============================================================================
echo STEP 3: Starting Live Trading Scripts
echo ----------------------------------------

REM Check if live trading scripts exist
if not exist "Live\Trading\livearkm.py" (
    echo Error: livearkm.py not found in Live\Trading\ directory
    echo    Please ensure you're in the LTSV root directory
    pause
    exit /b 1
)

REM Start ARKM live trading from Live/Trading/ directory
echo Starting ARKM Live Trading in new window...
start "ARKM Live Trading - EFS" cmd /k "cd /d %CD%\Live\Trading && python livearkm.py"

REM Start DOGE live trading (if exists) from Live/Trading/ directory
if exist "Live\Trading\livedoge.py" (
    echo Starting DOGE Live Trading in new window...
    start "DOGE Live Trading - EFS" cmd /k "cd /d %CD%\Live\Trading && python livedoge.py"
) else (
    echo Warning: livedoge.py not found in Live\Trading\, skipping DOGE live trading
)

REM Start SEI live trading (if exists) from Live/Trading/ directory
if exist "Live\Trading\livesei.py" (
    echo Starting SEI Live Trading in new window...
    start "SEI Live Trading - EFS" cmd /k "cd /d %CD%\Live\Trading && python livesei.py"
) else (
    echo Warning: livesei.py not found in Live\Trading\, skipping SEI live trading
)

REM Start THETA live trading (if exists) from Live/Trading/ directory
if exist "Live\Trading\livetheta.py" (
    echo Starting THETA Live Trading in new window...
    start "THETA Live Trading - EFS" cmd /k "cd /d %CD%\Live\Trading && python livetheta.py"
) else (
    echo Warning: livetheta.py not found in Live\Trading\, skipping THETA live trading
)

echo.
echo All scripts started successfully!
echo.

REM =============================================================================
REM FINAL STATUS
REM =============================================================================
echo EFS Strategy - All Systems Running
echo ======================================
echo.
echo Data Collectors Running:
echo    - ARKM Data Collector
echo    - DOGE Data Collector
echo    - SEI Data Collector
echo    - THETA Data Collector
echo.
echo Live Trading Running:
echo    - ARKM Live Trading
echo    - DOGE Live Trading (if exists)
echo    - SEI Live Trading (if exists)
echo    - THETA Live Trading (if exists)
echo.
echo Monitor each window for:
echo    - Historical data download progress
echo    - WebSocket connection status
echo    - Live candle updates
echo    - Trading signals and executions
echo.
echo To stop all scripts: Close the command prompt windows or use Ctrl+C in each
echo.
echo Data files will be saved in: Live\Data\
echo    - dataarkm.csv
echo    - datadoge.csv
echo    - datasei.csv
echo    - datatheta.csv
echo.

echo Press any key to exit this status display (scripts will continue running)
pause >nul
