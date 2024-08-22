# hedge.py

import asyncio
import json
import time
import logging
from decimal import Decimal
from typing import Dict, List, Any
import requests
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
import eth_account
import websockets
import os
from datetime import datetime
import sqlite3
import yaml

# Load configuration
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Hedge-specific configuration
hedge_config = config['hedge']
API_WALLET_ADDRESS = hedge_config['api_wallet_address']
USER_WALLET_ADDRESS = hedge_config['user_wallet_address']
PRIVATE_KEY = hedge_config['private_key']

# Shared configuration
TELEGRAM_BOT_TOKEN = config['telegram']['bot_token']
TELEGRAM_CHAT_ID = config['telegram']['chat_id']

# Price adjustment for limit orders (in basis points)
PRICE_ADJUSTMENT_BPS = hedge_config['price_adjustment_bps']
logger.info(f"Loaded PRICE_ADJUSTMENT_BPS: {PRICE_ADJUSTMENT_BPS}")

# Initialize Hyperliquid SDK
account = eth_account.Account.from_key(PRIVATE_KEY)
info = Info(constants.MAINNET_API_URL)
exchange = Exchange(account, constants.MAINNET_API_URL, account_address=API_WALLET_ADDRESS)

# Track our positions
positions = {}
sz_decimals = config['sz_decimals']
total_short_amount = {}

# File to store last processed timestamp
LAST_PROCESSED_TIME_FILE = hedge_config['last_processed_time_file']

# Database functions
def create_tables():
    """Create necessary database tables if they don't exist."""
    conn = sqlite3.connect(hedge_config['database_file'])
    c = conn.cursor()
    
    # Create trades table
    c.execute('''CREATE TABLE IF NOT EXISTS trades
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp INTEGER,
                  exchange TEXT,
                  asset TEXT,
                  side TEXT,
                  amount REAL,
                  price REAL,
                  fees_earned REAL,
                  fees_asset TEXT,
                  matched INTEGER)''')
    
    # Create trade_pairs table
    c.execute('''CREATE TABLE IF NOT EXISTS trade_pairs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp INTEGER,
                  asset TEXT,
                  chainflip_id INTEGER,
                  hyperliquid_id INTEGER,
                  chainflip_amount REAL,
                  chainflip_price REAL,
                  hyperliquid_amount REAL,
                  hyperliquid_price REAL,
                  fees_earned REAL,
                  pnl REAL,
                  pnl_percentage REAL,
                  processed INTEGER DEFAULT 0)''')
    
    conn.commit()
    conn.close()
    logger.info("Database tables created or already exist.")

def record_trade(exchange, asset, side, amount, price, fees_earned=None, fees_asset=None):
    """Record a trade in the database."""
    conn = sqlite3.connect(hedge_config['database_file'])
    c = conn.cursor()
    try:
        if exchange == 'Chainflip':
            c.execute("""
                INSERT INTO trades (timestamp, exchange, asset, side, amount, price, fees_earned, fees_asset, matched)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (int(time.time()), exchange, asset, side, amount, price, fees_earned, fees_asset, 0))
        else:  # Hyperliquid
            c.execute("""
                INSERT INTO trades (timestamp, exchange, asset, side, amount, price, matched)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (int(time.time()), exchange, asset, side, amount, price, 0))

        trade_id = c.lastrowid
        conn.commit()
        logger.info(f"Recorded trade: {exchange} {asset} {side} {amount} @ {price}" +
                    (f", Fees: {fees_earned} {fees_asset}" if fees_earned is not None else ""))
        return trade_id
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
    except Exception as e:
        logger.error(f"Error inserting trade: {e}")
    finally:
        conn.close()

