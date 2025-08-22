import asyncio
import aiohttp
from aiohttp import ClientConnectorError
import math
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import warnings
import os
import csv
warnings.filterwarnings('ignore')

# Helper function to add timestamps to prints
def tprint(message: str):
    """Print with timestamp"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] {message}")

def is_rate_limit_error(error_message: str) -> bool:
    """Check if error message indicates rate limiting"""
    rate_limit_messages = [
        "请求频率过快!",
        "rate limit",
        "too many requests",
        "频率过快"
    ]
    return any(msg in str(error_message).lower() for msg in rate_limit_messages)

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8156711122:AAFYoW3ESDlxAjSfHO_DkjabgKZ3aUc3oRI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7168811895")

# GroupMe Configuration
GROUPME_BOT_ID = os.getenv("GROUPME_BOT_ID", "2fc11058a4b66320f1bafc1593")  # Add your GroupMe bot ID here

# MEXC SDK imports
import sys
import os
sys.path.append('./mexc_python')
from mexcpy.mexcTypes import CreateOrderRequest, OpenType, OrderSide, OrderType
from mexcpy.api import MexcFuturesAPI

# =============================================================================
# CONFIGURATION - EDIT THESE SETTINGS
# =============================================================================
# 
# TRADING STRATEGY: The Entropy Fracture Strategy (EFS)
# - Novel 15m framework built on microstructure entropy + volatility fractures
# - Measures information entropy decay in short-term volatility patterns
# - When entropy collapses (market moves from chaotic to ordered), it precedes impulse moves
# - Pairs entropy collapse with volatility "fracture" - structural break in candle formation
# 
# DATA SOURCE:
# - OHLCV: Local CSV file (dataarkm.csv) - 1-minute candles
# - Bid/Ask: MEXC futures API (only when in position)
# - CSV Logging: All bid/ask data saved for analysis
#

# MEXC API Configuration
from config import get_api_token, get_account_name
TRADING_SYMBOL = "ARKM_USDT"                  # Trading pair
MEXC_API_TOKEN = get_api_token(TRADING_SYMBOL)
USE_TESTNET = False                           # Set to False for live trading

# Symbol-Specific Configuration
SYMBOL_DECIMALS = 4                           # Number of decimal places for price (ARKM = 4)
CONTRACTS_PER_SYMBOL = 10                     # Number of tokens per contract (ARKM = 10)
MIN_CONTRACT_SIZE = 1                          # Minimum contract size
MAX_CONTRACT_SIZE = 1000                       # Maximum contract size

# Strategy Configuration - EFS Parameters
POSITION_SIZE_PCT = 0.25                     # 5% of balance per trade
LEVERAGE = 15                                 # 5x leverage
RISK_PCT = 0.25                             # 2% risk per trade (for position sizing)
ENTROPY_WINDOW = 30                          # Rolling window for entropy calculation (30 bars)
ENTROPY_STD_THRESHOLD = 1.2                  # Entropy collapse threshold (1.2 std below mean)
FRACTURE_COMPRESSION = 0.7                   # Fracture Index compression threshold
TP1_ATR_MULTIPLIER = 0.8                    # TP1: 0.8 × ATR(15m)
TP2_ATR_MULTIPLIER = 0.5                    # TP2: trail stop at 0.5 × ATR(15m)
SIGNAL_COOLDOWN = 60                         # Seconds between signal checks
CSV_UPDATE_INTERVAL = 60                     # Seconds between CSV data updates

# Order Management
MAX_RETRIES = 3                              # Max retries for post-only orders
ORDER_TIMEOUT = 5                            # Seconds to wait for order fill (5 seconds for entry)
PRICE_MOVEMENT_THRESHOLD = 0.0001            # 0.01% price movement threshold
ROUND_TRIP_FEE = 0.0005                     # 0.05% round trip fees

# Data Management
MIN_CANDLES_REQUIRED = 30 * 24 * 4  # 30 days * 24 hours * 4 candles per hour (15m) = 2,880 candles for entropy calculations
HISTORICAL_WINDOW_SIZE = 100          # Number of candles to keep in historical window
CSV_CHECK_INTERVAL = 3                # Seconds between CSV checks (normal)
CSV_CANDLE_DETECTED_WAIT = 45        # Seconds to wait after detecting a new candle
POSITION_CLOSE_COOLDOWN = 120         # Seconds cooldown after position close (2 minutes)
ENTROPY_CALCULATION_PERIOD = 30       # Days needed for entropy mean/std calculations

# Notification Settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8156711122:AAFYoW3ESDlxAjSfHO_DkjabgKZ3aUc3oRI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7168811895")
GROUPME_BOT_ID = os.getenv("GROUPME_BOT_ID", "2fc11058a4b66320f1bafc1593")

# CSV File Configuration
CSV_FILENAME = f"../Data/data{TRADING_SYMBOL.lower().split('_')[0]}.csv"  # ../Data/dataarkm.csv for ARKM_USDT
ORDERBOOK_CSV_PREFIX = f"live_orderbook_{TRADING_SYMBOL.replace('_', '')}"

# =============================================================================

class LiveEFSTrader:
    def __init__(self, token: str = MEXC_API_TOKEN, symbol: str = TRADING_SYMBOL, testnet: bool = USE_TESTNET):
        """
        Initialize live EFS trader with Entropy Fracture Strategy using local CSV data for ARKM
        """
        self.api = MexcFuturesAPI(token, testnet=testnet)
        self.symbol = symbol
        
        # Strategy parameters (from configuration)
        self.position_size_pct = POSITION_SIZE_PCT
        self.leverage = LEVERAGE
        self.risk_pct = RISK_PCT
        self.entropy_window = ENTROPY_WINDOW
        self.entropy_std_threshold = ENTROPY_STD_THRESHOLD
        self.fracture_compression = FRACTURE_COMPRESSION
        self.tp1_atr_multiplier = TP1_ATR_MULTIPLIER
        self.tp2_atr_multiplier = TP2_ATR_MULTIPLIER
        self.round_trip_fee = ROUND_TRIP_FEE
        
        # Order management
        self.ORDER_TIMEOUT = ORDER_TIMEOUT  # Add missing attribute
        self.current_position = None
        self.active_orders = {}
        self.tp_order_id = None
        self.sl_order_id = None
        self.manual_tp_monitor = False
        self.manual_tp_price = 0
        self.is_entering_position = False  # Flag to prevent multiple entries
        self.last_order_details = None  # Store order details for position info
        self.last_position_close_time = 0  # Cooldown after position close
        self.position_close_cooldown = POSITION_CLOSE_COOLDOWN  # 120 seconds cooldown (2 minutes)
        
        # Trailing stop loss tracking
        self.trailing_sl_enabled = False
        self.trailing_sl_price = 0
        self.entry_price = 0
        self.position_side = None
        
        # Telegram session for reuse
        self.tg_session = None
        
        # Market data
        self.price_data = []
        self.orderbook = {'bids': [], 'asks': []}
        self.last_orderbook_update = 0
        self.account_balance = 0.0
        self.is_trading = False
        
        # CSV data management - FIXED: Maintain rolling window
        self.csv_data = []
        self.historical_candles = []  # Rolling window of candles for indicators
        self.min_candles_required = MIN_CANDLES_REQUIRED  # Need at least 60 candles for all indicators
        self.current_csv_index = 0
        self.last_csv_update = 0
        
        # Initialize CSV data
        self.load_csv_data()
        
        tprint("🚀 Live Entropy Fracture Strategy (EFS) Trader initialized")
        tprint(f"   Symbol: {self.symbol}")
        tprint(f"   Testnet: {testnet}")
        tprint(f"   Position Size: {self.position_size_pct*100}%")
        tprint(f"   Leverage: {self.leverage}x")
        tprint(f"   Risk per trade: {self.risk_pct*100}%")
        tprint(f"   Entropy Window: {self.entropy_window} bars")
        tprint(f"   Entropy Collapse Threshold: {self.entropy_std_threshold} std below mean")
        tprint(f"   Fracture Compression: {self.fracture_compression}")
        tprint(f"   TP1: {self.tp1_atr_multiplier}× ATR(15m)")
        tprint(f"   TP2: {self.tp2_atr_multiplier}× ATR(15m) trailing")
        tprint(f"   Signal Cooldown: {SIGNAL_COOLDOWN}s")
        tprint(f"   Data Source: Local CSV + MEXC bid/ask")
        tprint(f"   Data logging: {ORDERBOOK_CSV_PREFIX}_{datetime.now().strftime('%Y%m%d')}.csv")
        tprint(f"   Min candles required: {self.min_candles_required:,} candles ({ENTROPY_CALCULATION_PERIOD} days)")
        tprint(f"   Strategy: Entropy Fracture Strategy (EFS) - Novel 15m framework")
        tprint(f"   Symbol Decimals: {SYMBOL_DECIMALS} | Contracts per symbol: {CONTRACTS_PER_SYMBOL}")
        tprint(f"   ⚠️  EFS requires {ENTROPY_CALCULATION_PERIOD} days of data for entropy calculations!")
        tprint(f"   ⚠️  Without sufficient data, entropy collapse detection will not work properly!")
    
    def load_csv_data(self):
        """Load and prepare live CSV data for trading"""
        try:
            # Load live CSV data (being tailed/updated in real-time)
            if not os.path.exists(CSV_FILENAME):
                tprint(f"❌ {CSV_FILENAME} not found - waiting for live data...")
                self.csv_data = []
                return
            
            # Load CSV data with headers (MEXC futures format)
            df = pd.read_csv(CSV_FILENAME)
            
            # Convert column names to lowercase for compatibility
            df.columns = df.columns.str.lower()
            
            # Convert timestamp to datetime (handle both timestamp and datetime columns)
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_numeric(df['timestamp'])
                df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            elif 'datetime' in df.columns:
                df['datetime'] = pd.to_datetime(df['datetime'])
                # Create timestamp from datetime if needed
                df['timestamp'] = df['datetime'].astype('int64') // 1000000
            
            # Filter out rows with empty OHLCV data
            df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])
            
            # Convert to list of dictionaries
            self.csv_data = df.to_dict('records')
            
            # Update historical candles window (keep last 100 candles for safety)
            if len(self.csv_data) > 0:
                # Keep the most recent candles for indicator calculation
                self.historical_candles = self.csv_data  # Use all available data for accurate indicators
                
                tprint(f"✅ Loaded {len(self.csv_data)} live candles from {CSV_FILENAME}")
                tprint(f"   Historical window: {len(self.historical_candles)} candles")
                tprint(f"   Latest candle: {self.csv_data[-1]['datetime']} @ ${self.csv_data[-1]['close']:.4f}")
                
                # Check if we have enough data for trading
                if len(self.historical_candles) >= self.min_candles_required:
                    tprint(f"✅ Sufficient data available ({len(self.historical_candles)} >= {self.min_candles_required})")
                else:
                    tprint(f"⚠️  Insufficient data: {len(self.historical_candles)} < {self.min_candles_required} candles required")
            
        except Exception as e:
            tprint(f"❌ Error loading CSV data: {e}")
            self.csv_data = []
            self.historical_candles = []
    
    def tail_csv_data(self):
        """Tail CSV file for new candles (only read new lines)"""
        try:
            if not os.path.exists(CSV_FILENAME):
                return []
            
            # Read CSV data (MEXC futures format)
            df = pd.read_csv(CSV_FILENAME)
            
            if len(df) == 0:
                return []
            
            # Convert column names to lowercase for compatibility
            df.columns = df.columns.str.lower()
            
            # Convert timestamp to datetime (handle both timestamp and datetime columns)
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_numeric(df['timestamp'])
                df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            elif 'datetime' in df.columns:
                df['datetime'] = pd.to_datetime(df['datetime'])
                # Create timestamp from datetime if needed
                df['timestamp'] = df['datetime'].astype('int64') // 1000000
            
            # Filter out rows with empty OHLCV data
            df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])
            
            # Convert to list of dictionaries
            all_candles = df.to_dict('records')
            
            # Find new candles (candles not in our historical data)
            if not self.historical_candles:
                # First time loading
                new_candles = all_candles[-HISTORICAL_WINDOW_SIZE:] if len(all_candles) > HISTORICAL_WINDOW_SIZE else all_candles
                self.historical_candles = new_candles
                self.csv_data = all_candles
                return new_candles
            
            # Find the last timestamp we have
            last_timestamp = self.historical_candles[-1]['datetime']
            
            # Find new candles after our last timestamp
            new_candles = []
            for candle in all_candles:
                if candle['datetime'] > last_timestamp:
                    new_candles.append(candle)
            
            if new_candles:
                # Add new candles to historical data
                self.historical_candles.extend(new_candles)
                self.csv_data = all_candles
                
                # Keep all available data for accurate indicators (matching backtest)
                # No need to limit data - use all available candles
            
            return new_candles
            
        except Exception as e:
            tprint(f"❌ Error tailing CSV data: {e}")
            return []
    
    def get_current_candle(self) -> Optional[Dict]:
        """Get current candle from CSV data based on time"""
        if not self.csv_data:
            return None
        
        current_time = datetime.now()
        
        # Find the closest candle to current time
        for candle in self.csv_data:
            candle_time = pd.to_datetime(candle['datetime'])
            if candle_time <= current_time:
                return candle
        
        return self.csv_data[-1] if self.csv_data else None
    
    def get_entry_candle(self) -> Optional[Dict]:
        """Get the 15m candle that triggered the entry signal"""
        if not self.csv_data or len(self.csv_data) == 0:
            return None
        current_time = datetime.now()
        for i in range(len(self.csv_data) - 1, -1, -1):
            candle = self.csv_data[i]
            if not candle or 'datetime' not in candle:
                continue
            try:
                candle_time = pd.to_datetime(candle['datetime'])
                if candle_time.minute % 15 == 0: # Check if this is a 15m boundary
                    return candle
            except (ValueError, TypeError, KeyError):
                continue
        return self.csv_data[-1] if self.csv_data else None
    
    def ensure_csv_headers(self):
        """Ensure CSV file has proper headers"""
        filename = f"{ORDERBOOK_CSV_PREFIX}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        if not os.path.exists(filename):
            with open(filename, 'w', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['timestamp', 'datetime', 'best_bid', 'best_ask', 'bid_volume', 'ask_volume'])
    
    def save_orderbook_to_csv(self, best_bid: float, best_ask: float, bid_vol: float, ask_vol: float):
        """Save orderbook data to CSV"""
        try:
            filename = f"{ORDERBOOK_CSV_PREFIX}_{datetime.now().strftime('%Y%m%d')}.csv"
            
            with open(filename, 'a', newline='') as file:
                writer = csv.writer(file)
                writer.writerow([
                    int(time.time()),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    f"{best_bid:.{SYMBOL_DECIMALS}f}",
                    f"{best_ask:.{SYMBOL_DECIMALS}f}",
                    f"{bid_vol:.2f}",
                    f"{ask_vol:.2f}"
                ])
        except Exception as e:
            tprint(f"❌ Error saving orderbook to CSV: {e}")
    
    async def send_groupme_message(self, message: str):
        """Send message to GroupMe"""
        try:
            if self.tg_session is None:
                timeout = aiohttp.ClientTimeout(total=5)
                self.tg_session = aiohttp.ClientSession(timeout=timeout)
            
            url = f"https://api.groupme.com/v3/bots/post"
            data = {
                'bot_id': GROUPME_BOT_ID,
                'text': f"🤖 BOT_11 (ARKM): {message}"
            }
            
            async with self.tg_session.post(url, json=data) as response:
                if response.status == 202:  # GroupMe returns 202 for success
                    tprint(f"📱 GroupMe sent: {message[:50]}...")
                else:
                    tprint(f"❌ GroupMe error: {response.status}")
                        
        except Exception as e:
            tprint(f"❌ Error sending GroupMe message: {e}")
    
    async def send_telegram_message(self, message: str):
        """Send message to Telegram using reusable session"""
        try:
            if self.tg_session is None:
                timeout = aiohttp.ClientTimeout(total=5)
                self.tg_session = aiohttp.ClientSession(timeout=timeout)
            
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': f"🤖 BOT_11 (ARKM): {message}",
                'parse_mode': 'HTML'
            }
            
            async with self.tg_session.post(url, json=data) as response:
                if response.status == 200:
                    tprint(f"📱 Telegram sent: {message[:50]}...")
                else:
                    tprint(f"❌ Telegram error: {response.status}")
                        
        except Exception as e:
            tprint(f"❌ Error sending Telegram message: {e}")
    
    async def get_orderbook(self) -> Dict:
        """Fetch current orderbook from MEXC using futures ticker endpoint"""
        try:
            # Using MEXC futures ticker endpoint for bid/ask prices
            url = f"https://contract.mexc.com/api/v1/contract/ticker?symbol={self.symbol}"
            
            timeout = aiohttp.ClientTimeout(total=5)  # 5 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Futures ticker format: {"success": true, "data": {"symbol": "ARKM_USDT", "bid1": "0.12340", "ask1": "0.12350", ...}}
                        ticker_data = data.get('data') or {}
                        
                        bid1 = ticker_data.get('bid1')
                        ask1 = ticker_data.get('ask1')
                        
                        if bid1 is not None and ask1 is not None:
                            self.orderbook = {
                                'bids': [[float(bid1), 0.0]],  # Best bid
                                'asks': [[float(ask1), 0.0]]   # Best ask
                            }
                            self.last_orderbook_update = time.time()
                            return self.orderbook
                    
            tprint(f"⚠️  Failed to fetch ticker: {response.status}")
            return self.orderbook
            
        except Exception as e:
            tprint(f"❌ Error fetching ticker: {e}")
            return self.orderbook
    
    async def get_best_bid_ask(self) -> Tuple[float, float]:
        """Get best bid and ask prices and save to CSV"""
        orderbook = await self.get_orderbook()
        
        if orderbook['bids'] and orderbook['asks']:
            best_bid = orderbook['bids'][0][0]  # Highest bid
            best_ask = orderbook['asks'][0][0]  # Lowest ask
            bid_vol = orderbook['bids'][0][1]   # Bid volume
            ask_vol = orderbook['asks'][0][1]   # Ask volume
            
            # Save to CSV
            self.save_orderbook_to_csv(best_bid, best_ask, bid_vol, ask_vol)
            
            return best_bid, best_ask
        
        # Fallback: use last known prices or fetch from API
        tprint("⚠️  No orderbook data, using fallback prices")
        return 0.0, 0.0
    
    async def get_account_balance(self) -> float:
        """Get current account balance"""
        try:
            tprint(f"🔍 Fetching account balance...")
            response = await self.api.get_user_assets()
            
            if response.success:
                tprint(f"✅ API response successful")
                tprint(f"📊 Response data type: {type(response.data)}")
                tprint(f"📊 Response data length: {len(response.data) if hasattr(response.data, '__len__') else 'N/A'}")
                
                for i, asset in enumerate(response.data):
                    tprint(f"   Asset {i}: {asset}")
                    
                    # Check for USDT currency
                    currency = None
                    if hasattr(asset, 'currency'):
                        currency = asset.currency
                    elif isinstance(asset, dict):
                        currency = asset.get('currency')
                    
                    if currency == 'USDT':
                        # Try different balance fields
                        balance = 0.0
                        if hasattr(asset, 'availableBalance'):
                            balance = float(asset.availableBalance)
                        elif hasattr(asset, 'available'):
                            balance = float(asset.available)
                        elif isinstance(asset, dict):
                            balance = float(asset.get('availableBalance', asset.get('available', 0)))
                        
                        self.account_balance = balance
                        tprint(f"✅ Found USDT balance: ${self.account_balance:,.2f}")
                        return self.account_balance
                
                tprint(f"⚠️  No USDT asset found in response")
                tprint(f"📊 Available assets:")
                for i, asset in enumerate(response.data):
                    if hasattr(asset, 'currency'):
                        tprint(f"   - {asset.currency}: {asset.availableBalance if hasattr(asset, 'availableBalance') else 'N/A'}")
                    elif isinstance(asset, dict):
                        tprint(f"   - {asset.get('currency', 'Unknown')}: {asset.get('availableBalance', 'N/A')}")
                
                return 0.0
            else:
                tprint(f"❌ API response failed: {response.message}")
                return 0.0
                
        except Exception as e:
            tprint(f"❌ Error fetching balance: {e}")
            tprint(f"❌ Error type: {type(e)}")
            import traceback
            tprint(f"❌ Traceback: {traceback.format_exc()}")
            return 0.0
    
    async def get_current_position(self) -> Optional[Dict]:
        """Get current open position"""
        try:
            response = await self.api.get_open_positions(self.symbol)
            if response.success and response.data:
                tprint(f"📊 Position API response: {len(response.data)} positions found")
                for i, position in enumerate(response.data):
                    # Debug: Print position data
                    if hasattr(position, 'symbol'):
                        tprint(f"   Position {i}: symbol={position.symbol}, holdVol={getattr(position, 'holdVol', 'N/A')}, vol={getattr(position, 'vol', 'N/A')}")
                    elif isinstance(position, dict):
                        tprint(f"   Position {i}: symbol={position.get('symbol')}, holdVol={position.get('holdVol', 'N/A')}, vol={position.get('vol', 'N/A')}")
                    
                    # Check if this is our symbol
                    symbol_match = False
                    if hasattr(position, 'symbol') and position.symbol == self.symbol:
                        symbol_match = True
                    elif isinstance(position, dict) and position.get('symbol') == self.symbol:
                        symbol_match = True
                    
                    if symbol_match:
                        # Extract position data with correct field names
                        # Try both holdVol and vol fields
                        position_size = 0
                        if hasattr(position, 'holdVol') and position.holdVol > 0:
                            position_size = float(position.holdVol)
                        elif hasattr(position, 'vol') and position.vol > 0:
                            position_size = float(position.vol)
                        elif isinstance(position, dict):
                            position_size = float(position.get('holdVol', 0)) or float(position.get('vol', 0))
                        
                        if position_size > 0:
                            # Convert position type to side
                            if hasattr(position, 'positionType'):
                                side = 'LONG' if position.positionType == 1 else 'SHORT'
                            elif isinstance(position, dict):
                                side = 'LONG' if position.get('positionType') == 1 else 'SHORT'
                            else:
                                side = 'UNKNOWN'
                            
                            # Get entry price
                            if hasattr(position, 'openAvgPrice'):
                                entry_price = float(position.openAvgPrice)
                            elif isinstance(position, dict):
                                entry_price = float(position.get('openAvgPrice', 0))
                            else:
                                entry_price = 0
                            
                            # Get PnL
                            if hasattr(position, 'realised'):
                                pnl = float(position.realised)
                            elif isinstance(position, dict):
                                pnl = float(position.get('realised', 0))
                            else:
                                pnl = 0
                            
                            # Get position ID
                            if hasattr(position, 'positionId'):
                                position_id = position.positionId
                            elif isinstance(position, dict):
                                position_id = position.get('positionId')
                            else:
                                position_id = None
                            
                            tprint(f"✅ Found position: {side} {position_size} @ ${entry_price:.4f}")
                        return {
                                'side': side,
                                'size': position_size,
                                'entry_price': entry_price,
                                'current_price': entry_price,  # Will be updated separately
                                'pnl': pnl,
                                'position_id': position_id
                        }
            return None
        except Exception as e:
            tprint(f"❌ Error fetching position: {e}")
            return None
    
    async def place_post_only_order(self, side: OrderSide, volume: float, price: float, 
                                  retry_count: int = 0) -> Optional[str]:
        """Place post-only maker order"""
        try:
            order_request = CreateOrderRequest(
                symbol=self.symbol,
                side=side,
                vol=volume,
                price=round(price, SYMBOL_DECIMALS),  # Use centralized decimal configuration
                leverage=self.leverage,
                type=OrderType.PostOnlyMaker,
                openType=OpenType.Isolated
            )
            
            response = await self.api.create_order(order_request)
            
            if response.success:
                order_id = str(response.data.orderId)
                tprint(f"✅ Post-only order placed: {side} {volume} @ {price:.4f}")
                return order_id
            else:
                tprint(f"❌ Order failed: {response.message}")
                return None
                
        except Exception as e:
            tprint(f"❌ Error placing order: {e}")
            return None
    
    async def wait_for_order_fill(self, order_id: str, timeout: int = 5) -> bool:
        """Wait for order to be filled"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = await self.api.get_order_by_order_id(order_id)
                
                if response.success:
                    order = response.data
                    if hasattr(order, 'state') and order.state == 'FILLED':
                        tprint(f"✅ Order filled: {order_id}")
                        return True
                    elif hasattr(order, 'state') and order.state in ['CANCELED', 'REJECTED']:
                        tprint(f"❌ Order {order.state.lower()}: {order_id}")
                        return False
                    elif isinstance(order, dict) and order.get('state') == 'FILLED':
                        tprint(f"✅ Order filled: {order_id}")
                        return True
                    elif isinstance(order, dict) and order.get('state') in ['CANCELED', 'REJECTED']:
                        tprint(f"❌ Order {order.get('state', '').lower()}: {order_id}")
                        return False
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                tprint(f"❌ Error checking order: {e}")
                await asyncio.sleep(0.5)
        
        tprint(f"⏰ Order timeout: {order_id}")
        return False
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            response = await self.api.cancel_orders([order_id])
            if response.success:
                tprint(f"✅ Order canceled: {order_id}")
                return True
            else:
                tprint(f"❌ Cancel failed: {response.message}")
                return False
        except Exception as e:
            tprint(f"❌ Error canceling order: {e}")
            return False
    
    async def monitor_order_status(self, order_id: str, timeout: int = 300) -> str:
        """Monitor order status and return status string"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                # Get order details
                response = await self.api.get_order_by_order_id(order_id)
                
                if response.success and response.data:
                    order = response.data
                    order_state = order.state
                    
                    # Map numeric states to strings
                    if order_state == 2:
                        status = "PENDING"
                    elif order_state == 3:
                        status = "FILLED"
                    elif order_state == 4:
                        status = "CANCELED"
                    else:
                        status = f"UNKNOWN({order_state})"
                    
                    tprint(f"📊 Order {order_id} status: {status}")
                    
                    if status == "FILLED":
                        return "FILLED"
                    elif status == "CANCELED":
                        return "CANCELED"
                    elif status == "PENDING":
                        tprint(f"⏳ Order pending: {order_id}")
                
                await asyncio.sleep(1)  # Check every second
                
            except Exception as e:
                tprint(f"❌ Error monitoring order {order_id}: {e}")
                await asyncio.sleep(1)
        
        # Timeout reached
        tprint(f"⏰ Order timeout: {order_id}")
        return "TIMEOUT"

    async def smart_order_placement(self, side: OrderSide, quantity: float, price: float, is_entry: bool = False) -> str:
        """Smart order placement with market orders for entries"""
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                tprint(f"🔄 Placing order (attempt {attempt + 1}/{max_retries})")
                
                # For entries, use market orders directly
                if is_entry:
                    tprint(f"📊 Using market order for entry: {side} {quantity}")
                    
                    # Create market order request
                    order_request = CreateOrderRequest(
                        symbol=self.symbol,
                        side=side,
                        vol=quantity,
                        leverage=self.leverage,
                        type=OrderType.MarketOrder,
                        openType=OpenType.Isolated
                    )
                    
                    # Place market order
                    response = await self.api.create_order(order_request)
                    
                    if response.success and response.data:
                        order_id = str(response.data.orderId)
                        tprint(f"✅ Market order placed: {side} {quantity}")
                        
                        # Store order details for position info
                        self.last_order_details = {
                            'order_id': order_id,
                            'side': side,
                            'quantity': quantity,
                            'price': price,  # Use intended price for reference
                            'filled': True
                        }
                        return order_id
                    else:
                        error_msg = response.message if hasattr(response, 'message') else 'Unknown error'
                        tprint(f"❌ Market order failed: {error_msg}")
                        if is_rate_limit_error(error_msg):
                            tprint("⏳ Rate limit detected, waiting 3 seconds...")
                            await asyncio.sleep(3)
                        elif attempt < max_retries - 1:
                            await asyncio.sleep(1)
                        else:
                            return None
                        if attempt < max_retries - 1:
                            continue
                        else:
                            return None
                
                # For non-entries, use the original limit order logic
                # Get fresh bid/ask prices for each retry attempt
                best_bid, best_ask = await self.get_best_bid_ask()
                if best_bid <= 0 or best_ask <= 0:
                    tprint(f"❌ Invalid bid/ask prices: bid={best_bid}, ask={best_ask}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    else:
                        return None
                
                # Use fresh prices for maker orders
                if side in [OrderSide.OpenLong, OrderSide.CloseShort]:
                    # Buy orders - use ask price (slightly above bid for maker)
                    fresh_price = best_ask
                else:
                    # Sell orders - use bid price (slightly below ask for maker)
                    fresh_price = best_bid
                
                tprint(f"📊 Fresh prices - Bid: ${best_bid:.4f}, Ask: ${best_ask:.4f}, Using: ${fresh_price:.4f}")
                
                # Create order request for post-only maker order
                order_request = CreateOrderRequest(
                    symbol=self.symbol,
                    side=side,
                    vol=quantity,
                    leverage=self.leverage,
                    type=OrderType.PostOnlyMaker,
                    openType=OpenType.Isolated,
                    price=round(fresh_price, SYMBOL_DECIMALS)
                )
                
                # Try post-only maker order first
                response = await self.api.create_order(order_request)
                
                if response.success and response.data:
                    order_id = str(response.data.orderId)
                    tprint(f"✅ Post-only order placed: {side} {quantity} @ {fresh_price:.4f}")
                    
                    # Monitor order status
                    order_status = await self.monitor_order_status(order_id, self.ORDER_TIMEOUT)
                    
                    if order_status == "FILLED":
                        tprint(f"✅ Order filled: {order_id}")
                        # Store order details for position info
                        self.last_order_details = {
                            'order_id': order_id,
                            'side': side,
                            'quantity': quantity,
                            'price': fresh_price,
                            'filled': True
                        }
                        return order_id
                    elif order_status == "CANCELED":
                        tprint(f"❌ Order canceled: {order_id}")
                        # Cancel the order explicitly
                        await self.api.cancel_orders([order_id])
                    else:
                        tprint(f"⏰ Order timeout: {order_id}")
                        # Cancel the order explicitly
                        await self.api.cancel_orders([order_id])
                
                # If we get here, post-only failed, try market order
                if attempt < max_retries - 1:
                    tprint(f"🔄 Order failed, retrying... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(1)
                else:
                    tprint("🚨 All retries failed, placing market order")
                    # Final attempt: market order
                    market_order_request = CreateOrderRequest(
                        symbol=self.symbol,
                        side=side,
                        vol=quantity,
                        leverage=self.leverage,
                        type=OrderType.MarketOrder,
                        openType=OpenType.Isolated
                    )
                    
                    market_response = await self.api.create_order(market_order_request)
                    
                    if market_response.success and market_response.data:
                        market_order_id = str(market_response.data.orderId)
                        tprint(f"✅ Market order placed: {side} {quantity}")
                        # Store order details for position info
                        self.last_order_details = {
                            'order_id': market_order_id,
                            'side': side,
                            'quantity': quantity,
                            'price': price,  # Use intended price for reference
                            'filled': True
                        }
                        return market_order_id
                    else:
                        error_msg = getattr(market_response, 'message', 'Unknown error')
                        tprint(f"❌ Market order failed: {error_msg}")
                        if is_rate_limit_error(error_msg):
                            tprint("⏳ Rate limit detected for final market order, waiting 3 seconds...")
                            await asyncio.sleep(3)
                        return None
                
            except Exception as e:
                tprint(f"❌ Error placing order: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    return None
        
        return None

    async def place_tp_limit_order(self, side: OrderSide, quantity: float, price: float) -> str:
        """Place a TP limit order (no market fallback)"""
        try:
            tprint(f"🎯 Placing TP LIMIT order: {side} {quantity} @ {price}")
            
            # Create order request for limit order
            order_request = CreateOrderRequest(
                symbol=self.symbol,
                side=side,
                vol=quantity,
                leverage=self.leverage,
                type=OrderType.PostOnlyMaker,  # Use PostOnlyMaker for TP orders
                openType=OpenType.Isolated,
                price=round(price, SYMBOL_DECIMALS)
            )
            
            # Place limit order
            response = await self.api.create_order(order_request)
            
            if response.success and response.data:
                order_id = str(response.data.orderId)
                tprint(f"✅ TP LIMIT order placed: {side} {quantity} @ {price}")
                return order_id
            else:
                error_msg = response.message if hasattr(response, 'message') else 'Unknown error'
                tprint(f"❌ TP LIMIT order failed: {error_msg}")
                if is_rate_limit_error(error_msg):
                    tprint("⏳ Rate limit detected for TP order, waiting 3 seconds before retry...")
                    await asyncio.sleep(3)
                    # Retry TP order once after rate limit
                    retry_response = await self.api.create_order(order_request)
                    if retry_response.success and retry_response.data:
                        order_id = str(retry_response.data.orderId)
                        tprint(f"✅ TP LIMIT order placed after retry: {side} {quantity} @ {price}")
                        return order_id
                    else:
                        tprint(f"❌ TP LIMIT order retry failed: {getattr(retry_response, 'message', 'Unknown error')}")
                return None
                
        except Exception as e:
            tprint(f"❌ Error placing TP LIMIT order: {e}")
            return None
    
    async def enter_position(self, signal_type: str, entry_price: float) -> bool:
        """Enter a new position"""
        # Prevent multiple position entries
        if self.is_entering_position:
            tprint("⚠️  Already entering position, skipping...")
            return False
            
        self.is_entering_position = True
        
        try:
            tprint(f"📈 Entering {signal_type} position at {entry_price:.4f}")
            await self.send_telegram_message(f"📈 Entering {signal_type} position at ${entry_price:.4f}")
            
            # Get current balance
            balance = await self.get_account_balance()
            if balance <= 0:
                tprint("❌ No balance available")
                await self.send_telegram_message("❌ No balance available")
                self.is_entering_position = False
                return False
            
            # Get current bid/ask for reference (market orders will execute at best available price)
            best_bid, best_ask = await self.get_best_bid_ask()
            if best_bid <= 0 or best_ask <= 0:
                tprint("❌ No valid bid/ask prices")
                await self.send_telegram_message("❌ No valid bid/ask prices")
                self.is_entering_position = False
                return False
            
            tprint(f"📊 Current Bid: ${best_bid:.4f} | Ask: ${best_ask:.4f}")
            
            # Use mid price for position size calculation (market orders will execute at best available price)
            mid_price = (best_bid + best_ask) / 2
            tprint(f"📊 Using mid price for calculation: ${mid_price:.4f}")
            
            # Calculate position value in USD (5% of balance)
            position_value_usd = balance * self.position_size_pct
            
            # Calculate contract quantity from USD value
            # With leverage, we want to control a position worth (position_value_usd * leverage)
            # Each contract = CONTRACTS_PER_SYMBOL ARKM, so we need to divide by (price * CONTRACTS_PER_SYMBOL)
            leveraged_position_value = position_value_usd * self.leverage
            position_size = leveraged_position_value / (mid_price * CONTRACTS_PER_SYMBOL)
            
            # Round position size to correct precision (ARKM allows whole contracts)
            position_size = round(position_size, 0)  # Round to whole number
            
            # Ensure minimum position size (at least MIN_CONTRACT_SIZE contract)
            if position_size < MIN_CONTRACT_SIZE:
                position_size = MIN_CONTRACT_SIZE
                tprint(f"⚠️  Position size too small, using minimum: {MIN_CONTRACT_SIZE} contract")
            
            # Ensure maximum position size
            if position_size > MAX_CONTRACT_SIZE:
                position_size = MAX_CONTRACT_SIZE
                tprint(f"⚠️  Position size too large, using maximum: {MAX_CONTRACT_SIZE} contracts")
            
            tprint(f"💰 Balance: ${balance:,.2f}")
            tprint(f"💵 Position value: ${position_value_usd:,.2f} ({self.position_size_pct*100}%)")
            tprint(f"📊 Position size: {position_size:,.0f} contracts")
            tprint(f"🔍 Debug: Balance=${balance:,.2f}, 5% margin=${position_value_usd:,.2f}, ARKM Price=${mid_price:.4f}")
            tprint(f"🔍 Debug: Leveraged position value = ${leveraged_position_value:,.2f}")
            tprint(f"🔍 Debug: Contracts = ${leveraged_position_value:,.2f} / (${mid_price:.4f} * {CONTRACTS_PER_SYMBOL}) = {position_size:,.0f}")
            tprint(f"🔍 Debug: Each contract = {CONTRACTS_PER_SYMBOL} ARKM, so {position_size:,.0f} contracts = {position_size * CONTRACTS_PER_SYMBOL:,.0f} ARKM")
            
            # Determine order side
            if signal_type == 'LONG':
                side = OrderSide.OpenLong
            else:
                side = OrderSide.OpenShort
            
            # Place entry order with market order
            order_id = await self.smart_order_placement(side, position_size, mid_price, is_entry=True)
            
            if order_id and self.last_order_details:
                tprint(f"✅ Position entered: {order_id}")
                await self.send_telegram_message(f"✅ Position entered: {order_id}")
                
                # Use stored order details instead of API position
                position = {
                    'order_id': order_id,
                    'side': signal_type,
                    'size': self.last_order_details['quantity'],
                    'entry_price': self.last_order_details['price'],
                    'filled': True
                }
                
                self.current_position = position
                await self.set_tp_sl_orders(position)
                self.is_entering_position = False
                return True
            else:
                tprint("❌ Failed to enter position")
                await self.send_telegram_message("❌ Failed to enter position")
                self.is_entering_position = False
                return False
                
        except Exception as e:
            tprint(f"❌ Error entering position: {e}")
            await self.send_telegram_message(f"❌ Error entering position: {e}")
            self.is_entering_position = False
            return False
    
    async def set_tp_sl_orders(self, position: Dict):
        """Set EFS take profit orders: TP1 at 0.8× ATR(15m), TP2 trailing at 0.5× ATR(15m)"""
        try:
            entry_price = position.get('entry_price', 0)
            position_size = abs(position.get('size', 0))
            
            # Validate position data
            if entry_price <= 0 or position_size <= 0:
                tprint(f"❌ Invalid position data: entry_price={entry_price}, size={position_size}")
                await self.send_telegram_message(f"❌ Invalid position data: entry_price={entry_price}, size={position_size}")
                return
            
            # Get the entry candle (15m candle that triggered signal)
            entry_candle = self.get_entry_candle()
            if not entry_candle:
                tprint("❌ No entry candle data for ATR calculation")
                return
            
            # Calculate ATR(15m) from the entry candle
            df = pd.DataFrame(self.historical_candles)
            df = self.calculate_indicators(df)
            if len(df) == 0:
                tprint("❌ No data for ATR calculation")
                return
            
            # Find the entry candle in our data
            entry_candle_df = df[df['datetime'].astype(str) == str(entry_candle['datetime'])]
            if len(entry_candle_df) == 0:
                tprint("❌ Entry candle not found in calculated data, using latest")
                entry_atr_15m = df.iloc[-1]['atr_15m']
            else:
                entry_atr_15m = entry_candle_df.iloc[0]['atr_15m']
            
            if pd.isna(entry_atr_15m) or entry_atr_15m <= 0:
                tprint(f"❌ Invalid ATR(15m) from entry candle: {entry_atr_15m}")
                return
            
            # Calculate TP levels based on entry candle ATR(15m)
            if position.get('side') == 'LONG':
                tp1_price = entry_price + (self.tp1_atr_multiplier * entry_atr_15m)
                tp2_price = entry_price + (self.tp2_atr_multiplier * entry_atr_15m)
                tp1_side = OrderSide.CloseLong
                
                # SL: below entry candle low (the 15m candle we broke)
                sl_price = entry_candle['low'] * 0.9997  # 0.03% buffer below entry candle low
            else:  # SHORT
                tp1_price = entry_price - (self.tp1_atr_multiplier * entry_atr_15m)
                tp2_price = entry_price - (self.tp2_atr_multiplier * entry_atr_15m)
                tp1_side = OrderSide.CloseShort
                
                # SL: above entry candle high (the 15m candle we broke)
                sl_price = entry_candle['high'] * 1.0003  # 0.03% buffer above entry candle high
            
            # Round prices to correct decimal places for symbol
            tp1_price = round(tp1_price, SYMBOL_DECIMALS)
            tp2_price = round(tp2_price, SYMBOL_DECIMALS)
            sl_price = round(sl_price, SYMBOL_DECIMALS)
            
            tprint(f"🎯 EFS Exit Conditions Setup (from entry candle):")
            tprint(f"   Entry: ${entry_price:.4f}")
            tprint(f"   Entry Candle ATR(15m): {entry_atr_15m:.6f}")
            tprint(f"   TP1: ${tp1_price:.4f} ({self.tp1_atr_multiplier}× ATR)")
            tprint(f"   TP2: ${tp2_price:.4f} ({self.tp2_atr_multiplier}× ATR trailing)")
            tprint(f"   SL: ${sl_price:.4f} (entry candle {'low' if position.get('side') == 'LONG' else 'high'} + 0.03% buffer)")
            
            # Store EFS exit conditions for monitoring
            self.efs_exit_conditions = {
                'entry_price': entry_price,
                'entry_atr_15m': entry_atr_15m,
                'tp1_price': tp1_price,
                'tp2_price': tp2_price,
                'sl_price': sl_price,
                'entry_candle_datetime': entry_candle['datetime'],
                'position_side': position.get('side'),
                'position_size': position_size,
                'bars_since_entry': 0,
                'tp2_armed': False,
                'highest_favorable_price': entry_price,
                'lowest_favorable_price': entry_price,
                'current_trail': None
            }
            
            # Set TP1 order (limit order)
            tprint(f"🎯 Creating TP1 LIMIT order: {tp1_price:.4f}")
            tprint(f"⏳ Waiting 3 seconds before placing TP1 order...")
            await asyncio.sleep(3)  # Wait 3 seconds to avoid rate limiting
            
            tp1_order_id = await self.place_tp_limit_order(tp1_side, position_size, tp1_price)
            
            if tp1_order_id:
                self.tp_order_id = tp1_order_id
                self.efs_exit_conditions['tp1_order_id'] = tp1_order_id
                tprint(f"✅ TP1 LIMIT order set: {tp1_order_id}")
                await self.send_telegram_message(f"🎯 TP1 set: ${tp1_price:.4f} ({self.tp1_atr_multiplier}× ATR)")
            else:
                tprint(f"❌ TP1 LIMIT order failed")
                await self.send_telegram_message(f"❌ TP1 order failed")
                self.efs_exit_conditions['tp1_order_id'] = None
            
            # Note: No SL order since only one limit order allowed per balance
            # SL will be monitored manually based on ATR and entry candle structure
            tprint(f"ℹ️  No SL order set (only one limit order allowed per balance)")
            tprint(f"ℹ️  SL will be monitored manually at ${sl_price:.4f}")
            await self.send_telegram_message(f"ℹ️  No SL order set (only one limit order allowed per balance)")
            await self.send_telegram_message(f"ℹ️  SL monitored manually at ${sl_price:.4f}")
            
        except Exception as e:
            tprint(f"❌ Error setting EFS TP/SL orders: {e}")
            await self.send_telegram_message(f"❌ Error setting EFS TP/SL orders: {e}")
            self.manual_tp_monitor = True
            self.manual_tp_price = entry_price * (1 + self.tp1_atr_multiplier * 0.01) if position.get('side') == 'LONG' else entry_price * (1 - self.tp1_atr_multiplier * 0.01)
    
    async def execute_sl_exit(self):
        """Execute SL exit immediately with market order"""
        try:
            tprint("🚨 EXECUTING SL EXIT - Placing market order NOW!")
            await self.send_telegram_message("🚨 EXECUTING SL EXIT - Placing market order NOW!")
            
            # 1. Cancel TP order if it exists
            if self.tp_order_id:
                tprint(f"🔄 Canceling TP order: {self.tp_order_id}")
                await self.cancel_order(self.tp_order_id)
                self.tp_order_id = None
            
            # 2. Get position details
            position_size = abs(self.current_position.get('size', 0))
            position_side = self.current_position.get('side', 'UNKNOWN')
            
            if position_size <= 0:
                tprint("❌ Invalid position size for SL exit")
                await self.handle_position_exit("SL")
                return
            
            # 3. Determine close side
            if position_side == 'LONG':
                close_side = OrderSide.CloseLong
            else:
                close_side = OrderSide.CloseShort
            
            # 4. Place market order immediately
            tprint(f"🚨 SL EXIT: Placing {close_side} market order for {position_size} contracts")
            
            close_request = CreateOrderRequest(
                symbol=self.symbol,
                side=close_side,
                vol=position_size,
                leverage=self.leverage,
                type=OrderType.MarketOrder,
                openType=OpenType.Isolated
            )
            
            close_response = await self.api.create_order(close_request)
            if close_response.success:
                tprint("✅ SL exit market order placed successfully!")
                await self.send_telegram_message("✅ SL exit market order placed successfully!")
                # Wait a moment for order to fill, then handle exit
                await asyncio.sleep(1)
                await self.handle_position_exit("SL")
            else:
                tprint(f"❌ SL exit market order failed: {close_response.message}")
                await self.send_telegram_message(f"❌ SL exit market order failed: {close_response.message}")
                # Try one more time
                await asyncio.sleep(1)
                retry_response = await self.api.create_order(close_request)
                if retry_response.success:
                    tprint("✅ SL exit retry successful!")
                    await self.handle_position_exit("SL")
                else:
                    tprint(f"❌ SL exit retry also failed: {retry_response.message}")
                    await self.send_telegram_message(f"❌ SL exit retry also failed: {retry_response.message}")
            
        except Exception as e:
            tprint(f"❌ Error executing SL exit: {e}")
            await self.send_telegram_message(f"❌ Error executing SL exit: {e}")
            # Fall back to direct exit
            await self.handle_position_exit("SL")
    
    async def execute_tp_exit(self):
        """Execute TP exit immediately with market order"""
        try:
            tprint("🎯 EXECUTING TP EXIT - Placing market order NOW!")
            await self.send_telegram_message("🎯 EXECUTING TP EXIT - Placing market order NOW!")
            
            # 1. Cancel TP order if it exists
            if self.tp_order_id:
                tprint(f"🔄 Canceling TP order: {self.tp_order_id}")
                await self.cancel_order(self.tp_order_id)
                self.tp_order_id = None
            
            # 2. Get position details
            position_size = abs(self.current_position.get('size', 0))
            position_side = self.current_position.get('side', 'UNKNOWN')
            
            if position_size <= 0:
                tprint("❌ Invalid position size for TP exit")
                await self.handle_position_exit("TP")
                return
            
            # 3. Determine close side
            if position_side == 'LONG':
                close_side = OrderSide.CloseLong
            else:
                close_side = OrderSide.CloseShort
            
            # 4. Place market order immediately
            tprint(f"🎯 TP EXIT: Placing {close_side} market order for {position_size} contracts")
            
            close_request = CreateOrderRequest(
                symbol=self.symbol,
                side=close_side,
                vol=position_size,
                leverage=self.leverage,
                type=OrderType.MarketOrder,
                openType=OpenType.Isolated
            )
            
            close_response = await self.api.create_order(close_request)
            if close_response.success:
                tprint("✅ TP exit market order placed successfully!")
                await self.send_telegram_message("✅ TP exit market order placed successfully!")
                # Wait a moment for order to fill, then handle exit
                await asyncio.sleep(1)
                await self.handle_position_exit("TP")
            else:
                tprint(f"❌ TP exit market order failed: {close_response.message}")
                await self.send_telegram_message(f"❌ TP exit market order failed: {close_response.message}")
                # Try one more time
                await asyncio.sleep(1)
                retry_response = await self.api.create_order(close_request)
                if retry_response.success:
                    tprint("✅ TP exit retry successful!")
                    await self.handle_position_exit("TP")
                else:
                    tprint(f"❌ TP exit retry also failed: {retry_response.message}")
                    await self.send_telegram_message(f"❌ TP exit retry also failed: {retry_response.message}")
            
        except Exception as e:
            tprint(f"❌ Error executing TP exit: {e}")
            await self.send_telegram_message(f"❌ Error executing TP exit: {e}")
            # Fall back to direct exit
            await self.handle_position_exit("TP")
    
    async def handle_sl_exit(self):
        """Handle SL exit with immediate market order"""
        try:
            tprint("🛑 Starting SL exit procedure...")
            
            # 1. Cancel TP order if it exists
            if self.tp_order_id:
                tprint(f"🔄 Canceling TP order: {self.tp_order_id}")
                await self.cancel_order(self.tp_order_id)
                self.tp_order_id = None
            
            # 2. Get position details
            position_size = abs(self.current_position.get('size', 0))
            position_side = self.current_position.get('side', 'UNKNOWN')
            
            if position_size <= 0:
                tprint("❌ Invalid position size for SL exit")
                await self.handle_position_exit("SL")
                return
            
            # 3. Determine close side
            if position_side == 'LONG':
                close_side = OrderSide.CloseLong
            else:
                close_side = OrderSide.CloseShort
            
            # 4. Place market order immediately
            tprint("🚨 SL hit - placing market order immediately")
            
            close_request = CreateOrderRequest(
                symbol=self.symbol,
                side=close_side,
                vol=position_size,
                leverage=self.leverage,
                type=OrderType.MarketOrder,
                openType=OpenType.Isolated
            )
            
            close_response = await self.api.create_order(close_request)
            if close_response.success:
                tprint("✅ SL exit successful with market order")
                await self.handle_position_exit("SL")
            else:
                tprint(f"❌ Market order failed: {close_response.message}")
                await self.send_telegram_message(f"❌ SL exit failed: {close_response.message}")
            
        except Exception as e:
            tprint(f"❌ Error in SL exit: {e}")
            await self.send_telegram_message(f"❌ Error in SL exit: {e}")
            # Fall back to direct exit
            await self.handle_position_exit("SL")
    
    async def handle_position_exit(self, exit_reason: str):
        """Handle position exit and send notifications"""
        try:
            # Get current balance
            balance = await self.get_account_balance()
            
            # Calculate P&L if we have position details
            if self.current_position:
                entry_price = self.current_position.get('entry_price', 0)
                position_size = abs(self.current_position.get('size', 0))
                
                if entry_price > 0 and position_size > 0:
                    # Get current price for P&L calculation
                    current_price = await self.get_current_price()
                    if current_price > 0:
                        if self.current_position.get('side') == 'LONG':
                            trade_pnl_pct = ((current_price - entry_price) / entry_price) * 100
                        else:
                            trade_pnl_pct = ((entry_price - current_price) / entry_price) * 100
                        
                        # Calculate USD PnL with leverage (5x)
                        position_value_usd = position_size * entry_price
                        trade_pnl = position_value_usd * (trade_pnl_pct / 100) * self.leverage
                        
                        tprint(f"💰 Trade P&L: ${trade_pnl:.2f} ({trade_pnl_pct:.2f}%)")
            
            tprint(f"📤 Position exit: {exit_reason}")
            await self.send_telegram_message(f"📤 Position exit: {exit_reason}")
        
            # Send GroupMe notification
            groupme_message = f"""TRADE CLOSED
