import websocket
import json
import time
import signal
import sys
import threading
import csv
import os
import requests
from datetime import datetime, timedelta

# =================== CONFIGURATION ===================
SYMBOL = "DOGE_USDT"  # DOGE/USDT futures symbol
# =====================================================

# MEXC Futures WebSocket for real OHLCV kline data
WS_URL = "wss://contract.mexc.com/edge"
INTERVAL = "Min15"

# Auto-generate CSV filename based on symbol (lowercase, remove USDT)
def get_csv_filename(symbol):
    if not symbol:
        return "data.csv"
    # Remove _USDT and convert to lowercase
    clean_symbol = symbol.replace('_USDT', '').replace('_usdt', '').lower()
    return f"data{clean_symbol}.csv"

CSV_FILENAME = get_csv_filename(SYMBOL)

# Global variables for WebSocket data
latest_kline = None
last_candle_time = None
ws = None
heartbeat_timer = None
is_running = True

def download_historical_data(symbol, limit=3000):
    """Download historical kline data from MEXC Futures API"""
    print(f"📥 Downloading last 30 days of data for {symbol} (FUTURES)...")
    print(f"📊 Target: {limit} candles (MEXC API limit is ~2000 per request)")
    
    # Calculate timestamps (last 30 days)
    import time
    now = int(time.time())
    start = now - (30 * 24 * 60 * 60)  # 30 days ago in seconds
    
    # MEXC API limit is around 2000 candles per request
    API_LIMIT = 2000
    all_candles = []
    
    # Make multiple requests to get all candles
    current_start = start
    request_count = 0
    
    while len(all_candles) < limit and current_start < now:
        request_count += 1
        print(f"📡 API Request #{request_count}: {datetime.fromtimestamp(current_start).strftime('%Y-%m-%d %H:%M:%S')} to {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')}")
        
        # MEXC futures API endpoint for historical klines
        url = f"https://contract.mexc.com/api/v1/contract/kline/{symbol}"
        params = {
            "interval": "Min15",
            "start": current_start,
            "end": now,
            "limit": 1000 if request_count == 2 else min(API_LIMIT, limit - len(all_candles))  # Second request gets exactly 1000
        }
        
        print(f"🔍 Futures API URL: {url}")
        print(f"🔍 Params: {params}")
        
        try:
            response = requests.get(url, params=params, timeout=10)
            print(f"🔍 Response status: {response.status_code}")
            
            if response.status_code == 200:
                response_data = response.json()
                
                # MEXC futures API returns data in format: {"success": true, "code": 0, "data": {...}}
                if response_data.get('success') and 'data' in response_data:
                    data_obj = response_data['data']
                    
                    # Data format: {"time": [timestamps], "open": [prices], "high": [prices], "low": [prices], "close": [prices], "vol": [volumes]}
                    if all(key in data_obj for key in ['time', 'open', 'high', 'low', 'close', 'vol']):
                        times = data_obj['time']
                        opens = data_obj['open']
                        highs = data_obj['high']
                        lows = data_obj['low']
                        closes = data_obj['close']
                        volumes = data_obj['vol']
                        
                        # Convert to standard format [timestamp, open, high, low, close, volume]
                        converted_data = []
                        for i in range(len(times)):
                            converted_candle = [
                                int(times[i]) * 1000,  # Convert to milliseconds
                                str(opens[i]),
                                str(highs[i]),
                                str(lows[i]),
                                str(closes[i]),
                                str(volumes[i])
                            ]
                            converted_data.append(converted_candle)
                        
                        print(f"✅ Downloaded {len(converted_data)} candles in request #{request_count}")
                        all_candles.extend(converted_data)
                        
                        # If we got fewer candles than requested, we've hit the limit
                        if len(converted_data) < API_LIMIT:
                            print(f"📊 API limit reached, got {len(converted_data)} candles")
                            break
                        
                        # Move start time forward for next request
                        if converted_data:
                            # For the second request, we want to ensure we get exactly 1000 candles
                            if request_count == 1:  # After first request
                                # Calculate the timestamp of the last candle we received
                                last_candle_timestamp = int(converted_data[-1][0]) / 1000  # Convert from ms to seconds
                                # Move start time to just after the last candle
                                current_start = last_candle_timestamp + 1
                                
                                # Debug logging
                                print(f"🔍 Debug: Last candle timestamp: {last_candle_timestamp}")
                                print(f"🔍 Debug: Last candle time: {datetime.fromtimestamp(last_candle_timestamp).strftime('%Y-%m-%d %H:%M:%S')}")
                                print(f"🔍 Debug: New start time: {current_start}")
                                print(f"🔍 Debug: New start time: {datetime.fromtimestamp(current_start).strftime('%Y-%m-%d %H:%M:%S')}")
                                
                                # Ensure we have a reasonable time gap for the second request
                                time_gap = now - current_start
                                if time_gap < 900:  # Less than 15 minutes
                                    print(f"⚠️ Warning: Time gap too small ({time_gap} seconds), adjusting...")
                                    # Move start time back to ensure we have enough data
                                    current_start = now - (1000 * 900)  # 1000 candles * 15 minutes each
                                    print(f"🔍 Debug: Adjusted start time: {datetime.fromtimestamp(current_start).strftime('%Y-%m-%d %H:%M:%S')}")
                            else:
                                # For subsequent requests, use the time-based calculation
                                candles_received = len(converted_data)
                                time_covered = candles_received * 900  # Each 15m candle = 900 seconds
                                current_start = current_start + time_covered
                        
                        # Add delay between requests to avoid rate limiting
                        time.sleep(0.5)
                        
                    else:
                        print(f"❌ Unexpected data format: {data_obj.keys()}")
                        break
                else:
                    print(f"❌ Futures API error: {response_data}")
                    break
            else:
                print(f"❌ Futures historical data request failed: {response.status_code}")
                print(f"❌ Response text: {response.text}")
                break
                
        except Exception as e:
            print(f"⚠️ Error downloading futures historical data: {e}")
            break
    
    print(f"📊 Total candles downloaded: {len(all_candles)}")
    return all_candles

