"""
AI Context Builder
==================
Mengumpulkan dan memformat data pasar + status akun menjadi konteks
yang siap dikirim ke AI (LLM) untuk pengambilan keputusan trading.

Indikator yang disediakan ke AI:
- Price action (open/high/low/close) + perubahan %
- SMA (5 & 20) + tren
- RSI (14)             → momentum / overbought-oversold
- MACD (12,26,9)       → momentum & persilangan tren
- ATR (14) + ATR%      → volatilitas (untuk konteks SL/TP)
- Volume rata-rata
- Multi-timeframe       → 15m (utama) + 1h (HTF)
- Fibonacci retracement → level support/resistance dari swing high/low
- Funding rate          → biaya posisi perpetual

Bekerja dengan PaperTradingEngine (simulasi) maupun HyperliquidClient (asli).
"""

from datetime import datetime
from statistics import mean
from typing import Optional

from config import AI_SETTINGS

_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


# ═══════════════════════════════════════════════════════════════
# FUNGSI MATEMATIKA INDIKATOR
# ═══════════════════════════════════════════════════════════════

def _sma(values: list, n: int) -> Optional[float]:
    if len(values) < n:
        return mean(values) if values else None
    return mean(values[-n:])


def _ema_series(values: list, n: int) -> list:
    """Deret EMA, di-seed dengan nilai pertama lalu iterasi."""
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = mean(gains[-period:])
    avg_loss = mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = _ema_series(macd_line, signal)
    macd_val = macd_line[-1]
    signal_val = signal_line[-1]
    hist = macd_val - signal_val
    return {
        "macd": round(macd_val, 4),
        "signal": round(signal_val, 4),
        "histogram": round(hist, 4),
        "state": "bullish" if macd_val > signal_val else "bearish",
    }


def _atr(highs: list, lows: list, closes: list, period: int = 14) -> Optional[dict]:
    if len(closes) < 2:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    atr = mean(trs[-period:]) if len(trs) >= period else mean(trs)
    last = closes[-1]
    return {
        "atr": round(atr, 4),
        "atr_pct": round((atr / last * 100), 2) if last else 0.0,
    }


def _fibonacci(highs: list, lows: list, current_price: float) -> Optional[dict]:
    """Level retracement Fibonacci dari swing high/low pada jendela candle."""
    if not highs or not lows:
        return None
    swing_high = max(highs)
    swing_low = min(lows)
    diff = swing_high - swing_low
    if diff <= 0:
        return None
    ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    # Retracement dari atas ke bawah (asumsi tren naik yang terkoreksi)
    levels = {str(r): round(swing_high - diff * r, 4) for r in ratios}
    # Level terdekat dengan harga saat ini
    nearest = min(levels.items(), key=lambda kv: abs(current_price - kv[1]))
    # Posisi harga dalam rentang (0 = di low, 100 = di high)
    pos_pct = round((current_price - swing_low) / diff * 100, 1)
    return {
        "swing_high": round(swing_high, 4),
        "swing_low": round(swing_low, 4),
        "levels": levels,
        "nearest_level": nearest[0],
        "nearest_price": nearest[1],
        "price_position_pct": pos_pct,
    }


# ═══════════════════════════════════════════════════════════════
# PENGAMBILAN & RINGKASAN CANDLE
# ═══════════════════════════════════════════════════════════════

def _fetch_candles(info, coin: str, interval: str, count: int) -> list:
    """Ambil `count` candle terakhir dari Hyperliquid. Degrade gracefully."""
    try:
        now_ms = int(datetime.now().timestamp() * 1000)
        interval_ms = _INTERVAL_MS.get(interval, 900_000)
        start_ms = now_ms - interval_ms * (count + 5)
        candles = info.candles_snapshot(coin, interval, start_ms, now_ms)
        return candles[-count:] if candles else []
    except Exception:
        return []