Exit Reason: {exit_reason}
Position: {self.current_position.get('side', 'UNKNOWN') if self.current_position else 'UNKNOWN'}
Current Balance: ${balance:,.2f}"""
            
            if 'trade_pnl' in locals():
                groupme_message += f"\nP&L: ${trade_pnl:.2f} ({trade_pnl_pct:.2f}%)"
            
            await self.send_groupme_message(groupme_message)
            
            # Reset position tracking
            self.current_position = None
            self.tp_order_id = None
            self.manual_tp_monitor = False
            self.manual_tp_price = 0
            
            # Reset trailing stop loss
            self.trailing_sl_enabled = False
            self.trailing_sl_price = 0
            self.entry_price = 0
            self.position_side = None
            
            # Set cooldown timer
            self.last_position_close_time = time.time()
            
            tprint(f"✅ Position closed: {exit_reason}")
            tprint(f"💰 Current Balance: ${balance:,.2f}")
            await self.send_telegram_message(f"✅ Position closed: {exit_reason} | Balance: ${balance:,.2f}")
            
        except Exception as e:
            tprint(f"❌ Error handling position exit: {e}")
            await self.send_telegram_message(f"❌ Error handling position exit: {e}")
    
    async def check_position_status(self):
        """Check current position status and handle EFS TP/SL logic"""
        try:
            if not self.current_position:
                return
            
            # Get current price for monitoring
            current_price = await self.get_current_price()
            if current_price <= 0:
                return
            
            # Get position details
            position = await self.get_current_position()
            if not position or position.get('size', 0) == 0:
                tprint(f"⚠️  Position not found in API, but we think we have: {self.current_position.get('size', 0)} contracts")
                return
            
            # Update current position with latest data
            self.current_position = position
            
            entry_price = position.get('entry_price', 0)
            position_size = abs(position.get('size', 0))
            position_side = position.get('side', 'UNKNOWN')
            
            if entry_price <= 0 or position_size <= 0:
                tprint(f"⚠️  Invalid position data: entry_price={entry_price}, size={position_size}")
                return
            
            # Get current ATR and entropy data for EFS monitoring
            current_candle = self.get_current_candle()
            if not current_candle:
                return
            
            df = pd.DataFrame(self.historical_candles)
            df = self.calculate_indicators(df)
            if len(df) == 0:
                return
            
            latest = df.iloc[-1]
            current_atr_15m = latest['atr_15m']
            current_entropy = latest['entropy']
            current_entropy_zscore = latest['entropy_zscore']
            
            # Calculate current P&L
            if position_side == 'LONG':
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
            else:  # SHORT
                pnl_pct = ((entry_price - current_price) / entry_price) * 100
            
            # Get bid/ask for monitoring
            best_bid, best_ask = await self.get_best_bid_ask()
            
            # Calculate EFS TP levels (SL is static from entry candle)
            if position_side == 'LONG':
                tp1_price = entry_price + (self.tp1_atr_multiplier * current_atr_15m)
                tp2_price = entry_price + (self.tp2_atr_multiplier * current_atr_15m)
            else:  # SHORT
                tp1_price = entry_price - (self.tp1_atr_multiplier * current_atr_15m)
                tp2_price = entry_price - (self.tp2_atr_multiplier * current_atr_15m)
            
            # EFS Exit Condition 1: Entropy re-expansion to mean
            entropy_exit_triggered = False
            if not pd.isna(current_entropy_zscore) and abs(current_entropy_zscore) < 0.5:
                # Entropy has re-expanded to near mean (move lost its structure)
                entropy_exit_triggered = True
                tprint(f"🔄 EFS Exit: Entropy re-expanded to mean (z-score: {current_entropy_zscore:.2f})")
                await self.send_telegram_message(f"🔄 EFS Exit: Entropy re-expanded to mean (z-score: {current_entropy_zscore:.2f})")
            
            # EFS Exit Condition 2: TP2 trailing stop logic (handled in check_efs_exit_conditions)
            
            # Print position status every 30 seconds
            current_time = time.time()
            if not hasattr(self, 'last_position_update') or current_time - self.last_position_update >= 30:
                tp2_info = f" | TP2: ${self.efs_exit_conditions['tp2_price']:.4f}" if hasattr(self, 'efs_exit_conditions') else ""
                sl_info = f" | SL: ${self.efs_exit_conditions['sl_price']:.4f} (static)" if hasattr(self, 'efs_exit_conditions') else ""
                entropy_info = f" | Entropy: {current_entropy:.4f} (z: {current_entropy_zscore:.2f})"
                tprint(f"💰 EFS TRADE UPDATE {datetime.now().strftime('%H:%M:%S')} | {position_side} {position_size:.2f} @ ${entry_price:.4f}")
                tprint(f"   📊 Bid: ${best_bid:.4f} | Ask: ${best_ask:.4f} | Mid: ${current_price:.4f}")
                tprint(f"   🎯 TP1: ${tp1_price:.4f} ({self.tp1_atr_multiplier}× ATR){tp2_info}{sl_info}")
                tprint(f"   💵 P&L: {pnl_pct:+.3f}% (${position_size * entry_price * (pnl_pct/100) * self.leverage:.2f})")
                tprint(f"   🔍 ATR(15m): {current_atr_15m:.6f}{entropy_info}")
                self.last_position_update = current_time
            
            # Check TP1 order status if we have one
            if self.tp_order_id:
                tp_filled = await self.monitor_order_status(self.tp_order_id, 1)  # Check for 1 second
                if tp_filled == "FILLED":
                    tprint(f"🎯 TP1 order filled!")
                    await self.send_telegram_message(f"🎯 TP1 order filled!")
                    await self.handle_position_exit("TP1")
                    return
                elif tp_filled == "CANCELED":
                    tprint(f"⚠️  TP1 order canceled, setting manual monitoring")
                    self.manual_tp_monitor = True
                    self.manual_tp_price = tp1_price
                    self.tp_order_id = None
            
            # Check manual TP monitoring
            if self.manual_tp_monitor:
                if position_side == 'LONG' and current_price >= self.manual_tp_price:
                    tprint(f"🎯 Manual TP1 hit at {current_price:.4f}")
                    await self.send_telegram_message(f"🎯 Manual TP1 hit at ${current_price:.4f}")
                    
                    # Close position with market order
                    close_side = OrderSide.CloseLong if position_side == 'LONG' else OrderSide.CloseShort
                    
                    close_request = CreateOrderRequest(
                        symbol=self.symbol,
                        side=close_side,
                        vol=position_size,
                        leverage=self.leverage,
                        type=OrderType.MarketOrder,
                        openType=OpenType.Isolated
                    )
                    
                    close_response = await self.api.create_order(close_request)
                    if close_response.success:
                        tprint(f"✅ Position closed with market order")
                        await self.handle_position_exit("Manual TP1")
                    else:
                        tprint(f"❌ Failed to close position: {close_response.message}")
                    
                    self.manual_tp_monitor = False
                    self.manual_tp_price = 0
                    return
            
            # Check for entropy-based exit
            if entropy_exit_triggered:
                tprint(f"🔄 EFS entropy exit triggered - closing position")
                await self.send_telegram_message(f"🔄 EFS entropy exit triggered - closing position")
                
                # Close position with market order
                close_side = OrderSide.CloseLong if position_side == 'LONG' else OrderSide.CloseShort
                
                close_request = CreateOrderRequest(
                    symbol=self.symbol,
                    side=close_side,
                    vol=position_size,
                    leverage=self.leverage,
                    type=OrderType.MarketOrder,
                    openType=OpenType.Isolated
                )
                
                close_response = await self.api.create_order(close_request)
                if close_response.success:
                    tprint(f"✅ Position closed with entropy exit")
                    await self.handle_position_exit("EFS Entropy Exit")
                else:
                    tprint(f"❌ Failed to close position: {close_response.message}")
                
                return
            
        except Exception as e:
            tprint(f"❌ Error checking EFS position status: {e}")
    
    async def get_current_price(self) -> float:
        """Get current market price from bid/ask"""
        best_bid, best_ask = await self.get_best_bid_ask()
        return (best_bid + best_ask) / 2
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EFS indicators: Shannon entropy, ATR, fracture index, and directional bias"""
        try:
            # Basic OHLCV calculations
            df['body_size'] = abs(df['close'] - df['open'])
            df['candle_range'] = df['high'] - df['low']
            df['body_ratio'] = df['body_size'] / df['candle_range']
            df['is_green'] = df['close'] > df['open']
            df['is_red'] = df['close'] < df['open']
            
            # Calculate returns for entropy
            df['returns'] = df['close'].pct_change()
            
            # Shannon Entropy of returns (rolling window)
            df['entropy'] = df['returns'].rolling(window=self.entropy_window).apply(self.calculate_shannon_entropy)
            
            # 30-day rolling mean and std of entropy for normalization
            df['entropy_mean_30d'] = df['entropy'].rolling(ENTROPY_CALCULATION_PERIOD*24*4).mean()  # ENTROPY_CALCULATION_PERIOD days in 15m candles (4 per hour)
            df['entropy_std_30d'] = df['entropy'].rolling(ENTROPY_CALCULATION_PERIOD*24*4).std()
            
            # Entropy collapse detection (z-score)
            df['entropy_zscore'] = (df['entropy'] - df['entropy_mean_30d']) / df['entropy_std_30d']
            
            # ATR calculations for different timeframes
            df['atr_1m'] = self.calculate_atr(df, period=14)
            df['atr_15m'] = self.calculate_atr(df, period=14)  # Will be resampled to 15m
            
            # Fracture Index: ATR(15m) / ATR(1m * 15)
            # Since we're working with 1m data, we'll simulate 15m ATR by resampling
            df['fracture_index'] = self.calculate_fracture_index(df)
            
            # Directional bias filter: EMA 20 vs EMA 50 slope
            df['ema_20'] = df['close'].ewm(span=20).mean()
            df['ema_50'] = df['close'].ewm(span=50).mean()
            
            # Calculate slopes (rate of change)
            df['ema_20_slope'] = df['ema_20'].diff(5)  # 5-period slope
            df['ema_50_slope'] = df['ema_50'].diff(5)  # 5-period slope
            
            # Directional bias conditions
            df['bias_bullish'] = (df['ema_20'] > df['ema_50']) & (df['ema_20_slope'] > 0) & (df['ema_50_slope'] > 0)
            df['bias_bearish'] = (df['ema_20'] < df['ema_50']) & (df['ema_20_slope'] < 0) & (df['ema_50_slope'] < 0)
            
            # Breakout levels for entry execution
            df['high_15m'] = df['high'].rolling(15).max()  # 15-period high (simulating 15m)
            df['low_15m'] = df['low'].rolling(15).min()    # 15-period low (simulating 15m)
            
            # Volatility compression detection
            df['volatility_compression'] = df['fracture_index'] < self.fracture_compression
            
            return df
            
        except Exception as e:
            tprint(f"❌ Error calculating EFS indicators: {e}")
            return df
    
    def calculate_shannon_entropy(self, returns: pd.Series) -> float:
        """Calculate Shannon entropy of returns"""
        try:
            if len(returns) < 2:
                return 0.0
            
            # Remove NaN values
            clean_returns = returns.dropna()
            if len(clean_returns) < 2:
                return 0.0
            
            # Create histogram bins for entropy calculation
            # Use 10 bins for reasonable entropy calculation
            hist, bin_edges = np.histogram(clean_returns, bins=10, density=True)
            
            # Remove zero probabilities (log(0) is undefined)
            hist = hist[hist > 0]
            
            if len(hist) == 0:
                return 0.0
            
            # Calculate Shannon entropy: H = -sum(p * log2(p))
            entropy = -np.sum(hist * np.log2(hist))
            
            return float(entropy)
            
        except Exception as e:
            tprint(f"❌ Error calculating Shannon entropy: {e}")
            return 0.0
    
    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        try:
            high = df['high']
            low = df['low']
            close_prev = df['close'].shift(1)
            
            # True Range = max(high - low, |high - close_prev|, |low - close_prev|)
            tr1 = high - low
            tr2 = abs(high - close_prev)
            tr3 = abs(low - close_prev)
            
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            
            # ATR = exponential moving average of true range
            atr = true_range.ewm(span=period).mean()
            
            return atr
            
        except Exception as e:
            tprint(f"❌ Error calculating ATR: {e}")
            return pd.Series([0.0] * len(df))
    
    def calculate_fracture_index(self, df: pd.DataFrame) -> pd.Series:
        """Calculate Fracture Index: ATR(15m) / ATR(1m * 15)"""
        try:
            # Since we're working with 1m data, simulate 15m ATR
            # ATR(15m) = average of 15 consecutive 1m ATR values
            atr_1m = self.calculate_atr(df, period=14)
            atr_15m_simulated = atr_1m.rolling(15).mean()
            
            # Fracture Index = ATR(15m) / (ATR(1m) * 15)
            # This measures if 15m volatility is compressed relative to 1m
            fracture_index = atr_15m_simulated / (atr_1m * 15)
            
            # Handle division by zero
            fracture_index = fracture_index.replace([np.inf, -np.inf], np.nan)
            fracture_index = fracture_index.fillna(1.0)  # Default to 1.0 (no fracture)
            
            return fracture_index
            
        except Exception as e:
            tprint(f"❌ Error calculating fracture index: {e}")
            return pd.Series([1.0] * len(df))
    
    def check_efs_long_signal(self, candle: pd.Series) -> Optional[str]:
        """Check for EFS LONG signal: entropy collapse + fracture + bullish bias"""
        try:
            # 1. Entropy Collapse: entropy drops > 1.2 std below 30-day mean
            entropy_collapse = (
                candle['entropy_zscore'] < -self.entropy_std_threshold and
                not pd.isna(candle['entropy_zscore'])
            )
            
            # 2. Fracture Trigger: FI < 0.7 (compression - breakout imminent)
            fracture_trigger = (
                candle['fracture_index'] < self.fracture_compression and
                not pd.isna(candle['fracture_index'])
            )
            
            # 3. Directional Bias: EMA20 > EMA50 and both sloping up
            directional_bias = candle['bias_bullish']
            
            # 4. Breakout confirmation: price breaks above last 15m high
            breakout_confirmed = candle['close'] > candle['high_15m']
            
            if entropy_collapse and fracture_trigger and directional_bias:
                if breakout_confirmed:
                    return "EFS LONG: Entropy collapse + fracture + bullish bias + breakout"
                else:
                    return "EFS LONG: Entropy collapse + fracture + bullish bias (waiting for breakout)"
            
            return None
            
        except Exception as e:
            tprint(f"❌ Error checking EFS long signal: {e}")
            return None
    
    def check_efs_short_signal(self, candle: pd.Series) -> Optional[str]:
        """Check for EFS SHORT signal: entropy collapse + fracture + bearish bias"""
        try:
            # 1. Entropy Collapse: entropy drops > 1.2 std below 30-day mean
            entropy_collapse = (
                candle['entropy_zscore'] < -self.entropy_std_threshold and
                not pd.isna(candle['entropy_zscore'])
            )
            
            # 2. Fracture Trigger: FI < 0.7 (compression - breakdown imminent)
            fracture_trigger = (
                candle['fracture_index'] < self.fracture_compression and
                not pd.isna(candle['fracture_index'])
            )
            
            # 3. Directional Bias: EMA20 < EMA50 and both sloping down
            directional_bias = candle['bias_bearish']
            
            # 4. Breakdown confirmation: price breaks below last 15m low
            breakdown_confirmed = candle['close'] < candle['low_15m']
            
            if entropy_collapse and fracture_trigger and directional_bias:
                if breakdown_confirmed:
                    return "EFS SHORT: Entropy collapse + fracture + bearish bias + breakdown"
                else:
                    return "EFS SHORT: Entropy collapse + fracture + bearish bias (waiting for breakdown)"
            
            return None
            
        except Exception as e:
            tprint(f"❌ Error checking EFS short signal: {e}")
            return None
    
    async def generate_signal(self) -> Optional[Dict]:
        """Generate trading signal from CSV data"""
        try:
            # Check if we have enough historical data
            if len(self.historical_candles) < self.min_candles_required:
                tprint(f"⚠️  Insufficient data for signal generation: {len(self.historical_candles)} < {self.min_candles_required}")
                return None
            
            # Get current candle from CSV
            current_candle = self.get_current_candle()
            if not current_candle:
                tprint("❌ No current candle data")
                return None
            
            # Convert historical candles to DataFrame for indicator calculation
            df = pd.DataFrame(self.historical_candles)
            df = self.calculate_indicators(df)
            
            if len(df) == 0:
                return None
            
            # Use the latest candle (most recent data)
            candle = df.iloc[-1]
            
            # Debug: Print current EFS indicators
            tprint(f"🔍 EFS Signal Debug - Price: ${candle['close']:.4f}")
            tprint(f"   📊 Entropy: {candle['entropy']:.4f} | Z-Score: {candle['entropy_zscore']:.2f}")
            tprint(f"   📈 Entropy Mean: {candle['entropy_mean_30d']:.4f} | Std: {candle['entropy_std_30d']:.4f}")
            tprint(f"   🔍 Fracture Index: {candle['fracture_index']:.4f} | Compression: {candle['volatility_compression']}")
            tprint(f"   📊 ATR(1m): {candle['atr_1m']:.6f} | ATR(15m): {candle['atr_15m']:.6f}")
            tprint(f"   📈 EMA20: ${candle['ema_20']:.4f} | EMA50: ${candle['ema_50']:.4f}")
            tprint(f"   🎯 Bias: {'BULLISH' if candle['bias_bullish'] else 'BEARISH' if candle['bias_bearish'] else 'NEUTRAL'}")
            tprint(f"   📊 High_15m: ${candle['high_15m']:.4f} | Low_15m: ${candle['low_15m']:.4f}")
            tprint(f"   🕯️  Candle: {'Red' if candle['is_red'] else 'Green'} | Body Ratio: {candle['body_ratio']:.2f}")
            
            # Check for EFS signals
            long_reason = self.check_efs_long_signal(candle)
            short_reason = self.check_efs_short_signal(candle)
            
            if long_reason:
                tprint(f"✅ LONG Signal: {long_reason}")
                return {
                    'signal_type': 'LONG',
                    'reason': long_reason,
                    'entry_price': candle['close']
                }
            elif short_reason:
                tprint(f"✅ SHORT Signal: {short_reason}")
                return {
                    'signal_type': 'SHORT',
                    'reason': short_reason,
                    'entry_price': candle['close']
                }
            else:
                tprint("❌ No signal triggered")
            
            return None
            
        except Exception as e:
            tprint(f"❌ Error generating signal: {e}")
            return None
    
    async def trading_loop(self):
        """Main trading loop using EFS strategy with CSV data and real-time bid/ask"""
        tprint("🔄 Starting EFS trading loop...")
        tprint("📊 EFS Strategy:")
        tprint("   - Entropy Fracture Strategy: Novel 15m framework")
        tprint("   - Measures information entropy decay + volatility fractures")
        tprint(f"   - OHLCV data: Local CSV file ({CSV_FILENAME}) - 1-minute candles")
        tprint("   - Best bid/ask: MEXC futures API (when in position)")
        tprint("   - Entry: Entropy collapse + fracture + directional bias + breakout")
        tprint("   - Exit: TP1 (0.8× ATR), TP2 trailing (0.5× ATR), entropy re-expansion")
        tprint(f"   - CSV check interval: {CSV_CHECK_INTERVAL}s | Candle wait: {CSV_CANDLE_DETECTED_WAIT}s")
        tprint("")
        
        last_csv_update = 0
        last_data_status = 0
        last_signal_scan = 0
        last_trade_update = 0
        
        while self.is_trading:
            try:
                current_time = time.time()
                
                # Check if we have an open position
                position = await self.get_current_position()
                
                if position and position.get('size', 0) != 0:
                    # We have an open position, monitor it
                    if not self.current_position:
                        tprint(f"📊 Found existing position: {position.get('size', 0)} contracts")
                        self.current_position = position
                    elif abs(position.get('size', 0)) != abs(self.current_position.get('size', 0)):
                        # Position size changed - might be partial fill or adjustment
                        tprint(f"📊 Position size changed: {self.current_position.get('size', 0)} → {position.get('size', 0)}")
                        self.current_position = position
                    await self.check_position_status()
                else:
                    # No position detected - double check before resetting
                    if self.current_position:
                        tprint(f"⚠️  No position detected in API, but we think we have: {self.current_position.get('size', 0)} contracts")
                        # Wait a bit and check again to avoid false closure
                        await asyncio.sleep(2)
                        position_retry = await self.get_current_position()
                        if position_retry and position_retry.get('size', 0) != 0:
                            tprint(f"📊 Position still exists: {position_retry.get('size', 0)} contracts")
                            self.current_position = position_retry
                        else:
                            tprint("✅ Position confirmed closed, resetting...")
                            self.current_position = None
                            self.tp_order_id = None
                            self.manual_tp_monitor = False
                            self.manual_tp_price = 0
                            self.is_entering_position = False  # Reset entry flag
                
                # Tail CSV data for new candles (only read new lines)
                # Check every CSV_CHECK_INTERVAL seconds, but wait CSV_CANDLE_DETECTED_WAIT seconds after detecting a candle
                check_interval = CSV_CANDLE_DETECTED_WAIT if hasattr(self, 'last_candle_detected') and (current_time - self.last_candle_detected) < CSV_CANDLE_DETECTED_WAIT else CSV_CHECK_INTERVAL
                
                if current_time - last_csv_update >= check_interval:
                    new_candles = self.tail_csv_data()
                    if new_candles:
                        # Mark that we detected a candle
                        self.last_candle_detected = current_time
                        
                        tprint(f"📈 New data detected: {len(new_candles)} new candles")
                        # Recalculate indicators with new data
                        df = pd.DataFrame(self.historical_candles)
                        df = self.calculate_indicators(df)
                        if len(df) > 0:
                            latest = df.iloc[-1]
                            tprint(f"   📊 Latest: ${latest['close']:.4f} | EMA20: ${latest['ema_20']:.4f} | EMA50: ${latest['ema_50']:.4f}")
                        
                        # Trigger signal generation for new candles
                        if not self.current_position and not self.is_entering_position and len(self.historical_candles) >= self.min_candles_required:
                            # Check cooldown after position close
                            time_since_close = time.time() - self.last_position_close_time
                            if time_since_close >= POSITION_CLOSE_COOLDOWN:
                                current_candle = self.get_current_candle()
                                if current_candle:
                                    current_price = current_candle['close']
                                    tprint(f"🔍 New candle detected - Scanning at {datetime.now().strftime('%H:%M:%S')} - Price: ${current_price:.4f}")
                                    
                                    # Show indicator status
                                    if len(df) > 0:
                                        tprint(f"   📊 Entropy: {latest['entropy']:.4f} (z: {latest['entropy_zscore']:.2f})")
                                        tprint(f"   🔍 Fracture Index: {latest['fracture_index']:.4f} | Compression: {latest['volatility_compression']}")
                                        tprint(f"   📈 Bias: {'BULLISH' if latest['bias_bullish'] else 'BEARISH' if latest['bias_bearish'] else 'NEUTRAL'}")
                                        tprint(f"   📊 ATR(15m): {latest['atr_15m']:.6f} | Volume: {latest['volume']:.0f}")
                                    
                                    # Generate signal immediately for new candle
                                    signal = await self.generate_signal()
                                    if signal:
                                        signal_type = signal['signal_type']
                                        entry_price = signal['entry_price']
                                        
                                        tprint(f"📊 Signal generated: {signal_type}")
                                        tprint(f"   Entry Price: {entry_price}")
                                        await self.send_telegram_message(f"📊 Signal: {signal_type} @ ${entry_price:.4f}")
                                        
                                        # Enter position
                                        success = await self.enter_position(signal_type, entry_price)
                                        if not success:
                                            tprint("❌ Failed to enter position")
                                            await self.send_telegram_message("❌ Failed to enter position")
                            else:
                                # Show cooldown status
                                remaining_cooldown = POSITION_CLOSE_COOLDOWN - time_since_close
                                tprint(f"⏳ Cooldown active: {remaining_cooldown:.0f}s remaining before scanning resumes")
                        elif len(self.historical_candles) < self.min_candles_required:
                            # Not enough data for EFS
                            candles_missing = self.min_candles_required - len(self.historical_candles)
                            days_missing = candles_missing / (24 * 60)
                            tprint(f"⚠️  INSUFFICIENT DATA: Need {candles_missing:,} more candles ({days_missing:.1f} days) for EFS")
                            tprint(f"   ⚠️  EFS entropy calculations require {ENTROPY_CALCULATION_PERIOD} full days of data!")
                    last_csv_update = current_time
                
                # Report data status every 30 seconds (removed to reduce noise)
                # if current_time - last_data_status >= 30:
                #     if len(self.historical_candles) >= self.min_candles_required:
                #         tprint(f"✅ Data status: {len(self.historical_candles)} candles available (ready for trading)")
                #     else:
                #         tprint(f"⏳ Data status: {len(self.historical_candles)}/{self.min_candles_required} candles (waiting for more data)")
                #     last_data_status = current_time
                
                # Generate trading signal only if no position is open and we have enough data
                # (Signal generation moved to tailing section for better efficiency)
                if not self.current_position and not self.is_entering_position and len(self.historical_candles) < self.min_candles_required:
                    # Wait for more data
                    if current_time - last_data_status >= 30:  # Only print every 30 seconds
                        tprint(f"⏳ Waiting for more data: {len(self.historical_candles)}/{self.min_candles_required} candles")
                        last_data_status = current_time
                elif not self.current_position and not self.is_entering_position:
                    # Check if we're in cooldown period
                    time_since_close = time.time() - self.last_position_close_time
                    if time_since_close < POSITION_CLOSE_COOLDOWN:
                        # Show cooldown status every 10 seconds
                        if current_time - last_data_status >= 10:
                            remaining_cooldown = POSITION_CLOSE_COOLDOWN - time_since_close
                            tprint(f"⏳ Cooldown active: {remaining_cooldown:.0f}s remaining before scanning resumes")
                            last_data_status = current_time
                    else:
                        # Ready to scan for new signals
                        if current_time - last_data_status >= 30:  # Only print every 30 seconds
                            tprint(f"✅ Ready to scan for new signals")
                            last_data_status = current_time
                
                # When in position: monitor EFS exit conditions
                if self.current_position:
                    # Get fresh orderbook for price monitoring
                    await self.get_orderbook()
                    
                    # Monitor 15m candle closes for EFS exit conditions
                    await self.monitor_15m_closes()
                    
                    # Legacy TP monitoring for fallback
                    if self.tp_order_id:
                        tp_filled = await self.monitor_order_status(self.tp_order_id, 1)  # Check for 1 second
                        if tp_filled == "FILLED":
                            tprint(f"🎯 TP order filled!")
                            await self.send_telegram_message(f"🎯 TP order filled!")
                            await self.handle_position_exit("TP")
                            continue  # Skip rest of position monitoring
                    
                    # If manual TP monitor is set, check if price has moved towards TP
                    if self.manual_tp_monitor:
                        current_price = await self.get_current_price()
                        # For LONG, TP is above entry; for SHORT, TP is below entry
                        if (self.current_position['side'] == 'LONG' and current_price >= self.manual_tp_price) or \
                           (self.current_position['side'] == 'SHORT' and current_price <= self.manual_tp_price):
                            tprint(f"🎯 TP price hit! Current price: ${current_price:.4f}, TP: ${self.manual_tp_price:.4f}")
                            await self.send_telegram_message(f"🎯 TP price hit! Current price: ${current_price:.4f}, TP: ${self.manual_tp_price:.4f}")
                            # Manual TP hit - close position with market order
                            if self.current_position and self.current_position.get('side') == 'LONG':
                                close_side = OrderSide.CloseLong
                            else:
                                close_side = OrderSide.CloseShort
                            
                            close_request = CreateOrderRequest(
                                symbol=self.symbol,
                                side=close_side,
                                vol=abs(self.current_position.get('size', 0)),
                                leverage=self.leverage,
                                type=OrderType.MarketOrder,
                                openType=OpenType.Isolated
                            )
                            
                            close_response = await self.api.create_order(close_request)
                            if close_response.success:
                                tprint(f"✅ Position closed with market order")
                                await self.handle_position_exit("Manual TP")
                            else:
                                tprint(f"❌ Failed to close position: {close_response.message}")
                            
                            self.manual_tp_monitor = False
                            self.manual_tp_price = 0
                            continue # Skip rest of position monitoring
                    
                    # Show trade updates every second
                    if current_time - last_trade_update >= 1:
                        if self.orderbook['bids'] and self.orderbook['asks']:
                            current_bid = self.orderbook['bids'][0][0]
                            current_ask = self.orderbook['asks'][0][0]
                            current_price = (current_bid + current_ask) / 2
                            
                            entry_price = self.current_position['entry_price']
                            position_size = abs(self.current_position['size'])
                            
                            # Calculate distances and profit using ATR-based TP/SL
                            # Get latest data safely
                            df = pd.DataFrame(self.historical_candles)
                            if len(df) > 0:
                                df = self.calculate_indicators(df)
                                if len(df) > 0:
                                    latest = df.iloc[-1]
                                    current_atr_15m = latest.get('atr_15m', 0.001) if latest is not None else 0.001
                                else:
                                    current_atr_15m = 0.001
                            else:
                                current_atr_15m = 0.001
                            
                            if self.current_position['side'] == 'LONG':
                                tp1_price = entry_price + (self.tp1_atr_multiplier * current_atr_15m)
                                sl_price = entry_price - current_atr_15m
                                distance_to_tp = ((tp1_price - current_ask) / entry_price) * 100
                                distance_to_sl = ((current_bid - sl_price) / entry_price) * 100
                                current_profit_pct = ((current_bid - entry_price) / entry_price) * 100
                                current_profit_usd = (current_bid - entry_price) * position_size
                            else:  # SHORT
                                tp1_price = entry_price - (self.tp1_atr_multiplier * current_atr_15m)
                                sl_price = entry_price + current_atr_15m
                                distance_to_tp = ((current_bid - tp1_price) / entry_price) * 100
                                distance_to_sl = ((sl_price - current_ask) / entry_price) * 100
                                current_profit_pct = ((entry_price - current_ask) / entry_price) * 100
                                current_profit_usd = (entry_price - current_ask) * position_size
                            
                            tprint(f"💰 TRADE UPDATE {datetime.now().strftime('%H:%M:%S')} | {self.current_position['side']} {position_size:.2f} @ ${entry_price:.4f}")
                            tprint(f"   📊 Bid: ${current_bid:.4f} | Ask: ${current_ask:.4f} | Mid: ${current_price:.4f}")
                            tprint(f"   🎯 TP1: ${tp1_price:.4f} ({distance_to_tp:.3f}%) | SL: ${sl_price:.4f} ({distance_to_sl:.3f}%)")
                            tprint(f"   💵 P&L: {current_profit_pct:+.3f}% (${current_profit_usd:+.2f})")
                            
                            # Check if SL was hit (manual monitoring since no SL order)
                            if self.current_position['side'] == 'LONG':
                                if current_bid <= sl_price:
                                    # Check if we already detected SL hit
                                    if not hasattr(self, 'sl_hit_time'):
                                        self.sl_hit_time = current_time
                                        tprint(f"🛑 SL hit detected! Current bid: {current_bid:.4f}, SL: {sl_price:.4f} - Waiting 1 second...")
                                        await self.send_telegram_message(f"🛑 SL HIT DETECTED! Bid: ${current_bid:.4f}, SL: ${sl_price:.4f} - Waiting 1 second...")
                                    elif current_time - self.sl_hit_time >= 1:  # 1 second delay
                                        tprint(f"🛑 SL confirmed after 1 second! Current bid: {current_bid:.4f}, SL: {sl_price:.4f}")
                                        await self.send_telegram_message(f"🛑 SL CONFIRMED! Bid: ${current_bid:.4f}, SL: ${sl_price:.4f}")
                                        # Immediately execute SL exit with market order
                                        await self.execute_sl_exit()
                                        delattr(self, 'sl_hit_time')  # Reset for next time
                                else:
                                    # Price moved back above SL, reset timer
                                    if hasattr(self, 'sl_hit_time'):
                                        tprint(f"✅ Price moved back above SL, resetting timer")
                                        delattr(self, 'sl_hit_time')
                            else:  # SHORT
                                if current_ask >= sl_price:
                                    # Check if we already detected SL hit
                                    if not hasattr(self, 'sl_hit_time'):
                                        self.sl_hit_time = current_time
                                        tprint(f"🛑 SL hit detected! Current ask: {current_ask:.4f}, SL: {sl_price:.4f} - Waiting 1 second...")
                                        await self.send_telegram_message(f"🛑 SL HIT DETECTED! Ask: ${current_ask:.4f}, SL: ${sl_price:.4f} - Waiting 1 second...")
                                    elif current_time - self.sl_hit_time >= 1:  # 1 second delay
                                        tprint(f"🛑 SL confirmed after 1 second! Current ask: {current_ask:.4f}, SL: {sl_price:.4f}")
                                        await self.send_telegram_message(f"🛑 SL CONFIRMED! Ask: ${current_ask:.4f}, SL: ${sl_price:.4f}")
                                        # Immediately execute SL exit with market order
                                        await self.execute_sl_exit()
                                        delattr(self, 'sl_hit_time')  # Reset for next time
                                else:
                                    # Price moved back below SL, reset timer
                                    if hasattr(self, 'sl_hit_time'):
                                        tprint(f"✅ Price moved back below SL, resetting timer")
                                        delattr(self, 'sl_hit_time')
                    
                    last_trade_update = current_time
                
                # Wait 1 second before next iteration
                await asyncio.sleep(1)
                
            except ClientConnectorError as e:
                tprint(f"🚨 Network error in trading loop: {e}")
                await self.send_telegram_message("🚨 Network error, retrying in 10 s")
                await asyncio.sleep(10)
            except Exception as e:
                tprint(f"❌ Error in trading loop: {e}")
                await asyncio.sleep(5)
    
    async def start_trading(self):
        """Start live EFS trading"""
        tprint("🚀 Starting live EFS trading...")
        await self.send_telegram_message("🚀 BOT_11 (ARKM) Starting live EFS trading with CSV data...")
        
        # Get initial account balance
        balance = await self.get_account_balance()
        tprint(f"💰 Account balance: ${balance:,.2f}")
        await self.send_telegram_message(f"💰 Account balance: ${balance:,.2f}")
        tprint(f"📊 Position size: {self.position_size_pct*100}% per trade")
        tprint(f"🔍 Risk per trade: {self.risk_pct*100}%")
        
        # Ensure CSV headers
        self.ensure_csv_headers()
        
        # Check current position
        position = await self.get_current_position()
        if position:
            tprint(f"📊 Current position: {position}")
            await self.send_telegram_message(f"📊 Found existing position: {position['side']} {position['size']}")
            await self.set_tp_sl_orders(position)
        
        # Check data availability
        tprint(f"📊 Data check: {len(self.historical_candles):,}/{self.min_candles_required:,} candles available")
        
        # Validate EFS data requirements
        efs_data_ready = self.validate_efs_data_requirements()
        
        if not efs_data_ready:
            tprint(f"⏳ Waiting for {ENTROPY_CALCULATION_PERIOD} days of data before EFS can work...")
            await self.send_telegram_message(f"⏳ EFS requires {ENTROPY_CALCULATION_PERIOD} days of data - waiting for sufficient data")
            
            # Calculate how many more candles we need
            candles_missing = self.min_candles_required - len(self.historical_candles)
            days_missing = candles_missing / (24 * 60)
            tprint(f"📊 Need {candles_missing:,} more candles ({days_missing:.1f} days) for EFS to work")
        else:
            tprint("✅ Sufficient data available - EFS strategy ready to trade!")
            await self.send_telegram_message("✅ Sufficient data available - EFS strategy ready to trade!")
        
        # Start trading loop
        self.is_trading = True
        await self.send_telegram_message("✅ EFS trading loop started - monitoring for entropy collapse + fracture signals")
        await self.trading_loop()
    
    async def stop_trading(self):
        """Stop live EFS trading"""
        tprint("🛑 Stopping live EFS trading...")
        await self.send_telegram_message("🛑 BOT_11 (ARKM) Stopping live EFS trading...")
        self.is_trading = False
        
        # Cancel all orders
        try:
            await self.api.cancel_all_orders(self.symbol)
            await self.api.cancel_all_trigger_orders(self.symbol)
            tprint("✅ All orders canceled")
            await self.send_telegram_message("✅ All orders canceled")
        except Exception as e:
            tprint(f"❌ Error canceling orders: {e}")
            await self.send_telegram_message(f"❌ Error canceling orders: {e}")
        
        # Close telegram session
        if self.tg_session:
            await self.tg_session.close()
            self.tg_session = None
            tprint("✅ Telegram session closed")
    
    def validate_efs_data_requirements(self) -> bool:
        """Validate that we have sufficient data for EFS strategy"""
        try:
            if len(self.historical_candles) < self.min_candles_required:
                days_available = len(self.historical_candles) / (24 * 4)  # Convert 15m candles to days (4 per hour)
                days_needed = ENTROPY_CALCULATION_PERIOD
                days_missing = days_needed - days_available
                
                tprint(f"❌ INSUFFICIENT DATA FOR EFS STRATEGY!")
                tprint(f"   Available: {len(self.historical_candles):,} candles ({days_available:.1f} days)")
                tprint(f"   Required: {self.min_candles_required:,} candles ({days_needed} days)")
                tprint(f"   Missing: {days_missing:.1f} days of data")
                tprint(f"   ⚠️  EFS entropy calculations require {days_needed} full days of data!")
                tprint(f"   ⚠️  Strategy will not work properly until sufficient data is available!")
                
                return False
            else:
                days_available = len(self.historical_candles) / (24 * 4)  # Convert 15m candles to days (4 per hour)
                tprint(f"✅ SUFFICIENT DATA FOR EFS STRATEGY!")
                tprint(f"   Available: {len(self.historical_candles):,} candles ({days_available:.1f} days)")
                tprint(f"   Required: {self.min_candles_required:,} candles ({ENTROPY_CALCULATION_PERIOD} days)")
                tprint(f"   ✅ Entropy calculations will work properly!")
                
                return True
                
        except Exception as e:
            tprint(f"❌ Error validating EFS data requirements: {e}")
            return False
    
    def get_current_15m_candle_close(self) -> Optional[Dict]:
        """Get the most recent completed 15m candle"""
        if not self.csv_data:
            return None
        
        current_time = datetime.now()
        
        # Look for the last completed 15m candle
        for i in range(len(self.csv_data) - 1, -1, -1):
            candle = self.csv_data[i]
            candle_time = pd.to_datetime(candle['datetime'])
            
            # Must be a 15m boundary and completed (not current minute)
            if candle_time.minute % 15 == 0 and candle_time < current_time:
                return candle
        
        return None
    
    async def check_efs_exit_conditions(self):
        """Check all EFS exit conditions on 15m candle close"""
        try:
            if not hasattr(self, 'efs_exit_conditions') or not self.current_position:
                return
            
            conditions = self.efs_exit_conditions
            
            # Get current price
            current_price = await self.get_current_price()
            if current_price <= 0:
                return
            
            # Get current 15m candle data
            current_candle = self.get_current_15m_candle_close()
            if not current_candle:
                return
            
            # Calculate indicators for current candle
            df = pd.DataFrame(self.historical_candles)
            if len(df) == 0:
                return
            
            df = self.calculate_indicators(df)
            if len(df) == 0:
                return
            
            latest = df.iloc[-1]
            if latest is None:
                return
            
            # Safely get indicator values with fallbacks
            current_atr_15m = latest.get('atr_15m', 0.001)
            current_entropy_zscore = latest.get('entropy_zscore', 0.0)
            
            # Validate ATR value
            if pd.isna(current_atr_15m) or current_atr_15m <= 0:
                current_atr_15m = 0.001
            
            # Update bars since entry
            conditions['bars_since_entry'] += 1
            
            position_side = conditions['position_side']
            entry_price = conditions['entry_price']
            entry_atr = conditions['entry_atr_15m']
            
            # Calculate current MFE (Maximum Favorable Excursion)
            if position_side == 'LONG':
                mfe = current_price - entry_price
                conditions['highest_favorable_price'] = max(conditions['highest_favorable_price'], current_price)
            else:  # SHORT
                mfe = entry_price - current_price
                conditions['lowest_favorable_price'] = min(conditions['lowest_favorable_price'], current_price)
            
            tprint(f"🔍 EFS Exit Check - Bars: {conditions['bars_since_entry']} | MFE: ${mfe:.4f} | Price: ${current_price:.4f}")
            
            # EXIT PRIORITY (check in order):
            
            # 1. Hard SL (entry candle structure)
            sl_hit = False
            if position_side == 'LONG' and current_price <= conditions['sl_price']:
                sl_hit = True
            elif position_side == 'SHORT' and current_price >= conditions['sl_price']:
                sl_hit = True
            
            if sl_hit:
                tprint(f"🛑 EFS SL HIT - Entry candle structure breached!")
                await self.send_telegram_message(f"🛑 EFS SL HIT - Entry candle structure breached!")
                await self.execute_sl_exit()
                return
            
            # 2. TP1 (fixed target) - already handled by limit order
            if conditions.get('tp1_order_id'):
                tp_filled = await self.monitor_order_status(conditions['tp1_order_id'], 1)
                if tp_filled == "FILLED":
                    tprint(f"🎯 TP1 filled!")
                    await self.send_telegram_message(f"🎯 TP1 filled!")
                    await self.handle_position_exit("TP1")
                    return
            
            # 3. Entropy re-expansion exit
            if not pd.isna(current_entropy_zscore) and abs(current_entropy_zscore) < 0.5:
                tprint(f"🔄 EFS Entropy Re-expansion Exit - z-score: {current_entropy_zscore:.2f}")
                await self.send_telegram_message(f"🔄 EFS Entropy Re-expansion Exit - z-score: {current_entropy_zscore:.2f}")
                await self.execute_tp_exit()
                return
            
            # 4. TP2 Trailing Stop Logic
            await self.check_tp2_trailing_conditions(conditions, current_price, current_atr_15m, mfe, entry_atr)
            
        except Exception as e:
            tprint(f"❌ Error checking EFS exit conditions: {e}")
    
    async def check_tp2_trailing_conditions(self, conditions, current_price, current_atr, mfe, entry_atr):
        """Check TP2 trailing stop conditions and logic"""
        try:
            position_side = conditions['position_side']
            entry_price = conditions['entry_price']
            bars_since_entry = conditions['bars_since_entry']
            
            # Check if TP2 should be armed
            if not conditions['tp2_armed']:
                # Arming conditions:
                # 1. MFE threshold: unrealized ≥ +0.55 × ATR(15m) from entry
                mfe_threshold = 0.55 * entry_atr
                
                # 2. Bar confirmation: 1 closed 15m bar above entry + 0.30×ATR
                bar_confirmation_level = entry_price + (0.30 * entry_atr) if position_side == 'LONG' else entry_price - (0.30 * entry_atr)
                bar_confirmation = (position_side == 'LONG' and current_price > bar_confirmation_level) or \
                                 (position_side == 'SHORT' and current_price < bar_confirmation_level)
                
                # 3. Warm-up bars: never arm before 2 bars elapsed since entry
                warmup_complete = bars_since_entry >= 2
                
                if mfe >= mfe_threshold and bar_confirmation and warmup_complete:
                    conditions['tp2_armed'] = True
                    # Initialize trailing stop using snapshot entry_atr
                    if position_side == 'LONG':
                        conditions['current_trail'] = conditions['highest_favorable_price'] - (0.50 * entry_atr)
                    else:
                        conditions['current_trail'] = conditions['lowest_favorable_price'] + (0.50 * entry_atr)
                    
                    tprint(f"🎯 TP2 TRAILING ARMED! MFE: ${mfe:.4f} ≥ ${mfe_threshold:.4f} | Bars: {bars_since_entry} | Trail: ${conditions['current_trail']:.4f}")
                    await self.send_telegram_message(f"🎯 TP2 TRAILING ARMED! Trail: ${conditions['current_trail']:.4f}")
                else:
                    tprint(f"⏳ TP2 not armed - MFE: ${mfe:.4f}/{mfe_threshold:.4f} | Bar conf: {bar_confirmation} | Warmup: {warmup_complete}")
                    tprint(f"   📊 Details: Entry ATR: {entry_atr:.6f} | Current ATR: {current_atr:.6f} | Bar conf level: {bar_confirmation_level:.4f}")
            
            # If TP2 is armed, update trailing stop
            if conditions['tp2_armed']:
                if position_side == 'LONG':
                    # Update highest favorable price
                    conditions['highest_favorable_price'] = max(conditions['highest_favorable_price'], current_price)
                    # Trail = max(previous_trail, HFP - 0.50 × entry_atr) - only ratchet tighter
                    new_trail = conditions['highest_favorable_price'] - (0.50 * entry_atr)
                    conditions['current_trail'] = max(conditions['current_trail'], new_trail)
                    
                    # Break-even shift: when MFE ≥ 0.30 × ATR, bump stop to max(current_stop, entry)
                    if mfe >= (0.30 * entry_atr):
                        conditions['current_trail'] = max(conditions['current_trail'], entry_price)
                    
                    # Check if trailing stop hit
                    if current_price <= conditions['current_trail']:
                        tprint(f"🎯 TP2 TRAILING STOP HIT! Price: ${current_price:.4f} ≤ Trail: ${conditions['current_trail']:.4f}")
                        await self.send_telegram_message(f"🎯 TP2 TRAILING STOP HIT! Price: ${current_price:.4f} ≤ Trail: ${conditions['current_trail']:.4f}")
                        await self.execute_tp_exit()
                        return
                    
                else:  # SHORT
                    # Update lowest favorable price
                    conditions['lowest_favorable_price'] = min(conditions['lowest_favorable_price'], current_price)
                    # Trail = min(previous_trail, LFP + 0.50 × entry_atr) - only ratchet tighter
                    new_trail = conditions['lowest_favorable_price'] + (0.50 * entry_atr)
                    conditions['current_trail'] = min(conditions['current_trail'], new_trail)
                    
                    # Break-even shift: when MFE ≥ 0.30 × ATR, bump stop to min(current_stop, entry)
                    if mfe >= (0.30 * entry_atr):
                        conditions['current_trail'] = min(conditions['current_trail'], entry_price)
                    
                    # Check if trailing stop hit
                    if current_price >= conditions['current_trail']:
                        tprint(f"🎯 TP2 TRAILING STOP HIT! Price: ${current_price:.4f} ≥ Trail: ${conditions['current_trail']:.4f}")
                        await self.send_telegram_message(f"🎯 TP2 TRAILING STOP HIT! Price: ${current_price:.4f} ≥ Trail: ${conditions['current_trail']:.4f}")
                        await self.execute_tp_exit()
                        return
                
                tprint(f"📊 TP2 Trailing - HFP: ${conditions.get('highest_favorable_price', 0):.4f} | Trail: ${conditions['current_trail']:.4f} (0.50× entry ATR)")
        
        except Exception as e:
            tprint(f"❌ Error in TP2 trailing logic: {e}")
    
    async def monitor_15m_closes(self):
        """Monitor for 15m candle closes to trigger exit condition checks"""
        try:
            if not hasattr(self, 'last_15m_candle_time'):
                self.last_15m_candle_time = None
            
            current_candle = self.get_current_15m_candle_close()
            if not current_candle:
                return
            
            candle_time = current_candle['datetime']
            
            # Check if this is a new 15m candle close
            if self.last_15m_candle_time != candle_time:
                self.last_15m_candle_time = candle_time
                tprint(f"📅 New 15m candle closed: {candle_time}")
                
                # Run EFS exit condition checks
                await self.check_efs_exit_conditions()
        
        except Exception as e:
            tprint(f"❌ Error monitoring 15m closes: {e}")

