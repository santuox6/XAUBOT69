#!/usr/bin/env python3
"""
XAUUSDT.P Gold Bot — Stylish Edition v2.0
Live perpetual data: Bybit → Binance → Yahoo fallback
Charts: TradingView dark theme · EMA 9 · EMA 50 · Custom Zones
"""

import logging, sqlite3, os, io, asyncio, threading
from datetime import datetime
from typing import Optional, Dict, Any

# ── Matplotlib non-interactive backend (before all other imports) ──────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import numpy as np
import requests
import yfinance as yf

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))   # seconds
DB_PATH        = os.getenv("DB_PATH", "alerts.db")

# ── TradingView Dark Palette ───────────────────────────────────────────────────
TV = dict(
    bg     = "#131722",
    panel  = "#1e2230",
    grid   = "#1e2230",
    up     = "#26a69a",
    down   = "#ef5350",
    vol_u  = "#1a5c53",    # dark teal  for volume bars
    vol_d  = "#7a2020",    # dark red   for volume bars
    text   = "#d1d4dc",
    ema9   = "#2962ff",    # blue
    ema50  = "#ffca28",    # yellow
    zone_g = "#00897b",
    zone_r = "#c62828",
    price  = "#ffffff",
)

# ── Bybit timeframe → interval mapping ────────────────────────────────────────
BYBIT_TF = {
    "1m": "1",  "3m": "3",   "5m": "5",
    "15m": "15","30m": "30",
    "1h": "60", "2h": "120", "4h": "240",
    "1d": "D",  "1w": "W",
}
VALID_TF = "  ".join(BYBIT_TF.keys())

logging.basicConfig(
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
    level   = logging.INFO,
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# PRICE FEED   Bybit → Binance → yfinance
# ══════════════════════════════════════════════════════════════════════════════

def get_price_data() -> Optional[Dict[str, Any]]:
    """Full XAUUSDT.P ticker data. Returns dict or None."""

    # ── 1. Bybit perpetual ─────────────────────────────────────────────────
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": "XAUUSDT"},
            timeout=7,
        )
        d = r.json()
        if d.get("retCode") == 0 and d["result"]["list"]:
            t     = d["result"]["list"][0]
            last  = float(t.get("lastPrice", 0))
            mark  = float(t.get("markPrice",  last))
            open_ = float(t.get("prevPrice24h", last))
            h24   = float(t.get("highPrice24h",  0))
            l24   = float(t.get("lowPrice24h",   0))
            chg   = last - open_
            pct   = (chg / open_ * 100) if open_ else 0
            return {
                "exchange":  "Bybit",
                "symbol":    "XAUUSDT.P",
                "last":      last,
                "mark":      mark,
                "bid":       float(t.get("bid1Price",   0)),
                "ask":       float(t.get("ask1Price",   0)),
                "high24h":   h24,
                "low24h":    l24,
                "change":    chg,
                "pct":       pct,
                "volume24h": float(t.get("volume24h",   0)),
                "funding":   float(t.get("fundingRate", 0)) * 100,
                "open24h":   open_,
            }
    except Exception as e:
        logger.warning("Bybit price: %s", e)

    # ── 2. Binance FAPI ────────────────────────────────────────────────────
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            params={"symbol": "XAUUSDT"},
            timeout=7,
        )
        d = r.json()
        if "lastPrice" in d:
            last = float(d["lastPrice"])
            return {
                "exchange":  "Binance",
                "symbol":    "XAUUSDT.P",
                "last":      last,
                "mark":      last,
                "bid":       float(d.get("bidPrice", 0)),
                "ask":       float(d.get("askPrice", 0)),
                "high24h":   float(d["highPrice"]),
                "low24h":    float(d["lowPrice"]),
                "change":    float(d["priceChange"]),
                "pct":       float(d["priceChangePercent"]),
                "volume24h": float(d["volume"]),
                "funding":   0.0,
                "open24h":   float(d["openPrice"]),
            }
    except Exception as e:
        logger.warning("Binance price: %s", e)

    # ── 3. yfinance fallback ───────────────────────────────────────────────
    for sym in ["XAUUSD=X", "GC=F"]:
        try:
            fi = yf.Ticker(sym).fast_info
            p  = fi.get("last_price") or fi.get("regularMarketPrice")
            if p and float(p) > 500:
                price = round(float(p), 2)
                return {
                    "exchange": "Yahoo", "symbol": "XAUUSD",
                    "last": price, "mark": price,
                    "bid": 0, "ask": 0,
                    "high24h": 0, "low24h": 0,
                    "change": 0, "pct": 0,
                    "volume24h": 0, "funding": 0, "open24h": 0,
                }
        except Exception:
            pass
    return None


