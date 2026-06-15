"""
Decision Logger
==============
Menyimpan setiap keputusan AI secara persisten ke file JSONL
(satu objek JSON per baris) di logs/ai_decisions.jsonl.

Yang dicatat per siklus:
- waktu, PnL harian
- ringkasan konteks pasar yang DILIHAT AI (harga + indikator kunci per koin)
- keputusan AI (action, coin, confidence, reasoning, tp/sl, leverage)
- verdict Risk Manager (approved + alasan)
- hasil eksekusi (executed)

Berguna untuk evaluasi performa (evaluate.py) dan memperbaiki prompt AI.
"""

import json
from datetime import datetime
from pathlib import Path


def _trim_context(context: dict) -> dict:
    """Ringkas konteks agar log tidak membengkak, simpan sinyal kunci saja."""
    if not context:
        return {}
    market = {}
    for coin, data in context.get("market", {}).items():
        compact = {"price": data.get("price")}
        tfs = data.get("timeframes", {})
        compact_tf = {}
        for tf, s in tfs.items():
            compact_tf[tf] = {
                "trend": s.get("trend"),
                "rsi": s.get("rsi"),
                "macd_state": (s.get("macd") or {}).get("state"),
                "atr_pct": s.get("atr_pct"),
                "change_pct": s.get("change_pct"),
            }
        if compact_tf:
            compact["timeframes"] = compact_tf
        fib = data.get("fibonacci")
        if fib:
            compact["fib_position_pct"] = fib.get("price_position_pct")
            compact["fib_nearest"] = fib.get("nearest_level")
        fr = data.get("funding_rate")
        if fr:
            compact["funding_annualized_pct"] = fr.get("annualized_pct")
        market[coin] = compact

    acct = context.get("account", {})
    return {
        "market": market,
        "account": {
            "account_value": acct.get("account_value"),
            "num_open_positions": acct.get("num_open_positions"),
            "daily_pnl_pct": acct.get("daily_pnl_pct"),
        },
    }


class DecisionLogger:
    def __init__(self, path: str = None):
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        self.path = Path(path) if path else (log_dir / "ai_decisions.jsonl")

    def log(self, entry: dict, context: dict = None):
        """Tambahkan satu baris keputusan ke file."""
        record = dict(entry)
        record["logged_at"] = datetime.now().isoformat(timespec="seconds")
        if context is not None:
            record["context"] = _trim_context(context)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"⚠️ Gagal menulis decision log: {e}")

    def read_all(self) -> list:
        """Baca semua keputusan tercatat."""
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
