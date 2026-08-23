# Gold Signal Bot (Render Version)

ربات سیگنال طلا بر اساس استراتژی atr3 v2.3

## نماد طلا
- روی Yahoo Finance: `GC=F` (COMEX Gold Futures)
- جایگزین: `XAUUSD=X`

## استقرار روی Render

1. این ریپو را روی GitHub آپلود کن
2. در Render یک **Background Worker** بساز
3. تنظیمات:
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python gold_signal_bot.py`
4. Environment Variables (اختیاری ولی پیشنهادی):
   - `TELEGRAM_TOKEN` = توکن ربات تلگرام
   - `TELEGRAM_CHAT_ID` = آیدی کانال یا گروه

## نکته مهم
سرویس رایگان Render بعد از مدتی می‌خوابد.  
برای همیشه روشن ماندن یکی از این کارها را انجام بده:
- از پلن Starter استفاده کن
- یا با UptimeRobot هر ۵ دقیقه به یک Web Service جداگانه پینگ بزن
