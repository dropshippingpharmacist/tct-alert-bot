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
MIN_RR_RATIO = 1.0
TIMEFRAMES = ["15m", "1h", "4h"]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "FARTUSDT", "PEPEUSDT"]  # Expanded symbols list
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

def fetch_binance_price(symbol):
    url = f"https://api.binance.us/api/v3/ticker/price"
    params = {"symbol": symbol}
    r = requests.get(url, params=params)
    if r.status_code == 200:
        return float(r.json()["price"])
    return None

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

def detect_po3_schematic(df):
    if len(df) < 15:
        return None
    recent = df.iloc[-15:]
    range_high = recent["high"].max()
    range_low = recent["low"].min()
    mid = (range_high + range_low) / 2
    if recent["price"].iloc[-1] < mid and recent["price"].iloc[-5] > range_high:
        return {"type": "PO3", "direction": "short", "entry": mid, "stop": range_high, "target": range_low}
    if recent["price"].iloc[-1] > mid and recent["price"].iloc[-5] < range_low:
        return {"type": "PO3", "direction": "long", "entry": mid, "stop": range_low, "target": range_high}
    return None

def detect_model_schematics(df, zones, liquidity):
    if len(df) < 15:
        return []
    setups = []
    recent = df.iloc[-10:]
    price_now = recent["price"].iloc[-1]
    high = recent["high"].max()
    low = recent["low"].min()
    mid = (high + low) / 2

    if price_now < mid and recent["price"].iloc[-5] < low:
        setups.append({"type": "Model 1", "direction": "long", "entry": mid, "stop": low, "target": high})
    elif price_now > mid and recent["price"].iloc[-5] > high:
        setups.append({"type": "Model 1", "direction": "short", "entry": mid, "stop": high, "target": low})

    if recent["price"].iloc[-1] > recent["price"].iloc[-2] > recent["price"].iloc[-3]:
        setups.append({"type": "Model 2 Accumulation", "direction": "long", "entry": mid, "stop": low, "target": high})
    elif recent["price"].iloc[-1] < recent["price"].iloc[-2] < recent["price"].iloc[-3]:
        setups.append({"type": "Model 2 Distribution", "direction": "short", "entry": mid, "stop": high, "target": low})

    return setups

def detect_tct_setup(df):
    setups = []
    liquidity = detect_liquidity_zones(df)
    zones = detect_supply_demand_zones(df)
    po3 = detect_po3_schematic(df)
    models = detect_model_schematics(df, zones, liquidity)

    def confidence(entry, stop, target):
        rr = abs(target - entry) / abs(entry - stop)
        conf = 0.6 + min(rr / 10, 0.4)
        return rr, round(conf, 2)

    if po3:
        rr, conf = confidence(po3['entry'], po3['stop'], po3['target'])
        if rr >= MIN_RR_RATIO:
            leverage = calculate_optimal_leverage(rr)
            setups.append({"type": po3["type"], **po3, "rr": rr, "confidence": conf, "leverage": leverage, "time": df.index[-1]})

    for model in models:
        rr, conf = confidence(model['entry'], model['stop'], model['target'])
        if rr >= MIN_RR_RATIO:
            leverage = calculate_optimal_leverage(rr)
            setups.append({"type": model["type"], **model, "rr": rr, "confidence": conf, "leverage": leverage, "time": df.index[-1]})

    return setups

async def run():
    print("Running TCT Alert Bot with Full Lecture Logic + Model Detection + Binance US Live Prices...")
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

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())

