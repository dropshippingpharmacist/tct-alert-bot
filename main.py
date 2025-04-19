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
CHECK_INTERVAL = 180 if not FAST_MODE else 1
RISK_PERCENTAGE = 1.0
MIN_RR_RATIO = 2.0
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "PEPEUSDT"]

bot = Bot(token=TELEGRAM_BOT_TOKEN)

def calculate_optimal_leverage(rr):
    if rr >= 4: return 5
    elif rr >= 3: return 3
    elif rr >= 2: return 2
    return 1

def fetch_binance_ohlcv(symbol, interval="1h", limit=200):
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

def fetch_binance_price(symbol):
    url = f"https://api.binance.us/api/v3/ticker/price"
    params = {"symbol": symbol}
    r = requests.get(url, params=params)
    if r.status_code == 200:
        return float(r.json()["price"])
    return None

def detect_fvg(df):
    fvg_list = []
    for i in range(2, len(df)):
        if df['low'].iloc[i] > df['high'].iloc[i-2]:
            fvg_list.append((df.index[i], 'bullish'))
        elif df['high'].iloc[i] < df['low'].iloc[i-2]:
            fvg_list.append((df.index[i], 'bearish'))
    return fvg_list

def identify_ranges(df):
    df["high"] = df["price"].rolling(6).max()
    df["low"] = df["price"].rolling(6).min()
    df["mid"] = (df["high"] + df["low"]) / 2
    df["range"] = df["high"] - df["low"]
    df.dropna(inplace=True)
    return df

def detect_liquidity_zones(df):
    liquidity = {"above": [], "below": []}
    for i in range(2, len(df)-2):
        if df["price"].iloc[i] > df["price"].iloc[i-1] and df["price"].iloc[i] > df["price"].iloc[i+1]:
            liquidity["above"].append((df.index[i], df["price"].iloc[i]))
        if df["price"].iloc[i] < df["price"].iloc[i-1] and df["price"].iloc[i] < df["price"].iloc[i+1]:
            liquidity["below"].append((df.index[i], df["price"].iloc[i]))
    return liquidity

def detect_supply_demand_zones(df):
    zones = {"supply": [], "demand": []}
    for i in range(2, len(df)-2):
        if df["price"].iloc[i] < df["price"].iloc[i-1] and df["price"].iloc[i+1] > df["price"].iloc[i]:
            zones["demand"].append((df.index[i], df["price"].iloc[i]))
        elif df["price"].iloc[i] > df["price"].iloc[i-1] and df["price"].iloc[i+1] < df["price"].iloc[i]:
            zones["supply"].append((df.index[i], df["price"].iloc[i]))
    return zones

def trend_filter(df):
    return df['price'].iloc[-1] > df['price'].rolling(200).mean().iloc[-1]

def detect_order_blocks(df):
    ob_list = []
    for i in range(1, len(df)-1):
        if df['price'].iloc[i] > df['open'].iloc[i] and df['open'].iloc[i] < df['close'].iloc[i] < df['high'].iloc[i]:
            if df['close'].iloc[i+1] > df['high'].iloc[i]:
                ob_list.append((df.index[i], 'bullish'))
        elif df['price'].iloc[i] < df['open'].iloc[i] and df['open'].iloc[i] > df['close'].iloc[i] > df['low'].iloc[i]:
            if df['close'].iloc[i+1] < df['low'].iloc[i]:
                ob_list.append((df.index[i], 'bearish'))
    return ob_list

def detect_tct_setup(df, htf_df):
    setups = []
    liquidity = detect_liquidity_zones(df)
    zones = detect_supply_demand_zones(df)
    fvg = detect_fvg(df)
    obs = detect_order_blocks(df)

    high = df['high'].iloc[-15:].max()
    low = df['low'].iloc[-15:].min()
    mid = (high + low) / 2

    price_now = df['price'].iloc[-1]
    rr = abs(high - mid) / abs(mid - low)
    confidence = 0.6 + min(rr / 10, 0.4)
    score = 0

    if trend_filter(htf_df): score += 1
    if len(fvg) > 0: score += 1
    if len(obs) > 0: score += 1
    if rr >= 2.5: score += 1
    if any(df.index[-1] == i[0] for i in liquidity['above'] + liquidity['below']): score += 1

    if score >= 3:
        direction = 'long' if price_now > mid else 'short'
        setups.append({"type": "TCT Combo", "direction": direction, "entry": mid, "stop": low if direction == 'long' else high, "target": high if direction == 'long' else low, "rr": rr, "confidence": confidence, "leverage": calculate_optimal_leverage(rr), "time": df.index[-1]})

    return setups

async def run():
    print("Running Advanced TCT Bot with Multi-TF Confirmation...")
    active_alerts = {}
    while True:
        for symbol in SYMBOLS:
            df = fetch_binance_ohlcv(symbol, interval="15m")
            htf_df = fetch_binance_ohlcv(symbol, interval="1h")
            live_price = fetch_binance_price(symbol)
            if df is not None and htf_df is not None and live_price is not None:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {symbol} Live Price: ${live_price:.2f}")
                df = identify_ranges(df)
                setups = detect_tct_setup(df, htf_df)

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

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())


