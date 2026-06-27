# 🏅 XAUUSDT.P Perpetual Gold Bot — Stylish Edition

Real-time **XAUUSDT Perpetual** data from Bybit/Binance with TradingView-style charts.

---

## ✨ What's New in v2.0

| Feature | Details |
|---|---|
| 📡 Live Data | Bybit XAUUSDT.P perpetual → Binance → Yahoo fallback |
| 💎 Rich Price | Mark price, Bid/Ask spread, 24H stats, Funding rate |
| 📊 Position Bar | Visual `[████░░░]` showing where price sits in 24H range |
| 🖱 Tap Buttons | Click buttons on price/chart messages for quick navigation |
| 🕯 Better Charts | Volume bars, stats overlay, zone labels, live price badge |
| 🔔 Styled Alerts | Clean alert messages with gap distance shown |

---

## 🚀 Setup (5 minutes)

### Step 1 — Get Bot Token
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow prompts → copy the token

### Step 2 — Install
```bash
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 3 — Run
```bash
export BOT_TOKEN="your_token_here"
python bot.py
```

---

## 📋 Commands

| Command | Example | Description |
|---|---|---|
| `/price` | `/price` | Live price + mark + bid/ask + 24H stats + funding |
| `/chart` | `/chart 1h` | TradingView-style candlestick chart |
| `/alert` | `/alert 4100 resistance` | Alert when XAUUSDT.P hits $4,100 |
| `/alerts` | | List all active alerts |
| `/cancel` | `/cancel 3` | Remove alert #3 |
| `/cancelall` | | Remove all alerts |
| `/zone` | `/zone 3950 4000 support` | Add green support zone to chart |
| `/zone` | `/zone 4100 4150 resistance red` | Add red resistance zone |
| `/zones` | | List all saved zones |
| `/delzone` | `/delzone 2` | Remove zone #2 |
| `/live` | | Toggle live price stream every 60s |

**Timeframes for /chart:** `1m 3m 5m 15m 30m 1h 2h 4h 1d 1w`

---

## 📊 What the /price command shows

```
🟢 XAUUSDT · PERPETUAL

   💎 $4,088.385         ← Last price (3 decimals)
   📌 Mark    $4,088.12  ← Mark price
   🔁 Bid: $4,086.61  ╱  Ask: $4,090.16

━━━━━━━━━━━━━━━━━━━━━━━━
   📈 Change     +$52.89  (+1.31%)

   🔺 High 24H   $4,102.50
   🔻 Low  24H   $3,985.20
   [████████████░░]  88%     ← Position in 24H range
   L ←── position ──→ H

   🟡 Funding    +0.0100%
   📦 Volume     12,346 XAU
━━━━━━━━━━━━━━━━━━━━━━━━
🏦 Bybit   🕐 2026-06-27  13:07:12  UTC
```

**Tap buttons:**  `📊 15m  📊 1H  📊 4H  📊 1D  🔄 Refresh  🔔 Set Alert`

---

## 📡 Data Sources

| Source | Symbol | Quality |
|---|---|---|
| **Bybit** (primary) | XAUUSDT linear perpetual | ✅ Real-time bid/ask, funding |
| **Binance** (fallback) | XAUUSDT perpetual futures | ✅ Real-time |
| **Yahoo Finance** (fallback) | XAUUSD=X / GC=F | ⚠️ ~15 min delay |

---

## 🌐 Deploy 24/7 (Free)

### Railway.app (Easiest)
1. Push code to GitHub
2. [railway.app](https://railway.app) → New Project → GitHub repo
3. Set `BOT_TOKEN` env variable → Deploy

### VPS
```bash
screen -S goldbot
export BOT_TOKEN="your_token"
python bot.py
# Ctrl+A, D to detach
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY bot.py .
CMD ["python", "bot.py"]
```
```bash
docker build -t xauusdt-bot .
docker run -d -e BOT_TOKEN="your_token" --name goldbot xauusdt-bot
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | required | From @BotFather |
| `CHECK_INTERVAL` | `60` | Alert check interval (seconds) |
| `DB_PATH` | `alerts.db` | SQLite database path |

---