def _summarize_candles(candles: list, interval: str, summary_lookback: int) -> dict:
    """Hitung indikator dari deret candle untuk satu timeframe."""
    if not candles:
        return {}
    closes = [float(c["c"]) for c in candles]
    highs = [float(c["h"]) for c in candles]
    lows = [float(c["l"]) for c in candles]
    vols = [float(c.get("v", 0)) for c in candles]

    # Ringkasan price action pada jendela tampilan
    window = closes[-summary_lookback:] if len(closes) >= summary_lookback else closes
    first, last = window[0], window[-1]
    change_pct = ((last - first) / first * 100) if first else 0.0

    sma_short = _sma(closes, 5)
    sma_long = _sma(closes, 20)
    rsi = _rsi(closes, 14)
    macd = _macd(closes)
    atr = _atr(highs, lows, closes, 14)

    summary = {
        "interval": interval,
        "close": round(last, 4),
        "high": round(max(highs[-summary_lookback:]), 4),
        "low": round(min(lows[-summary_lookback:]), 4),
        "change_pct": round(change_pct, 2),
        "sma_short": round(sma_short, 4) if sma_short else None,
        "sma_long": round(sma_long, 4) if sma_long else None,
        "trend": ("up" if (sma_short and sma_long and sma_short > sma_long) else "down"),
        "avg_volume": round(mean(vols), 2) if vols else 0,
        "num_candles": len(candles),
    }
    if rsi is not None:
        summary["rsi"] = round(rsi, 2)
        summary["rsi_state"] = (
            "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"
        )
    if macd is not None:
        summary["macd"] = macd
    if atr is not None:
        summary["atr"] = atr["atr"]
        summary["atr_pct"] = atr["atr_pct"]
    return summary


def _fetch_funding(info, coins: list) -> dict:
    """Ambil funding rate (per jam) untuk koin yang dipantau. Degrade gracefully."""
    try:
        meta, ctxs = info.meta_and_asset_ctxs()
        universe = meta.get("universe", [])
        out = {}
        for i, asset in enumerate(universe):
            name = asset.get("name")
            if name in coins and i < len(ctxs):
                funding = ctxs[i].get("funding")
                if funding is not None:
                    hourly_pct = float(funding) * 100
                    out[name] = {
                        "hourly_pct": round(hourly_pct, 5),
                        "annualized_pct": round(hourly_pct * 24 * 365, 2),
                    }
        return out
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
# BANGUN KONTEKS LENGKAP
# ═══════════════════════════════════════════════════════════════

def build_context(engine, daily_pnl_pct: float = 0.0) -> dict:
    """
    Bangun konteks lengkap untuk AI (pasar multi-timeframe + indikator + akun).

    Args:
        engine: PaperTradingEngine atau HyperliquidClient
        daily_pnl_pct: PnL hari ini (%) untuk kesadaran risiko AI
    """
    coins = AI_SETTINGS["coins"]
    tf_main = AI_SETTINGS["candle_interval"]
    tf_htf = AI_SETTINGS["candle_interval_htf"]
    summary_lookback = AI_SETTINGS["candle_lookback"]
    indicator_lookback = AI_SETTINGS["indicator_lookback"]

    info = getattr(engine, "info", None)
    funding = _fetch_funding(info, coins) if info is not None else {}

    # ── Data Pasar ──
    market = {}
    for coin in coins:
        try:
            price = engine.get_price(coin)
        except Exception:
            continue
        entry = {"price": round(price, 4)}

        if info is not None:
            # Multi-timeframe
            timeframes = {}
            for tf in (tf_main, tf_htf):
                candles = _fetch_candles(info, coin, tf, indicator_lookback)
                summary = _summarize_candles(candles, tf, summary_lookback)
                if summary:
                    timeframes[tf] = summary
            if timeframes:
                entry["timeframes"] = timeframes

            # Fibonacci (dari timeframe utama)
            main_candles = _fetch_candles(info, coin, tf_main, indicator_lookback)
            if main_candles:
                highs = [float(c["h"]) for c in main_candles]
                lows = [float(c["l"]) for c in main_candles]
                fib = _fibonacci(highs, lows, price)
                if fib:
                    entry["fibonacci"] = fib

            # Funding rate
            if coin in funding:
                entry["funding_rate"] = funding[coin]

        market[coin] = entry

    # ── Status Akun ──
    account_value = engine.get_account_value()
    balance = getattr(engine, "balance", account_value)

    if hasattr(engine, "get_positions_summary"):
        positions = engine.get_positions_summary()
    else:
        positions = engine.get_positions()

    account = {
        "account_value": round(account_value, 4),
        "available_balance": round(balance, 4),
        "open_positions": positions,
        "num_open_positions": len(positions),
        "daily_pnl_pct": round(daily_pnl_pct, 2),
    }

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "market": market,
        "account": account,
        "watched_coins": coins,
    }
