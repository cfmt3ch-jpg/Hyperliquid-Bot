#!/usr/bin/env python3
"""
📊 Evaluasi Performa AI Trading
================================
Menghitung metrik performa dari hasil paper trading + log keputusan AI.

Sumber data:
- paper_history.json        → trade tereksekusi (untuk metrik PnL)
- logs/ai_decisions.jsonl   → keputusan AI (untuk statistik perilaku)
- paper_state.json          → modal awal

Cara menjalankan:
    python evaluate.py
"""

import json
from collections import Counter
from pathlib import Path

from decision_logger import DecisionLogger

BASE = Path(__file__).parent


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _max_drawdown(equity_curve: list) -> dict:
    """Hitung max drawdown dari kurva ekuitas."""
    if not equity_curve:
        return {"max_drawdown_pct": 0.0, "max_drawdown_abs": 0.0}
    peak = equity_curve[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd_abs = peak - v
        dd_pct = (dd_abs / peak * 100) if peak else 0.0
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
    return {"max_drawdown_pct": round(max_dd_pct, 2), "max_drawdown_abs": round(max_dd_abs, 4)}


def evaluate() -> dict:
    """Kumpulkan semua metrik performa."""
    history = _load_json(BASE / "paper_history.json", [])
    state = _load_json(BASE / "paper_state.json", {})
    initial_balance = state.get("initial_balance", 100.0)
    decisions = DecisionLogger().read_all()

    # ── Metrik dari trade tertutup ──
    closes = [t for t in history if t.get("type") == "market_close" and "pnl" in t]
    pnls = [float(t["pnl"]) for t in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_profit = sum(wins)
    gross_loss = sum(losses)  # negatif
    net_pnl = sum(pnls)
    n = len(pnls)
    win_rate = (len(wins) / n * 100) if n else 0.0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (
        float("inf") if gross_profit > 0 else 0.0)
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = (net_pnl / n) if n else 0.0

    # Kurva ekuitas realized (modal awal + kumulatif PnL per close berurutan)
    equity_curve = [initial_balance]
    run = initial_balance
    for p in pnls:
        run += p
        equity_curve.append(run)
    dd = _max_drawdown(equity_curve)
    roi = ((run - initial_balance) / initial_balance * 100) if initial_balance else 0.0

    trade_metrics = {
        "initial_balance": round(initial_balance, 2),
        "closed_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "net_pnl": round(net_pnl, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "expectancy_per_trade": round(expectancy, 4),
        "best_trade": round(max(pnls), 4) if pnls else 0.0,
        "worst_trade": round(min(pnls), 4) if pnls else 0.0,
        "realized_roi_pct": round(roi, 2),
        **dd,
    }

    # ── Statistik keputusan AI ──
    action_counts = Counter(d.get("action") for d in decisions)
    approved = [d for d in decisions if d.get("approved")]
    rejected = [d for d in decisions if not d.get("approved")]
    open_decisions = [d for d in decisions
                      if d.get("action") in ("open_long", "open_short")
                      and d.get("confidence") is not None]
    avg_conf = (sum(d["confidence"] for d in open_decisions) / len(open_decisions)) if open_decisions else 0.0
    reject_reasons = Counter(d.get("verdict") for d in rejected)

    ai_metrics = {
        "total_decisions": len(decisions),
        "by_action": dict(action_counts),
        "approved": len(approved),
        "rejected": len(rejected),
        "approval_rate_pct": round(len(approved) / len(decisions) * 100, 2) if decisions else 0.0,
        "avg_confidence_open": round(avg_conf, 3),
        "top_reject_reasons": reject_reasons.most_common(5),
    }

    return {"trades": trade_metrics, "ai": ai_metrics}


def print_report():
    m = evaluate()
    t = m["trades"]
    a = m["ai"]

    print("\n" + "═" * 60)
    print("  📊 EVALUASI PERFORMA — PAPER TRADING")
    print("═" * 60)
    print(f"  Modal awal:          ${t['initial_balance']:>10,.2f}")
    print(f"  Trade selesai:       {t['closed_trades']:>10}")
    print(f"  Win / Loss:          {t['wins']:>5} / {t['losses']:<5}")
    print(f"  Win rate:            {t['win_rate_pct']:>9.2f}%")
    print(f"  Net PnL:             ${t['net_pnl']:>+10,.4f}")
    print(f"  Realized ROI:        {t['realized_roi_pct']:>+9.2f}%")
    print(f"  Profit factor:       {str(t['profit_factor']):>10}")
    print(f"  Avg win / avg loss:  ${t['avg_win']:>+8,.4f} / ${t['avg_loss']:>+8,.4f}")
    print(f"  Expectancy/trade:    ${t['expectancy_per_trade']:>+10,.4f}")
    print(f"  Best / worst trade:  ${t['best_trade']:>+8,.4f} / ${t['worst_trade']:>+8,.4f}")
    print(f"  Max drawdown:        {t['max_drawdown_pct']:>9.2f}% (${t['max_drawdown_abs']:,.4f})")
    print("─" * 60)
    print("  🤖 PERILAKU AI")
    print(f"  Total keputusan:     {a['total_decisions']:>10}")
    print(f"  Per action:          {a['by_action']}")
    print(f"  Approved/Rejected:   {a['approved']} / {a['rejected']} "
          f"(approval {a['approval_rate_pct']:.1f}%)")
    print(f"  Avg confidence open: {a['avg_confidence_open']:>10}")
    if a["top_reject_reasons"]:
        print("  Alasan reject teratas:")
        for reason, cnt in a["top_reject_reasons"]:
            print(f"    [{cnt}x] {reason}")
    print("═" * 60)
    if t["closed_trades"] == 0:
        print("  ℹ️ Belum ada trade selesai. Jalankan AI lebih lama untuk data.")
    print()


if __name__ == "__main__":
    print_report()