async def main():
    """Main function"""
    # Check if API token is configured
    if MEXC_API_TOKEN == "YOUR_MEXC_API_TOKEN_HERE":
        tprint("❌ ERROR: Please add your MEXC API token in the configuration section at the top of the file!")
        tprint("   Edit the MEXC_API_TOKEN variable with your actual token.")
        return
    
    # Check if CSV file exists
    if not os.path.exists(CSV_FILENAME):
        tprint(f"❌ ERROR: {CSV_FILENAME} file not found!")
        tprint(f"   Please ensure {CSV_FILENAME} exists and is being updated with live 15-minute OHLCV data.")
        return
    
    # Initialize EFS trader
    trader = LiveEFSTrader()
    
    try:
        # Start EFS trading
        await trader.start_trading()
    except KeyboardInterrupt:
        tprint("\n🛑 Interrupted by user")
        await trader.send_telegram_message("🛑 BOT_11 (ARKM) EFS trading interrupted by user")
    except Exception as e:
        tprint(f"❌ Fatal error: {e}")
        await trader.send_telegram_message(f"❌ BOT_11 (ARKM) EFS trading fatal error: {e}")
    finally:
        # Stop EFS trading
        await trader.stop_trading()

if __name__ == "__main__":
    tprint("🚀 Live Entropy Fracture Strategy (EFS) Trader - ARKM (CSV Data)")
    tprint("⚠️  WARNING: This is live trading software!")
    tprint("   Make sure to:")
    tprint("   1. Add your MEXC API token")
    tprint("   2. Test on testnet first")
    tprint("   3. Review all EFS parameters")
    tprint("   4. Monitor the bot carefully")
    tprint(f"   5. Ensure {CSV_FILENAME} is being updated with live 15-minute OHLCV data")
    tprint("")
    tprint("🔍 EFS Strategy Overview:")
    tprint("   - Entropy collapse detection (z-score < -1.2)")
    tprint("   - Fracture index compression (FI < 0.7)")
    tprint("   - Directional bias filter (EMA20 vs EMA50 slope)")
    tprint("   - Breakout entry execution")
    tprint("   - ATR-based TP/SL management")
    tprint("   - Entropy re-expansion exit")
    tprint("")
    tprint("📊 CRITICAL DATA REQUIREMENTS:")
    tprint(f"   - EFS requires {ENTROPY_CALCULATION_PERIOD} FULL DAYS of 15-minute data")
    tprint(f"   - Minimum: {MIN_CANDLES_REQUIRED:,} candles ({MIN_CANDLES_REQUIRED/(24*4):.1f} days)")
    tprint("   - Without sufficient data, entropy calculations will fail")
    tprint("   - Strategy will not work until data requirements are met")
    tprint("")
    
    # Run the trader
    asyncio.run(main())