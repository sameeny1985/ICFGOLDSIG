# ============================================================
#  gold_signal_bot.py  (نسخه نهایی پایدار برای Render)
#  بدون pandas_ta - اندیکاتورها دستی محاسبه می‌شوند
#  منطق سیگنال دقیقاً همان v2.3
# ============================================================

from flask import Flask
import threading
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
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
ADX_TREND_LEVEL    = 15.0

COOLDOWN_MINUTES   = 2

BANDWALK_MAX_TOUCHES = 4
BB_TOUCH_TOLERANCE   = 0.0010

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8669710314:AAGzGTAfGoNGnE6eELqxZVe4SWDTDBE0Qlc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003714269439")

last_signal_time = None
last_bar_time = None
bot_started = False

# ====================== TELEGRAM ======================
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"Telegram error: {r.text}")
        else:
            print("Telegram sent successfully")
    except Exception as e:
        print(f"Telegram send failed: {e}")

# ====================== اندیکاتورهای دستی ======================
def calc_sma(series, period):
    return series.rolling(window=period).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_stochastic(high, low, close, k_period=5, d_period=3, smooth_k=3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low)
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
    minus_dm = low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
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
        touch_count = 0
        for i in range(-6, -1):
            if df['low'].iloc[i] <= df['BBL'].iloc[i] * (1.0 + BB_TOUCH_TOLERANCE):
                touch_count += 1
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
        touch_count = 0
        for i in range(-6, -1):
            if df['high'].iloc[i] >= df['BBU'].iloc[i] * (1.0 - BB_TOUCH_TOLERANCE):
                touch_count += 1
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
        print(f"Indicator error: {e}")
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
        if adx < ADX_TREND_LEVEL:
            buy_ready = False
        if minus_di > plus_di + 3.0:
            buy_ready = False
    if sell_ready:
        if adx < ADX_TREND_LEVEL:
            sell_ready = False
        if plus_di > minus_di + 3.0:
            sell_ready = False

    if buy_ready and rsi > RSI_OVERBUY:
        buy_ready = False
    if sell_ready and rsi < RSI_OVERSELL:
        sell_ready = False

    now = datetime.utcnow()
    if last_signal_time is not None:
        if (now - last_signal_time).total_seconds() < COOLDOWN_MINUTES * 60:
            return

    price = df['close'].iloc[-1]
    time_str = df.index[-1].strftime("%Y-%m-%d %H:%M")

    if buy_ready and stoch_k > STO_OVERSELL_EXT:
        msg = (f"📈 <b>Signal: BUY</b>\nSymbol: GOLD (GC=F)\nPrice: {price:.2f}\nTime: {time_str} UTC")
        print(f"[{time_str}] BUY signal @ {price:.2f}")
        send_telegram(msg)
        last_signal_time = now

    if sell_ready and stoch_k < STO_OVERBUY_EXT:
        msg = (f"📉 <b>Signal: SELL</b>\nSymbol: GOLD (GC=F)\nPrice: {price:.2f}\nTime: {time_str} UTC")
        print(f"[{time_str}] SELL signal @ {price:.2f}")
        send_telegram(msg)
        last_signal_time = now

# ====================== دریافت داده ======================
def get_data():
    try:
        df = yf.download(SYMBOL, period="5d", interval="1m", progress=False, auto_adjust=True)
        if df.empty or len(df) < 50:
            print("Not enough data")
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'})

        # اندیکاتورها
        df['STOCHk'], df['STOCHd'] = calc_stochastic(df['high'], df['low'], df['close'], 5, 3, 3)
        df['BBU'], df['BBM'], df['BBL'] = calc_bollinger(df['close'], BB_PERIOD, BB_DEV)
        df['RSI'] = calc_rsi(df['close'], RSI_PERIOD)
        df['ADX'], df['DMP'], df['DMN'] = calc_adx(df['high'], df['low'], df['close'], ADX_PERIOD)
        df['MA_FAST'] = calc_sma(df['close'], MA1_PERIOD)
        df['MA_SLOW'] = calc_sma(df['close'], MA2_PERIOD)

        df = df.dropna()
        return df
    except Exception as e:
        print(f"Error getting data: {e}")
        return None

# ====================== حلقه ربات ======================
def bot_loop():
    global last_bar_time
    print("=" * 50)
    print("🚀 Gold Signal Bot started")
    print(f"Symbol: {SYMBOL}")
    print("=" * 50)
    send_telegram("🤖 <b>Gold Signal Bot is ONLINE</b>\nReady to send signals...")

    while True:
        try:
            df = get_data()
            if df is not None and len(df) > 50:
                current_bar = df.index[-1]
                if last_bar_time is None or current_bar > last_bar_time:
                    last_bar_time = current_bar
                    print(f"New bar: {current_bar} | Close: {df['close'].iloc[-1]:.2f}")
                    check_signals(df)
            time.sleep(30)
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)

def start_bot():
    global bot_started
    if not bot_started:
        bot_started = True
        t = threading.Thread(target=bot_loop, daemon=True)
        t.start()
        print("Bot thread started")

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
