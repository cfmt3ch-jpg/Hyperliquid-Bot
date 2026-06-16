"""
Konfigurasi untuk Hyperliquid Trading Bot
==========================================
Master Caesar: Edit file config.json dengan data wallet Anda!
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    """Memuat konfigurasi dari config.json"""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"❌ File config.json tidak ditemukan!\n"
            f"   Salin config.json.example menjadi config.json,\n"
            f"   lalu isi account_address dan secret_key Anda."
        )
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ── Pengaturan Trading ──────────────────────────────────────────
# Edit nilai-nilai ini sesuai strategi Anda

TRADING_SETTINGS = {
    # Koin yang akan ditradingkan (bisa ditambah)
    "coins": ["ETH", "BTC", "SOL"],

    # Ukuran posisi default (dalam unit koin)
    "default_size": {
        "ETH": 0.01,
        "BTC": 0.001,
        "SOL": 0.5,
    },

    # Leverage default (1x-50x, hati-hati!)
    "default_leverage": 5,

    # Stop Loss (% kerugian maksimal dari harga masuk)
    "stop_loss_pct": 2.0,

    # Take Profit (% keuntungan dari harga masuk)
    "take_profit_pct": 4.0,

    # Gunakan testnet? (True = testnet, False = mainnet dengan uang asli!)
    "use_testnet": True,
}

# ── Pengaturan AI Trader (DeepSeek) ─────────────────────────────
# Otak AI yang memutuskan trade secara dinamis (bukan strategi tetap).
# Guardrail risiko di bawah ini WAJIB dan tidak boleh dilewati AI.

AI_SETTINGS = {
    # Provider LLM (OpenAI-compatible)
    "provider": "deepseek",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",

    # ── GUARDRAIL RISIKO (aturan keras) ──
    # Leverage maksimal yang diizinkan (AI tidak boleh melebihi ini)
    "max_leverage": 5,
    # Margin maksimal per trade sebagai % dari nilai akun.
    # Dinaikkan dari 1% → 5% agar notional (5% × 5x = 25% modal) memenuhi
    # minimum order Hyperliquid dan risiko/trade tetap modest (~0.5% di SL 2%).
    "max_position_pct": 5.0,
    # Batas nilai stop loss (%). SL dari AI di-clamp ke rentang ini agar
    # tidak terlalu rapat (kena noise) atau terlalu lebar (loss besar).
    "min_stop_loss_pct": 0.5,
    "max_stop_loss_pct": 10.0,
    # Rasio risk/reward minimal: take_profit_pct >= stop_loss_pct × nilai ini.
    "min_risk_reward": 1.0,
    # Maksimal kerugian harian sebagai % dari ekuitas awal hari itu.
    # Jika tercapai, bot BERHENTI trading sampai hari berganti.
    "max_daily_loss_pct": 5.0,
    # Confidence minimal dari AI agar trade dieksekusi (0.0 - 1.0)
    "min_confidence": 0.6,
    # Maksimal jumlah posisi terbuka bersamaan
    "max_open_positions": 3,
    # Wajibkan stop loss pada setiap posisi
    "require_stop_loss": True,

    # Koin yang dipantau AI
    "coins": ["BTC", "ETH", "SOL"],
    # Interval antar siklus keputusan (detik)
    "loop_interval_seconds": 300,
    # Interval cek TP/SL (detik) — JAUH lebih cepat dari siklus AI agar
    # stop loss tidak ketinggalan saat harga bergerak cepat.
    "tp_sl_check_seconds": 3,
    # Timeframe utama & timeframe lebih tinggi (multi-timeframe)
    "candle_interval": "15m",
    "candle_interval_htf": "1h",
    # Jumlah candle yang dipakai untuk RINGKASAN ke AI
    "candle_lookback": 30,
    # Jumlah candle yang diambil untuk PERHITUNGAN indikator (MACD butuh banyak)
    "indicator_lookback": 120,
}


def get_deepseek_api_key():
    """Ambil DeepSeek API key dari config.json atau environment variable."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    try:
        cfg = load_config()
        return cfg.get("deepseek_api_key", "")
    except FileNotFoundError:
        return ""

# ── Peringatan Keamanan ─────────────────────────────────────────
SECURITY_NOTES = """
⚠️  PERINGATAN PENTING:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. JANGAN pernah share file config.json ke siapapun!
2. Gunakan TESTNET dulu sebelum mainnet.
3. Mulai dengan ukuran posisi KECIL.
4. Trading crypto sangat berisiko - Anda bisa kehilangan modal!
5. Pastikan Anda mengerti strategi sebelum menjalankan bot.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