def save_historical_data(historical_data):
    """Save historical kline data to CSV"""
    if not historical_data:
        return
        
    print(f"💾 Saving {len(historical_data)} historical candles to {CSV_FILENAME}...")
    
    # Check if file exists to determine if we need headers
    file_exists = os.path.exists(CSV_FILENAME)
    
    with open(CSV_FILENAME, 'w', newline='') as csvfile:  # 'w' to overwrite existing file
        writer = csv.writer(csvfile)
        
        # Write header
        writer.writerow(['Timestamp', 'DateTime', 'Open', 'High', 'Low', 'Close', 'Volume'])
        
        # Write historical data
        for candle in historical_data:
            timestamp = int(candle[0])  # Already in milliseconds from MEXC
            dt = datetime.fromtimestamp(timestamp / 1000)
            
            writer.writerow([
                timestamp,
                dt.strftime('%Y-%m-%d %H:%M:%S'),
                float(candle[1]),  # Open
                float(candle[2]),  # High
                float(candle[3]),  # Low
                float(candle[4]),  # Close
                float(candle[5])   # Volume
            ])
    
    print(f"✅ Historical data saved to {CSV_FILENAME}")
    
    # Set last_candle_time to the last historical candle to avoid duplicates
    global last_candle_time
    if historical_data:
        last_candle_time = int(historical_data[-1][0])
        print(f"🕒 Last historical candle: {datetime.fromtimestamp(last_candle_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}")

def save_to_csv(timestamp, open_price, high_price, low_price, close_price, volume):
    """Save candle data to CSV file"""
    try:
        # Convert timestamp to readable datetime
        dt = datetime.fromtimestamp(timestamp / 1000)
        
        # Check if file exists to determine if we need headers
        file_exists = os.path.exists(CSV_FILENAME)
        
        with open(CSV_FILENAME, 'a', newline='') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write header if file is new
            if not file_exists:
                writer.writerow(['Timestamp', 'DateTime', 'Open', 'High', 'Low', 'Close', 'Volume'])
                print(f"📄 Created new CSV file: {CSV_FILENAME}")
            
            # Write candle data
            writer.writerow([
                timestamp,
                dt.strftime('%Y-%m-%d %H:%M:%S'),
                open_price,
                high_price,
                low_price,
                close_price,
                volume
            ])
            
    except Exception as e:
        print(f"⚠️ CSV save error: {e}")

