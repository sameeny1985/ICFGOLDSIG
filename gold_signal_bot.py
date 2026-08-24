# ============================================================
#  gold_signal_bot.py  - نسخه پایدار + لاگ کامل
# ============================================================

from flask import Flask
import threading
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import sys
from datetime import datetime
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "OK", 200

@app.route("/health")
def health():
    return "OK", 200

# ====================== تنظیمات ======================
SYMBOL = "GC=F"

RSI_PERIOD         = 8
RSI_OVERBUY        = 70
RSI_OVERSELL       = 30

STO_OVERBUY_CRS    = 70
STO_OVERSELL_CRS   = 30
STO_OVERBUY_EXT    = 80
STO_OVERSELL_EXT   = 20

BB_PERIOD          = 20
BB_DEV             = 2.0

MA1_PERIOD         = 10
MA2_PERIOD         = 21

ADX_PERIOD         = 8
ADX_TREND_LEVEL    = 12.0          # کمی نرم‌تر

COOLDOWN_MINUTES   = 2

BANDWALK_MAX_TOUCHES = 5           # کمی نرم‌تر
BB_TOUCH_TOLERANCE   = 0.0015

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8669710314:AAGzGTAfGoNGnE6eELqxZVe4SWDTDBE0Qlc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003714269439")

last_signal_time = None
last_bar_time = None
bot_started = False

def log(msg):
    """لاگ با فلش فوری تا در Render دیده شود"""
    print(msg, flush=True)
    sys.stdout.flush()

# ====================== TELEGRAM ======================
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            log(f"Telegram error: {r.text}")
        else:
            log("Telegram sent OK")
    except Exception as e:
        log(f"Telegram failed: {e}")