def get_price() -> Optional[float]:
    d = get_price_data()
    return d["last"] if d else None


# ══════════════════════════════════════════════════════════════════════════════
# OHLCV FEED   Bybit kline → yfinance
# ══════════════════════════════════════════════════════════════════════════════

def get_ohlcv(tf: str = "1h") -> Optional[pd.DataFrame]:
    interval = BYBIT_TF.get(tf, "60")

    # ── Bybit kline ────────────────────────────────────────────────────────
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={
                "category": "linear",
                "symbol":   "XAUUSDT",
                "interval": interval,
                "limit":    85,
            },
            timeout=8,
        )
        d = r.json()
        if d.get("retCode") == 0 and d["result"]["list"]:
            raw = list(reversed(d["result"]["list"]))   # oldest → newest
            df  = pd.DataFrame(
                raw,
                columns=["ts","Open","High","Low","Close","Volume","Turnover"]
            )
            df["ts"] = pd.to_datetime(
                df["ts"].astype(np.int64), unit="ms", utc=True
            )
            df.set_index("ts", inplace=True)
            df.index = df.index.tz_localize(None)
            for c in ["Open","High","Low","Close","Volume"]:
                df[c] = df[c].astype(float)
            return df.tail(80)
    except Exception as e:
        logger.warning("Bybit kline: %s", e)

    # ── yfinance fallback ──────────────────────────────────────────────────
    yf_map = {
        "1m": ("1m","1d"),   "5m": ("5m","2d"),   "15m": ("15m","5d"),
        "1h": ("1h","7d"),   "4h": ("4h","30d"),   "1d": ("1d","180d"),
    }
    yf_interval, period = yf_map.get(tf, ("1h","7d"))
    for sym in ["XAUUSD=X", "GC=F"]:
        try:
            df = yf.Ticker(sym).history(period=period, interval=yf_interval)
            if not df.empty and len(df) >= 15:
                return df.tail(80).copy()
        except Exception:
            pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════

def _con():
    return sqlite3.connect(DB_PATH)

