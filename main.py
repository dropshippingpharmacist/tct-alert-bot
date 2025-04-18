import os
import time
import json
import requests
import pandas as pd
import asyncio
from datetime import datetime, timedelta
from telegram import Bot
import sys

# === CONFIGURATION ===
TELEGRAM_BOT_TOKEN = "8199048602:AAEyxtzEB_5kDkwSbo0xnnO4GB-W8MHWkdA"
TELEGRAM_CHAT_ID = "5747777199"
FAST_MODE = False
CHECK_INTERVAL = 180 if not FAST_MODE else 1  # in seconds
RISK_PERCENTAGE = 1.0
MIN_RR_RATIO = 2.0
TIMEFRAMES = ["15m", "1h", "4h"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "PEPEUSDT"]
FARTCOIN_SYMBOL = "FARTCOIN_USDT"  # Virtual representation
FARTCOIN_API_URL = "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=fartcoin_usdt"
FARTCOIN_OHLC_URL = "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=fartcoin_usdt&interval=1h&limit=168"
RUN_BACKTEST = "--backtest" in sys.argv

bot = Bot(token=TELEGRAM_BOT_TOKEN)

def calculate_optimal_leverage(rr):
    if rr >= 4:
        return 5
    elif rr >= 3:
        return 3
    elif rr >= 2:
        return 2
    return 1

def fetch_binance_ohlcv(symbol, interval="1h", limit=168):
    url = "https://api.binance.us/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params)
    if r.status_code == 200:
        data = r.json()
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume", "num_trades", "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("date", inplace=True)
        df = df[["open", "high", "low", "close"]].astype(float)
        df.rename(columns={"close": "price"}, inplace=True)
        return df
    return None

def fetch_fartcoin_ohlcv():
    r = requests.get(FARTCOIN_OHLC_URL)
    if r.status_code == 200:
        data = r.json()
        df = pd.DataFrame(data, columns=["timestamp", "volume", "close", "high", "low", "open"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("date", inplace=True)
        df = df[["open", "high", "low", "close"]].astype(float)
        df.rename(columns={"close": "price"}, inplace=True)
        return df
    return None

def fetch_fartcoin_price():
    try:
        r = requests.get(FARTCOIN_API_URL)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                return float(data[0]["last"])
    except:
        pass
    return None

def fetch_binance_price(symbol):
    url = f"https://api.binance.us/api/v3/ticker/price"
    params = {"symbol": symbol}
    r = requests.get(url, params=params)
    if r.status_code == 200:
        return float(r.json()["price"])
    return None

# ... [The rest of the functions remain unchanged]

async def run():
    print("Running TCT Alert Bot with Full Lecture Logic + Model Detection + Binance US + Fartcoin Full TCT Support...")
    active_alerts = {}
    while True:
        for symbol in SYMBOLS:
            df = fetch_binance_ohlcv(symbol)
            live_price = fetch_binance_price(symbol)
            if df is not None and live_price is not None:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} Live Price: ${live_price:.2f}")
                df = identify_ranges(df)
                setups = detect_tct_setup(df)

                if setups:
                    for setup in setups:
                        key = f"{symbol}_{setup['direction']}_{setup['type']}"
                        if key not in active_alerts:
                            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📣 TCT Alert - {symbol}\n{setup['type']} {setup['direction'].upper()}\nEntry: {setup['entry']:.2f}, Stop: {setup['stop']:.2f}, Target: {setup['target']:.2f}\nRR: {setup['rr']:.2f}:1 | Confidence: {setup['confidence']*100:.0f}% | Leverage: {setup['leverage']}x")
                            active_alerts[key] = setup

                for key in list(active_alerts):
                    sym, direction, _ = key.split("_")
                    active = active_alerts[key]
                    if live_price is not None:
                        if (direction == "long" and live_price < active["stop"]) or \
                           (direction == "short" and live_price > active["stop"]):
                            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"🚨 CANCEL - {sym} setup invalidated")
                            del active_alerts[key]

        fart_price = fetch_fartcoin_price()
        fart_df = fetch_fartcoin_ohlcv()
        if fart_price:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] FARTCOIN Live Price: ${fart_price:.6f}")
        if fart_df is not None:
            fart_df = identify_ranges(fart_df)
            fart_setups = detect_tct_setup(fart_df)
            if fart_setups:
                for setup in fart_setups:
                    key = f"FARTCOIN_{setup['direction']}_{setup['type']}"
                    if key not in active_alerts:
                        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📣 TCT Alert - FARTCOIN\n{setup['type']} {setup['direction'].upper()}\nEntry: {setup['entry']:.6f}, Stop: {setup['stop']:.6f}, Target: {setup['target']:.6f}\nRR: {setup['rr']:.2f}:1 | Confidence: {setup['confidence']*100:.0f}% | Leverage: {setup['leverage']}x")
                        active_alerts[key] = setup

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