# ====================== اندیکاتورها ======================
def calc_sma(series, period):
    return series.rolling(window=period).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_stochastic(high, low, close, k_period=5, d_period=3, smooth_k=3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    k_smooth = k.rolling(window=smooth_k).mean()
    d = k_smooth.rolling(window=d_period).mean()
    return k_smooth, d

def calc_bollinger(series, period=20, std_dev=2.0):
    mid = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower

def calc_adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = low.diff().abs() * -1
    plus_dm = plus_dm.where((plus_dm > high.diff().shift(-0)) & (plus_dm > 0), 0.0)
    # ساده شده
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx, plus_di, minus_di

# ====================== فیلتر بولینگر ======================
def is_real_touch_lower(df):
    try:
        touched = (
            df['low'].iloc[-2] <= df['BBL'].iloc[-2] * (1.0 + BB_TOUCH_TOLERANCE) or
            df['low'].iloc[-3] <= df['BBL'].iloc[-3] * (1.0 + BB_TOUCH_TOLERANCE)
        )
        if not touched:
            return False
        touch_count = sum(
            1 for i in range(-6, -1)
            if df['low'].iloc[i] <= df['BBL'].iloc[i] * (1.0 + BB_TOUCH_TOLERANCE)
        )
        if touch_count >= BANDWALK_MAX_TOUCHES:
            return False
        return df['close'].iloc[-1] >= df['BBL'].iloc[-1] * (1.0 - BB_TOUCH_TOLERANCE * 0.5)
    except:
        return False

def is_real_touch_upper(df):
    try:
        touched = (
            df['high'].iloc[-2] >= df['BBU'].iloc[-2] * (1.0 - BB_TOUCH_TOLERANCE) or
            df['high'].iloc[-3] >= df['BBU'].iloc[-3] * (1.0 - BB_TOUCH_TOLERANCE)
        )
        if not touched:
            return False
        touch_count = sum(
            1 for i in range(-6, -1)
            if df['high'].iloc[i] >= df['BBU'].iloc[i] * (1.0 - BB_TOUCH_TOLERANCE)
        )
        if touch_count >= BANDWALK_MAX_TOUCHES:
            return False
        return df['close'].iloc[-1] <= df['BBU'].iloc[-1] * (1.0 + BB_TOUCH_TOLERANCE * 0.5)
    except:
        return False

# ====================== منطق سیگنال ======================
def check_signals(df):
    global last_signal_time
    if len(df) < 50:
        return

    try:
        stoch_k = df['STOCHk'].iloc[-1]
        stoch_d = df['STOCHd'].iloc[-1]
        stoch_k_prev = df['STOCHk'].iloc[-2]
        stoch_d_prev = df['STOCHd'].iloc[-2]
        rsi = df['RSI'].iloc[-1]
        adx = df['ADX'].iloc[-1]
        plus_di = df['DMP'].iloc[-1]
        minus_di = df['DMN'].iloc[-1]
        ma_fast = df['MA_FAST'].iloc[-1]
        ma_slow = df['MA_SLOW'].iloc[-1]
    except Exception as e:
        log(f"Indicator read error: {e}")
        return

    buy_ready = False
    sell_ready = False

    if (stoch_k_prev < stoch_d_prev and stoch_k > stoch_d and stoch_k <= STO_OVERSELL_CRS):
        buy_ready = True
    if (stoch_k_prev > stoch_d_prev and stoch_k < stoch_d and stoch_k >= STO_OVERBUY_CRS):
        sell_ready = True

    if buy_ready and not is_real_touch_lower(df):
        buy_ready = False
    if sell_ready and not is_real_touch_upper(df):
        sell_ready = False

    if buy_ready and ma_fast < ma_slow:
        buy_ready = False
    if sell_ready and ma_fast > ma_slow:
        sell_ready = False

    if buy_ready:
        if adx < ADX_TREND_LEVEL or minus_di > plus_di + 3.0:
            buy_ready = False
    if sell_ready:
        if adx < ADX_TREND_LEVEL or plus_di > minus_di + 3.0:
            sell_ready = False

    if buy_ready and rsi > RSI_OVERBUY:
        buy_ready = False
    if sell_ready and rsi < RSI_OVERSELL:
        sell_ready = False

    now = datetime.utcnow()
    if last_signal_time and (now - last_signal_time).total_seconds() < COOLDOWN_MINUTES * 60:
        return

    price = float(df['close'].iloc[-1])
    time_str = df.index[-1].strftime("%Y-%m-%d %H:%M")

    if buy_ready and stoch_k > STO_OVERSELL_EXT:
        msg = f"📈 <b>Signal: BUY</b>\nSymbol: GOLD (GC=F)\nPrice: {price:.2f}\nTime: {time_str} UTC"
        log(f">>> BUY SIGNAL @ {price:.2f}")
        send_telegram(msg)
        last_signal_time = now

    if sell_ready and stoch_k < STO_OVERBUY_EXT:
        msg = f"📉 <b>Signal: SELL</b>\nSymbol: GOLD (GC=F)\nPrice: {price:.2f}\nTime: {time_str} UTC"
        log(f">>> SELL SIGNAL @ {price:.2f}")
        send_telegram(msg)
        last_signal_time = now

# ====================== داده ======================
def get_data():
    try:
        df = yf.download(SYMBOL, period="5d", interval="1m", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty or len(df) < 50:
            log("yfinance returned empty or too short data")
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume'
        })

        df['STOCHk'], df['STOCHd'] = calc_stochastic(df['high'], df['low'], df['close'], 5, 3, 3)
        df['BBU'], df['BBM'], df['BBL'] = calc_bollinger(df['close'], BB_PERIOD, BB_DEV)
        df['RSI'] = calc_rsi(df['close'], RSI_PERIOD)
        df['ADX'], df['DMP'], df['DMN'] = calc_adx(df['high'], df['low'], df['close'], ADX_PERIOD)
        df['MA_FAST'] = calc_sma(df['close'], MA1_PERIOD)
        df['MA_SLOW'] = calc_sma(df['close'], MA2_PERIOD)

        df = df.dropna()
        return df
    except Exception as e:
        log(f"get_data error: {e}")
        return None

# ====================== حلقه اصلی ======================
def bot_loop():
    global last_bar_time
    log("=" * 50)
    log("🚀 Gold Signal Bot STARTED")
    log(f"Symbol: {SYMBOL}")
    log("=" * 50)

    send_telegram("🤖 <b>Gold Signal Bot is ONLINE</b>\nReady to send signals...")

    while True:
        try:
            df = get_data()
            if df is not None and len(df) > 50:
                current_bar = df.index[-1]
                price = float(df['close'].iloc[-1])

                if last_bar_time is None or current_bar > last_bar_time:
                    last_bar_time = current_bar
                    log(f"New bar: {current_bar} | Close: {price:.2f}")
                    check_signals(df)
                else:
                    log(f"Same bar: {current_bar} | Close: {price:.2f}")
            else:
                log("No valid data this round")

            time.sleep(45)   # کمی بیشتر برای پایداری

        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(60)

def start_bot():
    global bot_started
    if not bot_started:
        bot_started = True
        t = threading.Thread(target=bot_loop, daemon=True)
        t.start()
        log("Bot thread launched")

# شروع ربات وقتی ماژول لود می‌شود
start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