def on_message(ws, message):
    global latest_kline, last_candle_time
    try:
        data = json.loads(message)
        
        if data.get('channel') == 'push.kline':
            kline_data = data.get('data', {})
            if kline_data.get('symbol') == SYMBOL:
                # Extract OHLCV data from WebSocket message
                # Format: {"a": amount, "c": close, "h": high, "l": low, "o": open, "q": volume, "t": timestamp}
                timestamp = int(kline_data.get('t', 0)) * 1000  # Convert to milliseconds
                open_price = float(kline_data.get('o', 0))
                high_price = float(kline_data.get('h', 0))
                low_price = float(kline_data.get('l', 0))
                close_price = float(kline_data.get('c', 0))
                volume = float(kline_data.get('q', 0))
                
                kline_array = [timestamp, open_price, high_price, low_price, close_price, volume]
                
                latest_kline = {
                    'source': 'mexc-futures-websocket',
                    'data': kline_array
                }
                
                # Only print and save when we get a NEW candle (different timestamp)
                if timestamp != last_candle_time:
                    print(f"🕒 NEW CANDLE: {ts_to_time(timestamp)} | O:{open_price} H:{high_price} L:{low_price} C:{close_price} V:{volume}")
                    
                    # Save to CSV
                    save_to_csv(timestamp, open_price, high_price, low_price, close_price, volume)
                    print(f"💾 Saved to {CSV_FILENAME}")
                    
                    last_candle_time = timestamp
        
    except Exception as e:
        print(f"⚠️ WebSocket message error: {e}")

def on_error(ws, error):
    print(f"🔴 WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    global heartbeat_timer, is_running
    print("🔌 WebSocket connection closed")
    
    # Stop heartbeat timer if connection closes
    if heartbeat_timer:
        heartbeat_timer.cancel()
        heartbeat_timer = None
    
    # Auto-reconnect if still running
    if is_running:
        print("🔄 Auto-reconnecting in 5 seconds...")
        time.sleep(5)
        start_websocket()

def send_heartbeat():
    """Send ping to keep connection alive"""
    global ws, heartbeat_timer
    if ws:
        try:
            ping_msg = {"method": "ping"}
            ws.send(json.dumps(ping_msg))
            print("💓 Heartbeat sent")
            
            # Schedule next heartbeat in 30 seconds
            heartbeat_timer = threading.Timer(30.0, send_heartbeat)
            heartbeat_timer.start()
        except Exception as e:
            print(f"⚠️ Heartbeat error: {e}")

def on_open(ws):
    print(f"🟢 WebSocket connected - subscribing to {SYMBOL} Min1 klines...")
    
    # Subscribe to kline data
    subscribe_msg = {
        "method": "sub.kline",
        "param": {
            "symbol": SYMBOL,
            "interval": INTERVAL
        }
    }
    
    ws.send(json.dumps(subscribe_msg))
    print(f"📡 Subscribed to {SYMBOL} {INTERVAL} klines")
    
    # Start heartbeat to keep connection alive
    send_heartbeat()

def start_websocket():
    global ws
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(WS_URL,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    
    # Run WebSocket in a separate thread
    ws.run_forever()

def get_latest_kline():
    return latest_kline

def signal_handler(sig, frame):
    global heartbeat_timer, ws, is_running
    print("\n🛑 Shutting down gracefully...")
    
    # Stop auto-reconnection
    is_running = False
    
    # Stop heartbeat timer
    if heartbeat_timer:
        heartbeat_timer.cancel()
        
    # Close WebSocket connection
    if ws:
        ws.close()
        
    sys.exit(0)

def ts_to_time(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000).strftime('%H:%M:%S')

signal.signal(signal.SIGINT, signal_handler)

print("🚀 Starting MEXC Futures WebSocket kline monitor...")
print(f"📊 Symbol: {SYMBOL}")
print(f"💾 Saving to: {CSV_FILENAME}")
print()

# Download historical data first
historical_data = download_historical_data(SYMBOL, 3000)  # 30 days × 24 hours × 4 candles per hour
if historical_data:
    save_historical_data(historical_data)
    print()
else:
    print("⚠️ No historical data downloaded, starting with live data only")
    print()

print("🎯 Starting real-time data collection...")
print("Press Ctrl+C to stop")

# Start WebSocket in background thread
ws_thread = threading.Thread(target=start_websocket, daemon=True)
ws_thread.start()

print("⏳ Connecting to MEXC WebSocket...")

# Keep the main thread alive - WebSocket will handle all events
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    signal_handler(None, None)