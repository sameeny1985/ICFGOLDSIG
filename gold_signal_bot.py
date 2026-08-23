# ============================================================
#  gold_signal_bot.py  (نسخه مخصوص Render / Cloud)
#  منطق دقیقاً همان atr3_final_v2.3
#  منبع قیمت: Yahoo Finance (yfinance) - رایگان
# ============================================================

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
import pandas_ta as ta
import os

# ====================== تنظیمات ======================
# نماد طلا روی Yahoo Finance
SYMBOL = "GC=F"          # طلای فیوچرز (COMEX) - بهترین گزینه رایگان
# SYMBOL = "XAUUSD=X"    # اگر این یکی بهتر کار کرد عوض کن

# پارامترهای استراتژی (همان v2.3)
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

# تلگرام (از Environment Variable می‌خواند - امن‌تر)
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "8669710314:AAGzGTAfGoNGnE6eELqxZVe4SWDTDBE0Qlc")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003714269439")

# ====================== GLOBAL ======================
last_signal_time = None
last_bar_time = None

# ====================== TELEGRAM ======================
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"Telegram error: {r.text}")
        else:
            print("Telegram sent successfully")
    except Exception as e:
        print(f"Telegram send failed: {e}")

# ====================== فیلترهای بولینگر ======================
def is_real_touch_lower(df: pd.DataFrame) -> bool:
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


def is_real_touch_upper(df: pd.DataFrame) -> bool:
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
def check_signals(df: pd.DataFrame):
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

    buy_ready  = False
    sell_ready = False

    # ۱. کراس استوکاستیک
    if (stoch_k_prev < stoch_d_prev and stoch_k > stoch_d and stoch_k <= STO_OVERSELL_CRS):
        buy_ready = True
    if (stoch_k_prev > stoch_d_prev and stoch_k < stoch_d and stoch_k >= STO_OVERBUY_CRS):
        sell_ready = True

    # ۲. فیلتر بولینگر
    if buy_ready and not is_real_touch_lower(df):
        buy_ready = False
    if sell_ready and not is_real_touch_upper(df):
        sell_ready = False

    # ۳. فیلتر میانگین
    if buy_ready and ma_fast < ma_slow:
        buy_ready = False
    if sell_ready and ma_fast > ma_slow:
        sell_ready = False

    # ۴. ADX + DI
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

    # ۵. RSI
    if buy_ready and rsi > RSI_OVERBUY:
        buy_ready = False
    if sell_ready and rsi < RSI_OVERSELL:
        sell_ready = False

    # -------------------- ارسال سیگنال --------------------
    now = datetime.utcnow()

    if last_signal_time is not None:
        if (now - last_signal_time).total_seconds() < COOLDOWN_MINUTES * 60:
            return

    price = df['close'].iloc[-1]
    time_str = df.index[-1].strftime("%Y-%m-%d %H:%M")

    if buy_ready and stoch_k > STO_OVERSELL_EXT:
        msg = (f"📈 <b>Signal: BUY</b>\n"
               f"Symbol: GOLD (GC=F)\n"
               f"Price: {price:.2f}\n"
               f"Time: {time_str} UTC")
        print(f"[{time_str}] BUY signal @ {price:.2f}")
        send_telegram(msg)
        last_signal_time = now

    if sell_ready and stoch_k < STO_OVERBUY_EXT:
        msg = (f"📉 <b>Signal: SELL</b>\n"
               f"Symbol: GOLD (GC=F)\n"
               f"Price: {price:.2f}\n"
               f"Time: {time_str} UTC")
        print(f"[{time_str}] SELL signal @ {price:.2f}")
        send_telegram(msg)
        last_signal_time = now


# ====================== دریافت داده ======================
def get_data():
    try:
        # داده ۱ دقیقه‌ای طلا (حداکثر ۷ روز اخیر)
        df = yf.download(SYMBOL, period="5d", interval="1m", progress=False, auto_adjust=True)

        if df.empty or len(df) < 50:
            print("Not enough data from yfinance")
            return None

        # اگر MultiIndex بود صافش کن
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume'
        })

        # محاسبه اندیکاتورها
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=5, d=3, smooth_k=3)
        df = pd.concat([df, stoch], axis=1)

        bb = ta.bbands(df['close'], length=BB_PERIOD, std=BB_DEV)
        df = pd.concat([df, bb], axis=1)

        df['RSI'] = ta.rsi(df['close'], length=RSI_PERIOD)

        adx = ta.adx(df['high'], df['low'], df['close'], length=ADX_PERIOD)
        df = pd.concat([df, adx], axis=1)

        df['MA_FAST'] = ta.sma(df['close'], length=MA1_PERIOD)
        df['MA_SLOW'] = ta.sma(df['close'], length=MA2_PERIOD)

        # تغییر نام ستون‌ها برای راحتی
        rename_map = {}
        for col in df.columns:
            if col.startswith('STOCHk'):
                rename_map[col] = 'STOCHk'
            elif col.startswith('STOCHd'):
                rename_map[col] = 'STOCHd'
            elif col.startswith('BBU'):
                rename_map[col] = 'BBU'
            elif col.startswith('BBL'):
                rename_map[col] = 'BBL'
            elif col.startswith('ADX_'):
                rename_map[col] = 'ADX'
            elif col.startswith('DMP_'):
                rename_map[col] = 'DMP'
            elif col.startswith('DMN_'):
                rename_map[col] = 'DMN'

        df = df.rename(columns=rename_map)
        df = df.dropna()

        return df

    except Exception as e:
        print(f"Error getting data: {e}")
        return None


# ====================== MAIN ======================
def main():
    global last_bar_time

    print("=" * 50)
    print("🚀 Gold Signal Bot started (Render version)")
    print(f"Symbol: {SYMBOL}")
    print(f"Telegram Chat ID: {TELEGRAM_CHAT_ID}")
    print("=" * 50)

    # تست اولیه تلگرام
    send_telegram("🤖 <b>Gold Signal Bot is ONLINE</b>\nReady to send signals...")

    while True:
        try:
            df = get_data()
            if df is not None and len(df) > 50:
                current_bar = df.index[-1]

                # فقط وقتی کندل جدید آمد چک کن
                if last_bar_time is None or current_bar > last_bar_time:
                    last_bar_time = current_bar
                    print(f"New bar: {current_bar} | Close: {df['close'].iloc[-1]:.2f}")
                    check_signals(df)

            # هر ۳۰ ثانیه یک‌بار چک کن (کافی برای M1)
            time.sleep(30)

        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