def init_db():
    with _con() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                target     REAL    NOT NULL,
                direction  TEXT    NOT NULL,
                note       TEXT    DEFAULT '',
                created_at TEXT    NOT NULL,
                active     INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS zones (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                price_low  REAL    NOT NULL,
                price_high REAL    NOT NULL,
                label      TEXT    DEFAULT '',
                color      TEXT    DEFAULT 'green',
                active     INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS live_subs (
                chat_id    INTEGER PRIMARY KEY,
                active     INTEGER DEFAULT 1
            );
        """)
        c.commit()

# Alerts
def al_add(cid, target, direction, note=""):
    with _con() as c:
        r = c.execute(
            "INSERT INTO alerts(chat_id,target,direction,note,created_at)"
            " VALUES(?,?,?,?,?)",
            (cid, target, direction, note, datetime.utcnow().isoformat(timespec="seconds"))
        )
        c.commit(); return r.lastrowid

def al_list(cid):
    with _con() as c:
        return c.execute(
            "SELECT id,target,direction,note FROM alerts"
            " WHERE chat_id=? AND active=1 ORDER BY id", (cid,)
        ).fetchall()

def al_cancel(aid, cid):
    with _con() as c:
        r = c.execute(
            "UPDATE alerts SET active=0 WHERE id=? AND chat_id=? AND active=1",
            (aid, cid)
        ); c.commit(); return r.rowcount > 0

def al_cancel_all(cid):
    with _con() as c:
        r = c.execute("UPDATE alerts SET active=0 WHERE chat_id=? AND active=1",(cid,))
        c.commit(); return r.rowcount

def al_all_active():
    with _con() as c:
        return c.execute(
            "SELECT id,chat_id,target,direction FROM alerts WHERE active=1"
        ).fetchall()

def al_off(aid):
    with _con() as c:
        c.execute("UPDATE alerts SET active=0 WHERE id=?", (aid,)); c.commit()

# Zones
def zo_add(cid, low, high, label="", color="green"):
    with _con() as c:
        r = c.execute(
            "INSERT INTO zones(chat_id,price_low,price_high,label,color) VALUES(?,?,?,?,?)",
            (cid, low, high, label, color)
        ); c.commit(); return r.lastrowid

def zo_list(cid):
    with _con() as c:
        return c.execute(
            "SELECT id,price_low,price_high,label,color FROM zones"
            " WHERE chat_id=? AND active=1 ORDER BY price_low", (cid,)
        ).fetchall()

def zo_del(zid, cid):
    with _con() as c:
        r = c.execute(
            "UPDATE zones SET active=0 WHERE id=? AND chat_id=? AND active=1",
            (zid, cid)
        ); c.commit(); return r.rowcount > 0

# Live subs
def live_toggle(cid) -> bool:
    with _con() as c:
        row = c.execute("SELECT active FROM live_subs WHERE chat_id=?", (cid,)).fetchone()
        if row is None:
            c.execute("INSERT INTO live_subs(chat_id,active) VALUES(?,1)", (cid,))
            c.commit(); return True
        new = 0 if row[0] else 1
        c.execute("UPDATE live_subs SET active=? WHERE chat_id=?", (new, cid))
        c.commit(); return bool(new)

def live_subs():
    with _con() as c:
        return [r[0] for r in c.execute(
            "SELECT chat_id FROM live_subs WHERE active=1"
        ).fetchall()]


# ══════════════════════════════════════════════════════════════════════════════
# FORMATTERS — Stylish Telegram messages
# ══════════════════════════════════════════════════════════════════════════════

def fp(p: float) -> str:
    return f"${p:,.2f}"

def fpf(p: float) -> str:
    """Full precision (3 decimals) for mark/last."""
    return f"${p:,.3f}"

def de(direction: str) -> str:
    return "📈" if direction == "above" else "📉"

def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d  %H:%M:%S  UTC")

def price_bar(cur: float, lo: float, hi: float, w: int = 12) -> str:
    """Visual price position bar in 24H range."""
    if not (lo > 0 and hi > lo):
        return ""
    pct = max(0, min(100, (cur - lo) / (hi - lo) * 100))
    f   = int(pct / 100 * w)
    bar = "█" * f + "░" * (w - f)
    return f"`[{bar}]`  `{pct:.0f}%`"


def fmt_price_msg(d: dict) -> str:
    last = d["last"];  mark = d["mark"]
    bid  = d["bid"];   ask  = d["ask"]
    h24  = d["high24h"]; l24 = d["low24h"]
    chg  = d["change"];  pct = d["pct"]
    fund = d["funding"]; exc = d["exchange"]

    up    = chg >= 0
    c_e   = "🟢" if up else "🔴"
    a_e   = "📈" if up else "📉"
    sign  = "+" if up else ""
    f_e   = "🟢" if fund < 0 else ("🔴" if fund > 0.05 else "🟡")
    f_s   = "+" if fund >= 0 else ""
    bar   = price_bar(last, l24, h24)

    lines = [
        f"{c_e} *XAUUSDT  ·  PERPETUAL*",
        "",
        f"   💎 *{fpf(last)}*",
        f"   📌 Mark    `{fp(mark)}`",
    ]
    if bid and ask:
        lines.append(f"   🔁 `Bid: {fp(bid)}  ╱  Ask: {fp(ask)}`")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"   {a_e} Change     `{sign}{fp(chg)}  ({sign}{pct:.2f}%)`",
        "",
        f"   🔺 High 24H   `{fp(h24)}`",
        f"   🔻 Low  24H   `{fp(l24)}`",
    ]
    if bar:
        lines += ["", f"   {bar}", "   _L ←──── position ────→ H_"]

    lines += [
        "",
        f"   {f_e} Funding    `{f_s}{abs(fund):.4f}%`",
        f"   📦 Volume     `{d['volume24h']:,.0f} XAU`",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🏦 _{exc}_   🕐 `{utcnow()}`",
    ]
    return "\n".join(lines)


def fmt_alert_created(aid, target, direction, current, note="") -> str:
    up    = direction == "above"
    a_e   = "📈" if up else "📉"
    diff  = abs(current - target)
    sign  = "+" if direction == "above" else "-"
    return "\n".join([
        "✅ *ALERT CREATED*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"   🆔 ID         `#{aid}`",
        f"   🎯 Target     `{fp(target)}`",
        f"   📊 Current    `{fp(current)}`",
        f"   ↔️  Gap        `{sign}{fp(diff)}`",
        f"   {a_e} Fires when price goes *{direction}* `{fp(target)}`",
        (f"   📝 _{note}_" if note else ""),
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"   ⏱ _Checked every {CHECK_INTERVAL}s_",
    ])


def fmt_alert_fired(aid, current, target, direction) -> str:
    up    = direction == "above"
    a_e   = "📈" if up else "📉"
    sign  = "+" if up else "-"
    diff  = abs(current - target)
    return "\n".join([
        "🚨 *PRICE ALERT TRIGGERED!*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"   {a_e} XAUUSDT went *{direction}* your target!",
        "",
        f"   💰 Current    `{fp(current)}`",
        f"   🎯 Target     `{fp(target)}`",
        f"   📏 Moved by   `{sign}{fp(diff)}`",
        "",
        f"   🕐 `{utcnow()}`",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"   _Alert `#{aid}` removed · /alert to set a new one_",
    ])


def fmt_live_tick(price: float, d: Optional[dict] = None) -> str:
    """Compact live stream message."""
    if d:
        up   = d["change"] >= 0
        sign = "+" if up else ""
        e    = "🟢" if up else "🔴"
        chg  = f"  {sign}{d['change']:.2f} ({sign}{d['pct']:.2f}%)"
    else:
        e, chg = "📡", ""
    return f"{e} `{fp(price)}`{chg}  🕐 `{utcnow()}`"


# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════

def kb_price() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 15m", callback_data="chart_15m"),
            InlineKeyboardButton("📊 1H",  callback_data="chart_1h"),
            InlineKeyboardButton("📊 4H",  callback_data="chart_4h"),
            InlineKeyboardButton("📊 1D",  callback_data="chart_1d"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh",   callback_data="refresh"),
            InlineKeyboardButton("🔔 Set Alert", callback_data="alert_help"),
        ],
    ])

def kb_chart(tf: str) -> InlineKeyboardMarkup:
    tfs = ["1h","4h","1d"]
    row = [InlineKeyboardButton(
        f"{'✅ ' if t == tf else ''}{t.upper()}",
        callback_data=f"chart_{t}"
    ) for t in tfs]
    return InlineKeyboardMarkup([row, [
        InlineKeyboardButton("💰 Price",     callback_data="refresh"),
        InlineKeyboardButton("🔄 Redraw",    callback_data=f"chart_{tf}"),
    ]])


# ══════════════════════════════════════════════════════════════════════════════
# CHART GENERATION
# ══════════════════════════════════════════════════════════════════════════════

_chart_lock = threading.Lock()

def build_chart(tf: str = "1h", zones_data: list = None) -> Optional[io.BytesIO]:
    """TradingView-style dark candlestick chart. Thread-safe."""
    with _chart_lock:
        df = get_ohlcv(tf)
        if df is None or df.empty:
            return None

        df["EMA9"]  = df["Close"].ewm(span=9,  adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        has_vol     = "Volume" in df.columns and df["Volume"].sum() > 0

        mc = mpf.make_marketcolors(
            up   = TV["up"],   down  = TV["down"],
            edge = {"up": TV["up"], "down": TV["down"]},
            wick = {"up": TV["up"], "down": TV["down"]},
            volume = {"up": TV["vol_u"], "down": TV["vol_d"]},
        )
        style = mpf.make_mpf_style(
            base_mpl_style = "dark_background",
            marketcolors   = mc,
            figcolor = TV["bg"],  facecolor = TV["bg"],
            gridcolor = TV["grid"], gridstyle = "-", gridaxis = "both",
            rc = {
                "axes.labelcolor": TV["text"],
                "axes.edgecolor":  TV["panel"],
                "xtick.color":     TV["text"],
                "ytick.color":     TV["text"],
                "text.color":      TV["text"],
                "font.size":       9,
            },
        )

        current  = df["Close"].iloc[-1]
        prev     = df["Open"].iloc[0]
        chg      = current - prev
        pct      = (chg / prev * 100) if prev else 0
        is_up    = chg >= 0
        arrow    = "▲" if is_up else "▼"
        clr      = TV["up"] if is_up else TV["down"]
        sign     = "+" if is_up else ""

        ap = [
            mpf.make_addplot(df["EMA9"],  color=TV["ema9"],  width=1.5),
            mpf.make_addplot(df["EMA50"], color=TV["ema50"], width=1.5),
        ]

        h = 8 if has_vol else 7
        fig, axes = mpf.plot(
            df, type="candle", style=style, addplot=ap,
            volume=has_vol, figsize=(14, h), returnfig=True,
            title=(
                f"\nXAUUSDT.P   {tf.upper()}   "
                f"${current:,.2f}   "
                f"{arrow} {sign}{chg:.2f} ({sign}{pct:.2f}%)"
            ),
        )
        ax = axes[0]

        # ── Current price line ─────────────────────────────────────────────
        ax.axhline(y=current, color=TV["price"], linestyle="--",
                   linewidth=0.9, alpha=0.9, zorder=6)
        ax.annotate(
            f" {current:,.2f}",
            xy=(1.0, current), xycoords=("axes fraction", "data"),
            fontsize=9, color="white", va="center", zorder=10,
            bbox=dict(boxstyle="round,pad=0.3", facecolor=clr,
                      edgecolor="none", alpha=0.9),
        )

        # ── User zones (support / resistance rectangles) ───────────────────
        if zones_data:
            for z_low, z_high, z_label, z_color in zones_data:
                hc = TV["zone_g"] if z_color == "green" else TV["zone_r"]
                ax.axhspan(z_low, z_high, alpha=0.15, color=hc, zorder=2)
                ax.axhline(y=z_low,  color=hc, linestyle="--",
                           linewidth=0.8, alpha=0.6, zorder=3)
                ax.axhline(y=z_high, color=hc, linestyle="--",
                           linewidth=0.8, alpha=0.6, zorder=3)
                if z_label:
                    ax.annotate(
                        f"  {z_label}",
                        xy=(0.01, (z_low + z_high) / 2),
                        xycoords=("axes fraction", "data"),
                        fontsize=8, color=hc, va="center", alpha=0.9,
                    )

        # ── Stats box ─────────────────────────────────────────────────────
        ema9v  = df["EMA9"].iloc[-1]
        ema50v = df["EMA50"].iloc[-1]
        ax.text(
            0.01, 0.98,
            f"EMA 9: {ema9v:,.2f}   EMA 50: {ema50v:,.2f}",
            transform=ax.transAxes, fontsize=8, color=TV["text"],
            va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=TV["bg"],
                      edgecolor=TV["panel"], alpha=0.85),
        )

        # ── EMA legend ────────────────────────────────────────────────────
        ax.plot([], [], color=TV["ema9"],  linewidth=1.5, label="EMA 9")
        ax.plot([], [], color=TV["ema50"], linewidth=1.5, label="EMA 50")
        ax.legend(
            loc="upper right", fontsize=8, framealpha=0.3,
            facecolor=TV["bg"], edgecolor=TV["panel"], labelcolor=TV["text"],
        )

        # ── Watermark ─────────────────────────────────────────────────────
        fig.text(0.99, 0.01, "XAUUSDT.P Bot", ha="right",
                 fontsize=7, color="#555", alpha=0.45)

        buf = io.BytesIO()
        fig.savefig(buf, dpi=150, bbox_inches="tight", facecolor=TV["bg"])
        plt.close(fig)
        buf.seek(0)
        return buf


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🏅  *XAUUSDT PERPETUAL BOT*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Real-time gold perpetual data from\n"
        "Bybit  ·  Binance  ·  TradingView charts\n\n"
        "📊 /price — Live price + 24H stats\n"
        "🕯 /chart — Candlestick chart\n"
        "🔔 /alert — Price alert\n"
        "🟢 /zone  — Support/resistance zone\n"
        "📡 /live  — Toggle live stream\n"
        "❓ /help  — Full command list\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "_Type /price to begin_ 🚀"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── /price ───────────────────────────────────────────────────────────────────
async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ _Fetching live price…_", parse_mode="Markdown")
    data = get_price_data()
    if data:
        await msg.edit_text(
            fmt_price_msg(data),
            parse_mode  = "Markdown",
            reply_markup= kb_price(),
        )
    else:
        await msg.edit_text(
            "❌ *Price unavailable*\n\n"
            "All sources failed. Market may be closed (weekend).\n"
            "_Try again in a moment._",
            parse_mode="Markdown",
        )


# ─── /chart ───────────────────────────────────────────────────────────────────
async def cmd_chart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tf = (ctx.args[0].lower() if ctx.args else "1h")
    if tf not in BYBIT_TF:
        await update.message.reply_text(
            f"❌ Unknown timeframe.\n\nAvailable:\n`{VALID_TF}`",
            parse_mode="Markdown",
        )
        return

    msg  = await update.message.reply_text(
        f"⏳ _Rendering {tf.upper()} chart…_", parse_mode="Markdown"
    )
    cid   = update.effective_chat.id
    zones = [(r[1],r[2],r[3],r[4]) for r in zo_list(cid)]
    loop  = asyncio.get_event_loop()
    buf   = await loop.run_in_executor(None, build_chart, tf, zones)

    if buf is None:
        await msg.edit_text("❌ Chart data unavailable. Try again shortly.")
        return

    price   = get_price()
    caption = (
        f"🕯 *XAUUSDT.P  ·  {tf.upper()}*"
        + (f"\n💎 `{fp(price)}`" if price else "")
        + (f"\n🟢 {len(zones)} zone(s) active" if zones else "")
        + f"\n🕐 `{utcnow()}`"
    )
    await ctx.bot.send_photo(
        chat_id     = cid,
        photo       = buf,
        caption     = caption,
        parse_mode  = "Markdown",
        reply_markup= kb_chart(tf),
    )
    await msg.delete()


# ─── /alert ───────────────────────────────────────────────────────────────────
async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usage = (
        "*Usage:* `/alert <price>` `[note]`\n\n"
        "*Examples:*\n"
        "• `/alert 4100`\n"
        "• `/alert 3950 buy zone`\n"
        "• `/alert 4200 resistance top`"
    )
    if not ctx.args:
        await update.message.reply_text(usage, parse_mode="Markdown")
        return

    try:
        target = round(float(ctx.args[0].replace(",","").replace("$","")), 2)
    except ValueError:
        await update.message.reply_text(f"❌ Invalid price.\n\n{usage}", parse_mode="Markdown")
        return

    if not (100 < target < 100_000):
        await update.message.reply_text("❌ Price must be between $100 and $100,000.")
        return

    note    = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""
    current = get_price()
    if current is None:
        await update.message.reply_text("❌ Can't fetch current price. Try again.")
        return

    direction = "above" if target > current else "below"
    cid = update.effective_chat.id
    aid = al_add(cid, target, direction, note)

    await update.message.reply_text(
        fmt_alert_created(aid, target, direction, current, note),
        parse_mode="Markdown",
    )


# ─── /alerts ──────────────────────────────────────────────────────────────────
async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    rows = al_list(cid)
    cur  = get_price()

    if not rows:
        await update.message.reply_text(
            "📭 *No active alerts*\n\n_Use /alert <price> to create one_",
            parse_mode="Markdown",
        )
        return

    lines = ["📋 *Active Alerts*"]
    if cur:
        lines.append(f"💎 Current: `{fp(cur)}`\n")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    for aid, target, direction, note in rows:
        emoji = de(direction)
        gap   = f"  ↔️ `{fp(abs(cur-target))}`" if cur else ""
        note_ = f"\n       📝 _{note}_" if note else ""
        lines.append(f"   {emoji} `#{aid}` → `{fp(target)}` ({direction}){gap}{note_}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"_Total: {len(rows)} · /cancel <id> · /cancelall_")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── /cancel ──────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: `/cancel <id>`", parse_mode="Markdown")
        return
    try:
        aid = int(str(ctx.args[0]).lstrip("#"))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Check /alerts")
        return
    cid = update.effective_chat.id
    if al_cancel(aid, cid):
        await update.message.reply_text(f"✅ Alert `#{aid}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Alert `#{aid}` not found.")


# ─── /cancelall ───────────────────────────────────────────────────────────────
async def cmd_cancelall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    n = al_cancel_all(update.effective_chat.id)
    await update.message.reply_text(
        f"🗑 Removed *{n}* alert(s)." if n else "📭 No active alerts.",
        parse_mode="Markdown",
    )


# ─── /zone ────────────────────────────────────────────────────────────────────
async def cmd_zone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    usage = (
        "*Usage:* `/zone <low> <high> [label] [green|red]`\n\n"
        "*Examples:*\n"
        "• `/zone 3950 4000 support`\n"
        "• `/zone 4100 4150 resistance red`"
    )
    if len(ctx.args) < 2:
        await update.message.reply_text(usage, parse_mode="Markdown")
        return
    try:
        low  = float(ctx.args[0].replace(",","").replace("$",""))
        high = float(ctx.args[1].replace(",","").replace("$",""))
    except ValueError:
        await update.message.reply_text(f"❌ Invalid prices.\n\n{usage}", parse_mode="Markdown")
        return

    if low >= high:
        low, high = high, low
    rest  = list(ctx.args[2:])
    color = "green"
    if rest and rest[-1].lower() in ("green","red"):
        color = rest.pop().lower()
    label = " ".join(rest)
    cid   = update.effective_chat.id
    zid   = zo_add(cid, low, high, label, color)
    e     = "🟢" if color == "green" else "🔴"

    await update.message.reply_text(
        f"{e} *Zone Added — `#{zid}`*\n\n"
        f"   📉 Low:   `{fp(low)}`\n"
        f"   📈 High:  `{fp(high)}`\n"
        + (f"   📝 Label: _{label}_\n" if label else "")
        + f"\n_Will draw on next /chart_",
        parse_mode="Markdown",
    )


# ─── /zones ───────────────────────────────────────────────────────────────────
async def cmd_zones(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    rows = zo_list(cid)
    if not rows:
        await update.message.reply_text(
            "📭 *No zones saved*\n\n_/zone <low> <high> [label] to add one_",
            parse_mode="Markdown",
        )
        return
    lines = ["🗺 *Chart Zones*\n","━━━━━━━━━━━━━━━━━━━━━━━━"]
    for zid, low, high, label, color in rows:
        e = "🟢" if color == "green" else "🔴"
        l = f"  _{label}_" if label else ""
        lines.append(f"   {e} `#{zid}` `{fp(low)} — {fp(high)}`{l}")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━","_/delzone <id> to remove_"]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── /delzone ─────────────────────────────────────────────────────────────────
async def cmd_delzone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: `/delzone <id>`", parse_mode="Markdown")
        return
    try:
        zid = int(str(ctx.args[0]).lstrip("#"))
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
        return
    cid = update.effective_chat.id
    if zo_del(zid, cid):
        await update.message.reply_text(f"✅ Zone `#{zid}` removed.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Zone `#{zid}` not found. Check /zones")


# ─── /live ────────────────────────────────────────────────────────────────────
async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid   = update.effective_chat.id
    is_on = live_toggle(cid)
    if is_on:
        await update.message.reply_text(
            "📡 *Live Stream  ON*\n\n"
            f"You'll receive price ticks every *{CHECK_INTERVAL}s*.\n"
            "_Send /live again to stop._",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text("📡 *Live Stream  OFF*", parse_mode="Markdown")


# ─── /help ────────────────────────────────────────────────────────────────────
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ *Full Command Reference*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "*📊 Price & Charts*\n"
        "  /price — Live price + bid/ask + funding\n"
        "  /chart — 1H candlestick (default)\n"
        "  /chart `15m` `1h` `4h` `1d` etc.\n\n"
        "*🔔 Alerts*\n"
        "  /alert `4100` — Alert at $4,100\n"
        "  /alert `3950 buy zone` — With note\n"
        "  /alerts — List active alerts\n"
        "  /cancel `3` — Remove alert #3\n"
        "  /cancelall — Remove all\n\n"
        "*🟢 Chart Zones*\n"
        "  /zone `3950 4000 support` — Green zone\n"
        "  /zone `4100 4150 resistance red`\n"
        "  /zones — List zones\n"
        "  /delzone `2` — Remove zone #2\n\n"
        "*📡 Live Stream*\n"
        "  /live — Toggle price updates\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "*Data source:* Bybit → Binance → Yahoo\n"
        "_Bybit/Binance = real-time · Yahoo = ~15min delay_",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK HANDLER  (inline keyboard buttons)
# ══════════════════════════════════════════════════════════════════════════════

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q  = update.callback_query
    cd = q.data
    cid = q.message.chat_id

    # ── Chart buttons ──────────────────────────────────────────────────────
    if cd.startswith("chart_"):
        tf = cd.split("_")[1]
        await q.answer(f"Rendering {tf.upper()} chart…")
        zones = [(r[1],r[2],r[3],r[4]) for r in zo_list(cid)]
        loop  = asyncio.get_event_loop()
        buf   = await loop.run_in_executor(None, build_chart, tf, zones)
        if buf:
            price   = get_price()
            caption = (
                f"🕯 *XAUUSDT.P  ·  {tf.upper()}*"
                + (f"\n💎 `{fp(price)}`" if price else "")
                + f"\n🕐 `{utcnow()}`"
            )
            await ctx.bot.send_photo(
                chat_id     = cid,
                photo       = buf,
                caption     = caption,
                parse_mode  = "Markdown",
                reply_markup= kb_chart(tf),
            )
        else:
            await q.answer("❌ Chart data unavailable", show_alert=True)
        return

    # ── Refresh price ──────────────────────────────────────────────────────
    if cd == "refresh":
        await q.answer("Refreshing…")
        data = get_price_data()
        if data:
            try:
                await q.message.edit_text(
                    fmt_price_msg(data),
                    parse_mode  = "Markdown",
                    reply_markup= kb_price(),
                )
            except Exception:
                pass   # message unchanged → Telegram raises error; ignore
        else:
            await q.answer("❌ Price unavailable", show_alert=True)
        return

    # ── Alert help ─────────────────────────────────────────────────────────
    if cd == "alert_help":
        price = get_price()
        hint  = f"`/alert {int(price)+50}`" if price else "`/alert 4150`"
        await q.answer()
        await ctx.bot.send_message(
            chat_id = cid,
            text    = (
                "🔔 *Set a Price Alert*\n\n"
                f"Type: {hint}\n\n"
                "Or: `/alert 3950 support zone`\n\n"
                "_The bot will notify you the moment price hits your target._"
            ),
            parse_mode="Markdown",
        )
        return

    await q.answer()


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND JOBS
# ══════════════════════════════════════════════════════════════════════════════

async def job_alerts(ctx: ContextTypes.DEFAULT_TYPE):
    """Check all active alerts against live price."""
    current = get_price()
    if current is None:
        return

    rows = al_all_active()
    if not rows:
        return
    logger.info("Alert check: XAUUSDT=%s  active=%d", fp(current), len(rows))

    for aid, cid, target, direction in rows:
        hit = (
            (direction == "above" and current >= target) or
            (direction == "below" and current <= target)
        )
        if not hit:
            continue
        try:
            await ctx.bot.send_message(
                chat_id    = cid,
                text       = fmt_alert_fired(aid, current, target, direction),
                parse_mode = "Markdown",
            )
            al_off(aid)
            logger.info("🔔 Alert #%d fired → chat %d @ %s", aid, cid, fp(current))
        except Exception as e:
            logger.error("Notify failed (chat %d, alert #%d): %s", cid, aid, e)


async def job_live_stream(ctx: ContextTypes.DEFAULT_TYPE):
    """Send live price tick to subscribed users."""
    subs = live_subs()
    if not subs:
        return

    data    = get_price_data()
    current = data["last"] if data else get_price()
    if current is None:
        return

    msg = fmt_live_tick(current, data)
    for cid in subs:
        try:
            await ctx.bot.send_message(
                chat_id    = cid,
                text       = msg,
                parse_mode = "Markdown",
            )
        except Exception as e:
            logger.warning("Live stream (chat %d): %s", cid, e)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("\n❌  BOT_TOKEN not set!\n")
        print("   1. Open Telegram → search @BotFather")
        print("   2. Send /newbot and follow prompts")
        print("   3. Copy the token, then run:\n")
        print("   export BOT_TOKEN='1234567890:ABCdef...'")
        print("   python bot.py\n")
        return

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    for name, fn in [
        ("start",     cmd_start),
        ("price",     cmd_price),
        ("chart",     cmd_chart),
        ("alert",     cmd_alert),
        ("alerts",    cmd_alerts),
        ("cancel",    cmd_cancel),
        ("cancelall", cmd_cancelall),
        ("zone",      cmd_zone),
        ("zones",     cmd_zones),
        ("delzone",   cmd_delzone),
        ("live",      cmd_live),
        ("help",      cmd_help),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_callback))

    app.job_queue.run_repeating(job_alerts,      interval=CHECK_INTERVAL, first=10, name="alerts")
    app.job_queue.run_repeating(job_live_stream, interval=CHECK_INTERVAL, first=20, name="live")

    logger.info("🤖  XAUUSDT.P Bot started  |  interval=%ds  |  db=%s",
                CHECK_INTERVAL, DB_PATH)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