def insert_trade_pair(asset, chainflip_id, hyperliquid_id, chainflip_amount, chainflip_price, 
                      hyperliquid_amount, hyperliquid_price, fees_earned, pnl, pnl_percentage):
    """Insert a trade pair into the database."""
    conn = sqlite3.connect(hedge_config['database_file'])
    c = conn.cursor()
    try:
        c.execute("""INSERT INTO trade_pairs 
                     (timestamp, asset, chainflip_id, hyperliquid_id, chainflip_amount, chainflip_price,
                      hyperliquid_amount, hyperliquid_price, fees_earned, pnl, pnl_percentage)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                  (int(time.time()), asset, chainflip_id, hyperliquid_id, chainflip_amount, chainflip_price,
                   hyperliquid_amount, hyperliquid_price, fees_earned, pnl, pnl_percentage))
        conn.commit()
        logger.info(f"Inserted trade pair: {asset} - Chainflip ID: {chainflip_id}, Hyperliquid ID: {hyperliquid_id}, Fees Earned: {fees_earned}")
    except sqlite3.Error as e:
        logger.error(f"Database error when inserting trade pair: {e}")
    except Exception as e:
        logger.error(f"Error inserting trade pair: {e}")
    finally:
        conn.close()

# Utility functions
def round_size(size: float, sz_decimals: int) -> float:
    """Round the size to the specified number of decimal places."""
    return round(size, sz_decimals)

def round_price(price: float) -> float:
    """Round the price to a suitable precision."""
    rounded = round(float(f"{price:.5g}"), 6)
    logger.info(f"Rounding price: original={price}, rounded={rounded}")
    return rounded

def save_last_processed_time(timestamp):
    """Save the last processed timestamp to a file."""
    with open(LAST_PROCESSED_TIME_FILE, 'w') as f:
        f.write(str(float(timestamp)))  # Save as float
    logger.info(f"Updated last processed time to {datetime.fromtimestamp(timestamp)}")

def load_last_processed_time():
    """Load the last processed timestamp from a file."""
    if os.path.exists(LAST_PROCESSED_TIME_FILE):
        with open(LAST_PROCESSED_TIME_FILE, 'r') as f:
            timestamp = float(f.read().strip())  # Read as float
            logger.info(f"Loaded last processed time: {datetime.fromtimestamp(timestamp)}")
            return timestamp
    logger.info("No last processed time found, starting from 0")
    return 0.0  # Return 0.0 if file doesn't exist

# Async functions
async def fetch_metadata() -> Dict[str, int]:
    """Fetch metadata from Hyperliquid."""
    meta = info.meta()
    global sz_decimals
    sz_decimals = {asset['name']: asset['szDecimals'] for asset in meta['universe'] if asset['name'] in ['ETH', 'BTC', 'DOT']}
    logger.info(f"Fetched metadata: {sz_decimals}")
    return sz_decimals

async def send_telegram_message(message: str):
    """Send a message via Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Telegram message sent: {message}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {str(e)}")

async def update_leverage(symbol: str, leverage: int):
    """Update the leverage for a given symbol."""
    try:
        result = exchange.update_leverage(leverage, symbol, is_cross=False)
        logger.info(f"Updated leverage to {leverage}x for {symbol}")
    except Exception as e:
        logger.error(f"Failed to update leverage for {symbol}: {str(e)}")

async def execute_perpetual_order(symbol: str, side: str, amount: float, price: float, reduce_only: bool):
    """Execute a perpetual order on Hyperliquid."""
    logger.info(f"Executing perpetual order: {side} {amount} {symbol} at {price}")
    global positions
    global total_short_amount

    try:
        await update_leverage(symbol, 1) #Adjust your Hyperliquid leverage here. Set to 1 initially. 

        if symbol not in sz_decimals:
            error_message = f"No size decimal information for {symbol}. Skipping order."
            logger.error(error_message)
            await send_telegram_message(f"❌ Skipped order: {error_message}")
            return

        rounded_amount = round_size(amount, sz_decimals[symbol])
        rounded_price = round_price(price)
        is_buy = side.lower() == "buy"

        if rounded_amount == 0:
            logger.info(f"Order size adjusted to 0 for {symbol}. Skipping order.")
            return

        order_type = "limit"
        logger.info(f"Placing {order_type} {side.upper()} order: {rounded_amount} {symbol} at {rounded_price}")
        await send_telegram_message(
            f"🚀 Placing {order_type} {side.upper()} order: {rounded_amount} {symbol} at {rounded_price}")

        # Place the order using the correct method
        result = exchange.order(symbol, is_buy, rounded_amount, rounded_price, {"limit": {"tif": "Gtc"}})

        if result is None or result.get('status') != 'ok':
            error_message = result.get('error', 'Unknown error') if result else "No result returned from exchange"
            logger.error(f"Failed to execute {side} order for {rounded_amount} {symbol}: {error_message}")
            await send_telegram_message(f"❌ Order failed: {side.upper()} {rounded_amount} {symbol} - {error_message}")
            return

        statuses = result['response']['data']['statuses']
        for status in statuses:
            if 'resting' in status:
                order_id = status['resting']['oid']
                logger.info(f"Limit order placed: ID {order_id}")
                await send_telegram_message(f"📝 Limit order placed: ID {order_id}")
                # Record the trade
                hyperliquid_trade_id = record_trade("Hyperliquid", symbol, side, rounded_amount, rounded_price)
                return hyperliquid_trade_id
            elif 'filled' in status:
                filled_amount = status['filled']['totalSz']
                filled_price = status['filled']['avgPx']
                logger.info(f"Order immediately filled: {filled_amount} {symbol} at {filled_price}")
                await send_telegram_message(f"✅ Order immediately filled: {filled_amount} {symbol} at {filled_price}")
                # Record the trade
                hyperliquid_trade_id = record_trade("Hyperliquid", symbol, side, filled_amount, filled_price)
                return hyperliquid_trade_id
            elif 'error' in status:
                logger.error(f"Order error: {status['error']}")
                await send_telegram_message(f"❌ Order error: {status['error']}")

    except Exception as e:
        error_message = f"Error executing {side} order for {rounded_amount} {symbol}: {str(e)}"
        logger.error(error_message)
        await send_telegram_message(f"❌ Error: {error_message}")

    return None

async def process_order_fill(fill: Dict[str, Any]):
    """Process an order fill from Chainflip and execute a hedging order on Hyperliquid."""
    symbol = fill["base_asset"]
    chainflip_side = fill["side"]
    amount = fill["amount"]
    chainflip_price = fill["price"]
    fees_earned = fill.get("fees_earned_asset")
    fees_asset = fill.get("fees_asset")

    # Map Arbitrum ETH to regular ETH for hedging
    hedging_symbol = "ETH" if symbol == "ARBITRUM_ETH" else symbol

    logger.info(f"Processing Chainflip {chainflip_side.upper()} order: {amount} {symbol} at {chainflip_price}")
    await send_telegram_message(
        f"🔄 Processing Chainflip {chainflip_side.upper()} order: {amount} {symbol} at {chainflip_price}")

    # Record the Chainflip trade with fees
    chainflip_trade_id = record_trade("Chainflip", symbol, chainflip_side, amount, chainflip_price, fees_earned, fees_asset)

    if hedging_symbol not in sz_decimals:
        logger.error(f"No size decimal information for {hedging_symbol}. Skipping order.")
        await send_telegram_message(f"❌ Skipped order: No size decimal information for {hedging_symbol}")
        return

    rounded_amount = round_size(amount, sz_decimals[hedging_symbol])

    # Reverse the side for hedging and adjust the price
    if chainflip_side == "buy":
        hyperliquid_side = "sell"
        adjustment_bps = PRICE_ADJUSTMENT_BPS.get(hedging_symbol, {}).get('sell', -5)  # Default to -5 if not specified
    else:
        hyperliquid_side = "buy"
        adjustment_bps = PRICE_ADJUSTMENT_BPS.get(hedging_symbol, {}).get('buy', 16)  # Default to 16 if not specified

    logger.info(f"Price adjustment: {adjustment_bps} bps for {hyperliquid_side.upper()} order")

    adjustment_factor = 1 + (adjustment_bps / 10000)
    logger.info(f"Adjustment factor: {adjustment_factor}")

    adjusted_price = chainflip_price * adjustment_factor
    adjusted_price = round_price(adjusted_price)

    logger.info(f"Original price: {chainflip_price}, Adjusted price: {adjusted_price}")

    expected_adjustment = chainflip_price * (adjustment_bps / 10000)
    actual_adjustment = adjusted_price - chainflip_price
    logger.info(f"Expected price adjustment: {expected_adjustment}, Actual price adjustment: {actual_adjustment}")

    if abs(expected_adjustment - actual_adjustment) > 0.01:  # Check if difference is more than 1 cent
        logger.warning(f"Large discrepancy in price adjustment. Expected: {expected_adjustment}, Actual: {actual_adjustment}")

    logger.info(
        f"Placing Hyperliquid {hyperliquid_side.upper()} order: {rounded_amount} {hedging_symbol} at adjusted price {adjusted_price}")
    hyperliquid_trade_id = await execute_perpetual_order(hedging_symbol, hyperliquid_side, rounded_amount, adjusted_price, False)

    if hyperliquid_trade_id:
        # Calculate PnL
        if chainflip_side == "buy":
            pnl = (adjusted_price - chainflip_price) * amount
        else:
            pnl = (chainflip_price - adjusted_price) * amount

        # Convert fees to USDC if they're in the traded asset
        fees_in_usdc = fees_earned * chainflip_price if fees_asset == symbol else fees_earned

        # Add fees to PnL
        pnl += fees_in_usdc

        # Calculate PnL percentage
        trade_value = chainflip_price * amount
        pnl_percentage = (pnl / trade_value) * 100

        # Insert the trade pair into the database
        insert_trade_pair(symbol, chainflip_trade_id, hyperliquid_trade_id, amount, chainflip_price,
                          rounded_amount, adjusted_price, fees_in_usdc, pnl, pnl_percentage)

# WebSocket related functions
async def websocket_manager():
    """Manage WebSocket connections and subscriptions."""
    uri = config['hyperliquid_ws_url']
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                logger.info("WebSocket connected")
                subscriptions = [
                    {"method": "subscribe", "subscription": {"type": "orderUpdates", "user": USER_WALLET_ADDRESS}},
                    {"method": "subscribe", "subscription": {"type": "userFills", "user": USER_WALLET_ADDRESS}},
                    {"method": "subscribe", "subscription": {"type": "userEvents", "user": USER_WALLET_ADDRESS}},
                    {"method": "subscribe", "subscription": {"type": "notification", "user": USER_WALLET_ADDRESS}},
                    {"method": "subscribe", "subscription": {"type": "webData2", "user": USER_WALLET_ADDRESS}}
                ]

                for sub in subscriptions:
                    await websocket.send(json.dumps(sub))
                    await websocket.recv()  # Wait for response but don't log it

                logger.info("All subscriptions sent and confirmed")

                while True:
                    message = await websocket.recv()
                    try:
                        parsed_message = json.loads(message)
                        await process_websocket_message(parsed_message)
                    except json.JSONDecodeError:
                        logger.error("Failed to parse WebSocket message")
                    except Exception as e:
                        logger.error(f"Error processing WebSocket message: {str(e)}")

        except websockets.exceptions.ConnectionClosed:
            logger.error("WebSocket connection closed. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"WebSocket error: {str(e)}. Reconnecting...")
            await asyncio.sleep(5)

async def process_websocket_message(message):
    """Process incoming WebSocket messages."""
    if isinstance(message, dict):
        channel = message.get("channel")
        data = message.get("data")

        if channel == "orderUpdates":
            await process_order_updates(data)
        elif channel == "userFills":
            await process_user_fills(data)
        elif channel == "userEvents":
            await process_user_events(data)

async def process_order_updates(updates):
    """Process order updates from WebSocket."""
    for update in updates:
        update_info = f"Order update: {update['order']['coin']} - Status: {update['status']}, Size: {update['order']['sz']}"
        logger.info(update_info)
        if update['status'] == 'filled':
            await send_telegram_message(f"📝 {update_info}")

async def process_user_fills(data):
    """Process user fills from WebSocket."""
    if isinstance(data, dict) and 'fills' in data:
        fills = data['fills']
        for fill in fills:
            logger.info(f"Fill: {fill['coin']} - Price: {fill['px']}, Size: {fill['sz']}, Side: {fill['side'].upper()}")

async def process_user_events(events):
    """Process user events from WebSocket."""
    for event in events:
        if "fills" in event:
            for fill in event["fills"]:
                logger.info(
                    f"Fill event: {fill['coin']} - Price: {fill['px']}, Size: {fill['sz']}, Side: {fill['side'].upper()}")
        elif "funding" in event:
            funding = event["funding"]
            funding_info = f"Funding payment: {funding['coin']} - Amount: {funding['usdc']}, Rate: {funding['fundingRate']}"
            logger.info(funding_info)
            await send_telegram_message(f"💰 {funding_info}")

# Account and order management functions
async def check_balance():
    """Check account balance and positions."""
    try:
        response = info.user_state(USER_WALLET_ADDRESS)

        margin_summary = response.get('marginSummary', {})
        cross_margin_summary = response.get('crossMarginSummary', {})

        account_value = margin_summary.get('accountValue', '0.0')
        cross_account_value = cross_margin_summary.get('accountValue', '0.0')
        withdrawable = response.get('withdrawable', '0.0')

        logger.info(f"Account Summary:")
        logger.info(f"  Account Value: {account_value} USDC")
        logger.info(f"  Cross Margin Account Value: {cross_account_value} USDC")
        logger.info(f"  Withdrawable: {withdrawable} USDC")

        global positions
        positions = {}
        for pos in response.get("assetPositions", []):
            position_data = pos.get("position", {})
            coin = position_data.get("coin")
            size = position_data.get("szi")
            entry_price = position_data.get("entryPx")
            if coin in ['ETH', 'BTC', 'DOT'] and size is not None and float(size) != 0:
                positions[coin] = {
                    "size": float(size),
                    "entry_price": float(entry_price) if entry_price else None
                }

        if positions:
            logger.info("Current positions:")
            for coin, data in positions.items():
                logger.info(f"  {coin}: Size: {data['size']}, Entry Price: {data['entry_price']}")
        else:
            logger.info("No open positions")

        return float(account_value)
    except Exception as e:
        logger.error(f"Error checking balance: {str(e)}")
        return None

async def check_open_orders():
    """Check and log open orders."""
    try:
        open_orders = info.frontend_open_orders(USER_WALLET_ADDRESS)
        if open_orders:
            logger.info("Open orders:")
            for order in open_orders:
                if order['coin'] in ['ETH', 'BTC', 'DOT']:
                    logger.info(
                        f"  {order['coin']} - Side: {order['side'].upper()}, Size: {order['sz']}, Price: {order['limitPx']}")
        else:
            logger.info("No open orders")
    except Exception as e:
        logger.error(f"Error checking open orders: {str(e)}")

# Main function
async def main():
    """Main function to run the hedging bot."""
    script_start_time = time.time()
    create_tables()  # Create tables if they don't exist
    await fetch_metadata()

    websocket_task = asyncio.create_task(websocket_manager())

    last_processed_time = load_last_processed_time()
    last_balance_check = 0
    last_open_orders_check = 0
    last_order_fill_check = 0
    last_summary_log = 0
    balance_check_interval = hedge_config['check_intervals']['balance']
    open_orders_check_interval = hedge_config['check_intervals']['open_orders']
    order_fill_check_interval = hedge_config['check_intervals']['order_fill']
    summary_log_interval = hedge_config['check_intervals']['summary_log']

    iteration_count = 0
    log_every_n_iterations = hedge_config['log_iterations']

    processed_trades = set()  # Set to keep track of processed trade IDs

    while True:
        try:
            current_time = time.time()  # Use float time
            iteration_count += 1

            if iteration_count % log_every_n_iterations == 0:
                logger.info(f"Main loop iteration {iteration_count} at {datetime.fromtimestamp(current_time)}")

            if current_time - last_balance_check >= balance_check_interval:
                account_value = await check_balance()
                last_balance_check = current_time

            if current_time - last_open_orders_check >= open_orders_check_interval:
                await check_open_orders()
                last_open_orders_check = current_time

            if current_time - last_order_fill_check >= order_fill_check_interval:
                # Process new order fills
                try:
                    with open(config['order_fill_file'], 'r') as f:
                        lines = f.readlines()
                        new_fills_count = 0
                        trades_buffer = []
                        current_timestamp = None

                        for line in reversed(lines):
                            fill = json.loads(line)
                            fill_time = float(fill["timestamp"])
                            trade_id = f"{fill['base_asset']}_{fill['amount']}_{fill['price']}_{fill_time}"

                            if fill_time > max(last_processed_time, script_start_time) and trade_id not in processed_trades:
                                if current_timestamp is None:
                                    current_timestamp = fill_time
                                
                                if fill_time == current_timestamp:
                                    trades_buffer.append(fill)
                                else:
                                    # Process buffered trades
                                    for buffered_trade in reversed(trades_buffer):
                                        buffered_trade_id = f"{buffered_trade['base_asset']}_{buffered_trade['amount']}_{buffered_trade['price']}_{buffered_trade['timestamp']}"
                                        if buffered_trade_id not in processed_trades:
                                            logger.info(f"Processing new order fill: {buffered_trade}")
                                            await process_order_fill(buffered_trade)
                                            processed_trades.add(buffered_trade_id)
                                            new_fills_count += 1
                                    
                                    # Clear buffer and start new batch
                                    trades_buffer = [fill]
                                    current_timestamp = fill_time
                            elif fill_time <= script_start_time:
                                break  # Stop processing older fills

                        # Process any remaining trades in the buffer
                        for buffered_trade in reversed(trades_buffer):
                            buffered_trade_id = f"{buffered_trade['base_asset']}_{buffered_trade['amount']}_{buffered_trade['price']}_{buffered_trade['timestamp']}"
                            if buffered_trade_id not in processed_trades:
                                logger.info(f"Processing new order fill: {buffered_trade}")
                                await process_order_fill(buffered_trade)
                                processed_trades.add(buffered_trade_id)
                                new_fills_count += 1

                        if new_fills_count > 0:
                            last_processed_time = current_timestamp
                            save_last_processed_time(last_processed_time)
                            logger.info(f"Processed {new_fills_count} new order fills. Last processed time: {datetime.fromtimestamp(last_processed_time)}")
                        elif iteration_count % log_every_n_iterations == 0:
                            logger.info(f"No new order fills. Last processed time: {datetime.fromtimestamp(last_processed_time)}")
                except FileNotFoundError:
                    logger.warning(f"{config['order_fill_file']} not found. Waiting for file to be created.")
                except json.JSONDecodeError:
                    logger.error(f"Error decoding JSON from {config['order_fill_file']}")

                last_order_fill_check = current_time

            if current_time - last_summary_log >= summary_log_interval:
                logger.info(f"Summary - Account Value: {account_value} USDC, Open Positions: {positions}")
                last_summary_log = current_time

        except Exception as e:
            logger.error(f"Unexpected error in main loop: {str(e)}")

        await asyncio.sleep(1)  # Sleep for 1 second before next iteration

    websocket_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
