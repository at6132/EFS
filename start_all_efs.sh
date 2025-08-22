#!/bin/bash

# =============================================================================
# EFS Strategy - Start All Scripts Script
# =============================================================================
# This script starts all 4 data collectors and live trading scripts
# in separate terminal windows for the Entropy Fracture Strategy
# =============================================================================

echo "🚀 Starting EFS Strategy - All Scripts"
echo "========================================"
echo ""

# Check if we're in the right directory
if [ ! -d "Live/Data" ]; then
    echo "❌ Error: Please run this script from the LTSV root directory"
    echo "   Current directory: $(pwd)"
    echo "   Expected: LTSV root directory with Live/Data/ folder"
    exit 1
fi

# Function to start a script in a new terminal
start_in_terminal() {
    local script_name=$1
    local script_path=$2
    local title=$3
    
    echo "📱 Starting $title in new terminal..."
    
    # Try different terminal emulators
    if command -v gnome-terminal &> /dev/null; then
        gnome-terminal --title="$title" -- bash -c "cd $(pwd) && python3 $script_path; exec bash"
    elif command -v konsole &> /dev/null; then
        konsole --title "$title" -e bash -c "cd $(pwd) && python3 $script_path; exec bash"
    elif command -v xterm &> /dev/null; then
        xterm -title "$title" -e bash -c "cd $(pwd) && python3 $script_path; exec bash" &
    elif command -v alacritty &> /dev/null; then
        alacritty --title "$title" -e bash -c "cd $(pwd) && python3 $script_path; exec bash" &
    else
        echo "❌ No supported terminal emulator found. Please install gnome-terminal, konsole, xterm, or alacritty"
        exit 1
    fi
    
    sleep 1  # Small delay between terminal launches
}

# Check if Python3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python3 is not installed or not in PATH"
    echo "   Please install Python3 and try again"
    exit 1
fi

# Check if required Python packages are installed
echo "🔍 Checking Python dependencies..."
python3 -c "import websocket, requests, pandas" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "❌ Error: Required Python packages not found"
    echo "   Please install: websocket-client, requests, pandas"
    echo "   Run: pip3 install websocket-client requests pandas"
    exit 1
fi
echo "✅ Python dependencies OK"
echo ""

# =============================================================================
# STEP 1: Start Data Collectors
# =============================================================================
echo "📊 STEP 1: Starting Data Collectors"
echo "-----------------------------------"

# Start ARKM data collector
start_in_terminal "ARKM Data Collector" "Live/Data/dataarkm.py" "ARKM Data Collector - EFS"

# Start DOGE data collector  
start_in_terminal "DOGE Data Collector" "Live/Data/datadoge.py" "DOGE Data Collector - EFS"

# Start SEI data collector
start_in_terminal "SEI Data Collector" "Live/Data/datasei.py" "SEI Data Collector - EFS"

# Start THETA data collector
start_in_terminal "THETA Data Collector" "Live/Data/datatheta.py" "THETA Data Collector - EFS"

echo "✅ All 4 data collectors started"
echo ""

# =============================================================================
# STEP 2: Wait for Data Collection
# =============================================================================
echo "⏳ STEP 2: Waiting 15 seconds for data collection to initialize..."
echo "   This allows the data collectors to download historical data"
echo "   and establish WebSocket connections"
echo ""

for i in {15..1}; do
    echo -ne "   Starting live trading in: $i seconds...\r"
    sleep 1
done
echo -ne "   Starting live trading now!                    \r"
echo ""
echo ""

# =============================================================================
# STEP 3: Start Live Trading Scripts
# =============================================================================
echo "🎯 STEP 3: Starting Live Trading Scripts"
echo "----------------------------------------"

# Check if live trading scripts exist
if [ ! -f "livearkm.py" ]; then
    echo "❌ Error: livearkm.py not found in current directory"
    echo "   Please ensure you're in the LTSV root directory"
    exit 1
fi

# Start ARKM live trading
start_in_terminal "ARKM Live Trading" "livearkm.py" "ARKM Live Trading - EFS"

# Start DOGE live trading (if exists)
if [ -f "livedoge.py" ]; then
    start_in_terminal "DOGE Live Trading" "livedoge.py" "DOGE Live Trading - EFS"
else
    echo "⚠️  Warning: livedoge.py not found, skipping DOGE live trading"
fi

# Start SEI live trading (if exists)
if [ -f "livesei.py" ]; then
    start_in_terminal "SEI Live Trading" "livesei.py" "SEI Live Trading - EFS"
else
    echo "⚠️  Warning: livesei.py not found, skipping SEI live trading"
fi

# Start THETA live trading (if exists)
if [ -f "livetheta.py" ]; then
    start_in_terminal "THETA Live Trading" "livetheta.py" "THETA Live Trading - EFS"
else
    echo "⚠️  Warning: livetheta.py not found, skipping THETA live trading"
fi

echo ""
echo "✅ All scripts started successfully!"
echo ""

# =============================================================================
# FINAL STATUS
# =============================================================================
echo "🎉 EFS Strategy - All Systems Running"
echo "======================================"
echo ""
echo "📱 Data Collectors Running:"
echo "   • ARKM Data Collector"
echo "   • DOGE Data Collector" 
echo "   • SEI Data Collector"
echo "   • THETA Data Collector"
echo ""
echo "🎯 Live Trading Running:"
echo "   • ARKM Live Trading"
echo "   • DOGE Live Trading (if exists)"
echo "   • SEI Live Trading (if exists)"
echo "   • THETA Live Trading (if exists)"
echo ""
echo "📊 Monitor each terminal for:"
echo "   • Historical data download progress"
echo "   • WebSocket connection status"
echo "   • Live candle updates"
echo "   • Trading signals and executions"
echo ""
echo "🛑 To stop all scripts: Close the terminal windows or use Ctrl+C in each"
echo ""
echo "📁 Data files will be saved in: Live/Data/"
echo "   • dataarkm.csv"
echo "   • datadoge.csv"
echo "   • datasei.csv"
echo "   • datatheta.csv"
echo ""

# Keep script running to show status
echo "Press Ctrl+C to exit this status display (scripts will continue running)"
while true; do
    sleep 10
done
