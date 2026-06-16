#!/usr/bin/env python3
"""
🧠 AI Trading Dashboard — LLM (Paper Trading)
===================================================
Dashboard monitor untuk sistem AI-driven. AI (LLM) memutuskan,
RiskManager memvalidasi, PaperTradingEngine mengeksekusi (simulasi).

Fitur:
- Start/Stop loop AI + jalankan satu siklus manual
- Tampilan keputusan AI terbaru (action, confidence, reasoning) + verdict risiko
- Panel guardrail risiko (PnL harian vs batas, cap leverage/size/posisi)
- Riwayat keputusan AI, posisi, history trade, harga real-time
- Kontrol darurat: Close All & Reset

Cara menjalankan:
    python dashboard.py
    Buka browser: http://localhost:5000

⚠️  FASE 1 = SIMULASI. Tidak ada uang asli yang dipakai.
"""

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

sys.path.insert(0, str(Path(__file__).parent))

from paper_trading import PaperTradingEngine
from ai_context import build_context
from ai_trader import LLMTrader
from risk_manager import RiskManager
from ai_loop import AITradingLoop
from decision_logger import DecisionLogger
from config import AI_SETTINGS, get_llm_config
import evaluate as evaluator

app = Flask(__name__)

INITIAL_BALANCE = 100.0
engine = PaperTradingEngine(initial_balance=INITIAL_BALANCE)
risk = RiskManager()
decision_logger = DecisionLogger()

# Trader AI — bisa gagal jika API key kosong; dashboard tetap jalan.
AI_AVAILABLE = True
AI_INIT_ERROR = ""
try:
    trader = LLMTrader()
    loop = AITradingLoop(engine, trader, risk, decision_logger)
    # Monitor TP/SL cepat berjalan terus (menegakkan SL walau loop AI berhenti)
    loop.start_monitor()
except ValueError as e:
    AI_AVAILABLE = False
    AI_INIT_ERROR = str(e)
    trader = None
    loop = None

# ── State loop AI ──
ai_state = {
    "running": False,
    "interval": AI_SETTINGS["loop_interval_seconds"],
}
ai_thread = None
ai_lock = threading.Lock()
step_lock = threading.Lock()  # cegah dua siklus jalan bersamaan


def ai_runner():
    """Thread background: jalankan siklus AI tiap interval."""
    print("🚀 AI loop thread dimulai.")
    while ai_state["running"]:
        try:
            with step_lock:
                loop.step()
        except Exception as e:
            print(f"AI loop error: {e}")
        # Tidur bertahap agar Stop responsif
        slept = 0
        while ai_state["running"] and slept < ai_state["interval"]:
            time.sleep(1)
            slept += 1
    print("📊 AI loop thread berhenti.")


# ═══════════════════════════════════════════════════════════════
# API: AKUN / POSISI / HISTORY / HARGA
# ═══════════════════════════════════════════════════════════════

@app.route("/api/account")
def api_account():
    account_value = engine.get_account_value()
    unrealized = engine.get_unrealized_pnl()
    realized = engine.get_realized_pnl()
    roi = ((account_value - engine.initial_balance) / engine.initial_balance) * 100
    return jsonify({
        "initial_balance": engine.initial_balance,
        "balance": round(engine.balance, 4),
        "account_value": round(account_value, 4),
        "unrealized_pnl": round(unrealized, 4),
        "realized_pnl": round(realized, 4),
        "roi": round(roi, 2),
        "margin_used": round(account_value - engine.balance, 4),
    })


@app.route("/api/positions")
def api_positions():
    positions = engine.get_positions_summary()
    return jsonify({
        "positions": [{
            "coin": p["coin"],
            "side": p["side"],
            "size": round(p["size"], 6),
            "entry_price": round(p["entry_price"], 2),
            "current_price": round(p["current_price"], 2),
            "leverage": p["leverage"],
            "unrealized_pnl": round(p["unrealized_pnl"], 4),
            "pnl_pct": round(p["pnl_pct"], 2),
            "margin_used": round(p["margin_used"], 4),
        } for p in positions]
    })


@app.route("/api/history")
def api_history():
    limit = request.args.get("limit", 50, type=int)
    history = engine.trade_history[-limit:]
    history = list(reversed(history))
    return jsonify({"trades": history, "total": len(engine.trade_history)})


@app.route("/api/prices")
def api_prices():
    try:
        prices = engine.get_all_prices()
        watch = {}
        for coin in AI_SETTINGS["coins"]:
            if coin in prices:
                watch[coin] = round(prices[coin], 2)
        return jsonify({"prices": watch, "timestamp": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# API: AI CONTROL & STATUS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/ai/status")
def api_ai_status():
    account_value = engine.get_account_value()
    daily_pnl_pct = loop._daily_pnl_pct() if loop else 0.0
    halted = risk.daily_loss_breached(daily_pnl_pct)
    return jsonify({
        "available": AI_AVAILABLE,
        "init_error": AI_INIT_ERROR,
        "running": ai_state["running"],
        "interval": ai_state["interval"],
        "model": get_llm_config()["model"],
        "base_url": get_llm_config()["base_url"],
        "daily_pnl_pct": round(daily_pnl_pct, 2),
        "halted": halted,
        "day_start_equity": round(loop.day_start_equity, 4) if loop else account_value,
        "last_step_time": loop.last_step_time if loop else None,
        "last_decision": loop.last_decision if loop else None,
        "last_verdict": loop.last_verdict if loop else None,
        "guardrails": {
            "max_leverage": risk.max_leverage,
            "max_position_pct": risk.max_position_pct,
            "max_daily_loss_pct": risk.max_daily_loss_pct,
            "min_confidence": risk.min_confidence,
            "max_open_positions": risk.max_open_positions,
            "min_stop_loss_pct": risk.min_stop_loss_pct,
            "max_stop_loss_pct": risk.max_stop_loss_pct,
            "min_risk_reward": risk.min_risk_reward,
        },
    })


@app.route("/api/ai/decisions")
def api_ai_decisions():
    limit = request.args.get("limit", 30, type=int)
    if not loop:
        return jsonify({"decisions": []})
    decisions = list(reversed(loop.decision_log[-limit:]))
    return jsonify({"decisions": decisions})


@app.route("/api/metrics")
def api_metrics():
    """Metrik evaluasi performa (dari history + log keputusan)."""
    try:
        return jsonify(evaluator.evaluate())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/start", methods=["POST"])
def api_ai_start():
    global ai_thread
    if not AI_AVAILABLE:
        return jsonify({"status": "error", "message": AI_INIT_ERROR}), 400
    with ai_lock:
        if ai_state["running"]:
            return jsonify({"status": "error", "message": "AI loop sudah berjalan."}), 400
        data = request.json or {}
        interval = int(data.get("interval", ai_state["interval"]))
        ai_state["interval"] = max(10, interval)
        ai_state["running"] = True
        ai_thread = threading.Thread(target=ai_runner, daemon=True)
        ai_thread.start()
    return jsonify({"status": "ok", "message": f"AI loop dimulai (tiap {ai_state['interval']}s)."})


@app.route("/api/ai/stop", methods=["POST"])
def api_ai_stop():
    with ai_lock:
        if not ai_state["running"]:
            return jsonify({"status": "error", "message": "AI loop tidak berjalan."}), 400
        ai_state["running"] = False
    return jsonify({"status": "ok", "message": "AI loop dihentikan."})


@app.route("/api/ai/step", methods=["POST"])
def api_ai_step():
    """Jalankan satu siklus AI sekarang (manual trigger)."""
    if not AI_AVAILABLE:
        return jsonify({"status": "error", "message": AI_INIT_ERROR}), 400
    if not step_lock.acquire(blocking=False):
        return jsonify({"status": "error", "message": "Siklus sedang berjalan, tunggu."}), 409
    try:
        loop.step()
    finally:
        step_lock.release()
    return jsonify({
        "status": "ok",
        "decision": loop.last_decision,
        "verdict": loop.last_verdict,
    })


# ═══════════════════════════════════════════════════════════════
# API: KONTROL DARURAT (manual override)
# ═══════════════════════════════════════════════════════════════

@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.json or {}
    action = data.get("action")

    if action == "close":
        coin = data.get("coin")
        result = engine.market_close(coin)
        if loop:
            loop.managed.pop(coin, None)
        return jsonify(result)

    if action == "close_all":
        results = []
        for c in list(engine.positions.keys()):
            results.append(engine.market_close(c))
            if loop:
                loop.managed.pop(c, None)
        return jsonify({"status": "ok", "results": results})

    if action == "reset":
        # Hentikan AI dulu agar tidak menimpa state
        ai_state["running"] = False
        time.sleep(0.2)
        engine.reset()
        if loop:
            loop.managed.clear()
            loop.decision_log.clear()
            loop.last_decision = None
            loop.last_verdict = None
            loop.day_start_equity = engine.get_account_value()
        return jsonify({"status": "ok", "message": "Akun di-reset ke $100 & AI dihentikan."})

    return jsonify({"status": "error", "message": "Action tidak valid"}), 400


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


# ═══════════════════════════════════════════════════════════════
# HTML DASHBOARD
# ═══════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🧠 AI Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0e17; color: #e1e5eb; min-height: 100vh; }
        .header {
            background: linear-gradient(135deg, #1a1f2e 0%, #0d1117 100%);
            border-bottom: 1px solid #1e2a3a; padding: 16px 24px;
            display: flex; align-items: center; justify-content: space-between;
        }
        .header h1 { font-size: 20px; background: linear-gradient(90deg, #00d4ff, #7b61ff);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .header .badges { display: flex; gap: 10px; }
        .badge { background: #1a2332; border: 1px solid #2d3748; padding: 6px 14px;
            border-radius: 20px; font-size: 12px; color: #8b95a5; }
        .badge.pulse { animation: pulse 2s infinite; }
        .badge.active { background: #065f46; border-color: #22c55e; color: #bbf7d0; }
        .badge.danger { background: #7f1d1d; border-color: #ef4444; color: #fecaca; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }

        .container { max-width: 1400px; margin: 0 auto; padding: 20px;
            display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 16px; }
        .full-width { grid-column: 1 / -1; }
        .half-width { grid-column: span 2; }

        .card { background: #111827; border: 1px solid #1e2a3a; border-radius: 12px; padding: 20px; }
        .card-title { font-size: 12px; text-transform: uppercase; letter-spacing: 1px;
            color: #6b7280; margin-bottom: 12px; }
        .card-value { font-size: 28px; font-weight: 700; letter-spacing: -0.5px; }
        .card-sub { font-size: 13px; color: #6b7280; margin-top: 4px; }

        .positive { color: #22c55e; } .negative { color: #ef4444; } .neutral { color: #8b95a5; }
        .stats-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; }

        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase;
            color: #6b7280; border-bottom: 1px solid #1e2a3a; }
        td { padding: 12px; font-size: 14px; border-bottom: 1px solid #111827; }
        tr:hover td { background: #1a2332; }

        .side-badge { padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
        .side-long { background: rgba(34,197,94,0.15); color: #22c55e; }
        .side-short { background: rgba(239,68,68,0.15); color: #ef4444; }

        .btn { padding: 9px 18px; border-radius: 6px; border: none; font-size: 13px;
            font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .btn:hover { transform: translateY(-1px); }
        .btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
        .btn-start { background: linear-gradient(135deg,#22c55e,#16a34a); color: white; }
        .btn-stop { background: linear-gradient(135deg,#ef4444,#dc2626); color: white; }
        .btn-step { background: linear-gradient(135deg,#7b61ff,#6c47ff); color: white; }
        .btn-close { background: linear-gradient(135deg,#f59e0b,#d97706); color: white; }
        .btn-reset { background: #374151; color: #9ca3af; }
        .btn-reset:hover { background: #4b5563; color: white; }

        .ai-controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
        .ai-controls input { background: #1a2332; border: 1px solid #2d3748; border-radius: 6px;
            padding: 8px 12px; color: #e1e5eb; font-size: 14px; width: 90px; }

        .action-pill { padding: 4px 12px; border-radius: 6px; font-size: 13px; font-weight: 700; text-transform: uppercase; }
        .a-open_long { background: rgba(34,197,94,0.15); color: #22c55e; }
        .a-open_short { background: rgba(239,68,68,0.15); color: #ef4444; }
        .a-close { background: rgba(245,158,11,0.15); color: #f59e0b; }
        .a-hold { background: rgba(139,149,165,0.15); color: #8b95a5; }
        .a-halt { background: rgba(239,68,68,0.25); color: #fecaca; }

        .verdict-ok { color: #22c55e; } .verdict-reject { color: #ef4444; }

        .conf-bar { width: 100%; height: 8px; background: #1e2a3a; border-radius: 4px; overflow: hidden; margin-top: 6px; }
        .conf-fill { height: 100%; background: linear-gradient(90deg,#ef4444,#f59e0b,#22c55e); transition: width 0.4s; }

        .guardrail-row { display: flex; justify-content: space-between; padding: 8px 0;
            border-bottom: 1px solid #1a2332; font-size: 13px; }
        .guardrail-row:last-child { border-bottom: none; }
        .guardrail-label { color: #8b95a5; }

        .price-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px,1fr)); gap: 10px; }
        .price-card { background: #1a2332; border: 1px solid #2d3748; border-radius: 8px; padding: 12px; text-align: center; }
        .price-card .coin-name { font-size: 13px; font-weight: 700; color: #7b61ff; }
        .price-card .coin-price { font-size: 16px; font-weight: 600; margin-top: 4px; }

        .decision-item { padding: 10px 0; border-bottom: 1px solid #1a2332; font-size: 13px; }
        .decision-item:last-child { border-bottom: none; }
        .decision-head { display: flex; align-items: center; gap: 8px; }
        .decision-reason { color: #8b95a5; font-size: 12px; margin-top: 4px; }
        .decision-time { color: #4b5563; font-size: 11px; margin-left: auto; }

        .toast { position: fixed; bottom: 20px; right: 20px; padding: 12px 20px; border-radius: 8px;
            font-size: 13px; z-index: 1000; animation: slideIn 0.3s ease; max-width: 350px; }
        .toast-success { background: #065f46; border: 1px solid #22c55e; color: #bbf7d0; }
        .toast-error { background: #7f1d1d; border: 1px solid #ef4444; color: #fecaca; }
        @keyframes slideIn { from{transform:translateX(100%);opacity:0} to{transform:translateX(0);opacity:1} }

        .empty-state { text-align: center; padding: 30px; color: #4b5563; }
        .empty-state .icon { font-size: 32px; margin-bottom: 8px; }

        @media (max-width: 900px) {
            .container { grid-template-columns: 1fr; }
            .half-width { grid-column: span 1; }
            .stats-grid { grid-template-columns: repeat(2,1fr); }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 AI Trading Dashboard</h1>
        <div class="badges">
            <span class="badge pulse">● Live (Paper)</span>
            <span class="badge" id="ai-badge">AI: OFF</span>
            <span class="badge" id="time-badge">--:--:--</span>
        </div>
    </div>

    <div class="container">
        <!-- Account Stats -->
        <div class="card full-width">
            <div class="stats-grid">
                <div><div class="card-title">💰 Modal Awal</div><div class="card-value neutral" id="initial-balance">$100.00</div></div>
                <div><div class="card-title">📊 Nilai Akun</div><div class="card-value" id="account-value">$100.00</div></div>
                <div><div class="card-title">📈 Total PnL</div><div class="card-value" id="total-pnl">$0.00</div><div class="card-sub" id="roi-text">ROI: 0.00%</div></div>
                <div><div class="card-title">💵 Available</div><div class="card-value neutral" id="balance">$100.00</div><div class="card-sub" id="margin-text">Margin: $0.00</div></div>
            </div>
        </div>

        <!-- AI Control + Latest Decision -->
        <div class="card half-width">
            <div class="card-title">🤖 Kontrol AI</div>
            <div class="ai-controls">
                <button class="btn btn-start" id="btn-start" onclick="aiStart()">▶ Start AI</button>
                <button class="btn btn-stop" id="btn-stop" onclick="aiStop()">⏹ Stop AI</button>
                <button class="btn btn-step" onclick="aiStep()">⚡ Run Once</button>
                <label style="font-size:12px;color:#6b7280">Interval (s):</label>
                <input type="number" id="ai-interval" value="300" min="10">
            </div>
            <div id="ai-init-error" style="display:none;margin-top:12px;color:#ef4444;font-size:13px"></div>

            <div style="margin-top:16px">
                <div class="card-title">Keputusan AI Terbaru</div>
                <div id="latest-decision">
                    <div class="empty-state"><div class="icon">🤖</div><div>Belum ada keputusan</div></div>
                </div>
            </div>
        </div>

        <!-- Risk Guardrails -->
        <div class="card half-width">
            <div class="card-title">⚖️ Guardrail Risiko</div>
            <div id="daily-pnl-box" style="margin-bottom:14px">
                <div style="display:flex;justify-content:space-between">
                    <span class="guardrail-label">PnL Harian</span>
                    <span id="daily-pnl-val" class="neutral">+0.00%</span>
                </div>
                <div class="conf-bar"><div class="conf-fill" id="daily-pnl-bar" style="width:0%"></div></div>
                <div style="font-size:11px;color:#4b5563;margin-top:4px" id="daily-pnl-note">Batas berhenti: -5%</div>
            </div>
            <div class="guardrail-row"><span class="guardrail-label">Leverage maks</span><span id="g-lev">5x</span></div>
            <div class="guardrail-row"><span class="guardrail-label">Margin/trade maks</span><span id="g-pos">1%</span></div>
            <div class="guardrail-row"><span class="guardrail-label">Loss harian maks</span><span id="g-loss">5%</span></div>
            <div class="guardrail-row"><span class="guardrail-label">Confidence min</span><span id="g-conf">0.6</span></div>
            <div class="guardrail-row"><span class="guardrail-label">Posisi terbuka maks</span><span id="g-maxpos">3</span></div>
            <div class="guardrail-row"><span class="guardrail-label">Rentang SL</span><span id="g-sl">0.5% - 10%</span></div>
            <div class="guardrail-row"><span class="guardrail-label">Risk/Reward min</span><span id="g-rr">1.0</span></div>
        </div>

        <!-- Prices -->
        <div class="card full-width">
            <div class="card-title">💹 Harga Real-Time</div>
            <div class="price-grid" id="price-grid"><div style="color:#6b7280">Memuat harga...</div></div>
        </div>

        <!-- Performance Metrics -->
        <div class="card full-width">
            <div class="card-title">📊 Evaluasi Performa</div>
            <div class="stats-grid" id="metrics-grid">
                <div><div class="card-title">Win Rate</div><div class="card-value neutral" id="m-winrate">-</div><div class="card-sub" id="m-wl">- W / - L</div></div>
                <div><div class="card-title">Net PnL (realized)</div><div class="card-value" id="m-netpnl">-</div><div class="card-sub" id="m-roi">ROI: -</div></div>
                <div><div class="card-title">Profit Factor</div><div class="card-value neutral" id="m-pf">-</div><div class="card-sub" id="m-exp">Expectancy: -</div></div>
                <div><div class="card-title">Max Drawdown</div><div class="card-value negative" id="m-dd">-</div><div class="card-sub" id="m-trades">Trade: -</div></div>
            </div>
            <div style="font-size:12px;color:#6b7280;margin-top:10px" id="m-ai-note">Statistik AI: -</div>
        </div>

        <!-- Positions -->
        <div class="card half-width">
            <div class="card-title">📌 Posisi Terbuka (dikelola AI)</div>
            <div id="positions-container"><div class="empty-state"><div class="icon">📭</div><div>Belum ada posisi</div></div></div>
        </div>

        <!-- AI Decision Log -->
        <div class="card half-width">
            <div class="card-title">🧠 Riwayat Keputusan AI</div>
            <div id="decisions-container" style="max-height:320px;overflow-y:auto"><div class="empty-state"><div class="icon">📭</div><div>Belum ada</div></div></div>
        </div>

        <!-- Trade History -->
        <div class="card half-width">
            <div class="card-title">📜 Trade History</div>
            <div id="history-container" style="max-height:320px;overflow-y:auto"><div class="empty-state"><div class="icon">📭</div><div>Belum ada trade</div></div></div>
        </div>

        <!-- Emergency Controls -->
        <div class="card half-width">
            <div class="card-title">🚨 Kontrol Darurat</div>
            <div class="ai-controls">
                <button class="btn btn-close" onclick="closeAll()">🔒 Close All</button>
                <button class="btn btn-reset" onclick="doReset()">🔄 Reset $100</button>
            </div>
            <div style="font-size:12px;color:#6b7280;margin-top:10px">Reset akan menghentikan AI dan mengembalikan akun ke $100.</div>
        </div>
    </div>

    <script>
    function fmt(n, d=2) { return Number(n).toLocaleString('en-US', {minimumFractionDigits:d, maximumFractionDigits:d}); }
    function pnlClass(n) { return n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral'; }
    function showToast(msg, type='success') {
        const t = document.createElement('div'); t.className = `toast toast-${type}`; t.textContent = msg;
        document.body.appendChild(t); setTimeout(() => t.remove(), 4000);
    }
    async function fetchJSON(url) { return (await fetch(url)).json(); }
    async function postJSON(url, body) {
        const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
        return r.json();
    }

    async function updateAccount() {
        try {
            const d = await fetchJSON('/api/account');
            document.getElementById('initial-balance').textContent = `$${fmt(d.initial_balance)}`;
            const av = document.getElementById('account-value');
            av.textContent = `$${fmt(d.account_value)}`; av.className = `card-value ${pnlClass(d.roi)}`;
            const totalPnl = d.unrealized_pnl + d.realized_pnl;
            const pnlEl = document.getElementById('total-pnl');
            pnlEl.textContent = `$${totalPnl>=0?'+':''}${fmt(totalPnl)}`; pnlEl.className = `card-value ${pnlClass(totalPnl)}`;
            const roiEl = document.getElementById('roi-text');
            roiEl.textContent = `ROI: ${d.roi>=0?'+':''}${d.roi.toFixed(2)}%`; roiEl.className = `card-sub ${pnlClass(d.roi)}`;
            document.getElementById('balance').textContent = `$${fmt(d.balance)}`;
            document.getElementById('margin-text').textContent = `Margin: $${fmt(d.margin_used)}`;
        } catch(e) { console.error(e); }
    }

    async function updatePrices() {
        try {
            const d = await fetchJSON('/api/prices');
            const grid = document.getElementById('price-grid'); grid.innerHTML = '';
            for (const [coin, price] of Object.entries(d.prices || {})) {
                grid.innerHTML += `<div class="price-card"><div class="coin-name">${coin}</div><div class="coin-price">$${fmt(price, price>100?2:4)}</div></div>`;
            }
        } catch(e) { console.error(e); }
    }

    async function updatePositions() {
        try {
            const d = await fetchJSON('/api/positions');
            const c = document.getElementById('positions-container');
            if (!d.positions.length) { c.innerHTML = '<div class="empty-state"><div class="icon">📭</div><div>Belum ada posisi</div></div>'; return; }
            let h = '<table><tr><th>Koin</th><th>Side</th><th>Size</th><th>Entry</th><th>Now</th><th>Lev</th><th>PnL</th><th></th></tr>';
            for (const p of d.positions) {
                const cls = p.unrealized_pnl >= 0 ? 'positive' : 'negative';
                h += `<tr><td><strong>${p.coin}</strong></td><td><span class="side-badge side-${p.side.toLowerCase()}">${p.side}</span></td><td>${p.size}</td><td>$${fmt(p.entry_price)}</td><td>$${fmt(p.current_price)}</td><td>${p.leverage}x</td><td class="${cls}">${p.unrealized_pnl>=0?'+':''}${fmt(p.pnl_pct)}%</td><td><button class="btn btn-close" style="padding:4px 10px;font-size:11px" onclick="closePos('${p.coin}')">Close</button></td></tr>`;
            }
            c.innerHTML = h + '</table>';
        } catch(e) { console.error(e); }
    }

    async function updateHistory() {
        try {
            const d = await fetchJSON('/api/history?limit=20');
            const c = document.getElementById('history-container');
            if (!d.trades.length) { c.innerHTML = '<div class="empty-state"><div class="icon">📭</div><div>Belum ada trade</div></div>'; return; }
            let h = '';
            for (const t of d.trades) {
                const ts = t.timestamp ? t.timestamp.substring(11,19) : '--:--';
                const tl = t.type === 'market_open' ? 'OPEN' : 'CLOSE';
                const pnl = t.pnl !== undefined ? `<span class="${pnlClass(t.pnl)}">${t.pnl>=0?'+':''}${fmt(t.pnl,4)}</span>` : '';
                h += `<div class="decision-item"><div class="decision-head"><span class="action-pill ${tl==='OPEN'?'a-open_long':'a-close'}">${tl}</span><strong>${t.side||''} ${t.coin}</strong><span class="decision-time">${ts}</span></div><div class="decision-reason">${t.size} @ $${fmt(t.fill_price)} ${pnl}</div></div>`;
            }
            c.innerHTML = h;
        } catch(e) { console.error(e); }
    }

    function renderDecision(d) {
        if (!d) return '<div class="empty-state"><div class="icon">🤖</div><div>Belum ada keputusan</div></div>';
        const conf = (d.confidence || 0) * 100;
        return `<div class="decision-head"><span class="action-pill a-${d.action}">${d.action}</span>
            <strong>${d.coin || ''}</strong></div>
            <div style="font-size:12px;color:#6b7280;margin-top:6px">Confidence: ${(d.confidence||0).toFixed(2)}</div>
            <div class="conf-bar"><div class="conf-fill" style="width:${conf}%"></div></div>
            <div class="decision-reason" style="margin-top:8px">${d.reasoning || ''}</div>`;
    }

    async function updateAI() {
        try {
            const s = await fetchJSON('/api/ai/status');
            const badge = document.getElementById('ai-badge');

            if (!s.available) {
                badge.textContent = 'AI: NO KEY'; badge.className = 'badge danger';
                const errEl = document.getElementById('ai-init-error');
                errEl.style.display = 'block'; errEl.textContent = s.init_error;
                document.getElementById('btn-start').disabled = true;
            } else if (s.halted) {
                badge.textContent = 'AI: HALTED'; badge.className = 'badge danger';
            } else if (s.running) {
                badge.textContent = '● AI: ON'; badge.className = 'badge active';
            } else {
                badge.textContent = 'AI: OFF'; badge.className = 'badge';
            }

            document.getElementById('btn-start').disabled = s.running || !s.available;
            document.getElementById('btn-stop').disabled = !s.running;

            // Guardrails
            const g = s.guardrails;
            document.getElementById('g-lev').textContent = `${g.max_leverage}x`;
            document.getElementById('g-pos').textContent = `${g.max_position_pct}%`;
            document.getElementById('g-loss').textContent = `${g.max_daily_loss_pct}%`;
            document.getElementById('g-conf').textContent = g.min_confidence;
            document.getElementById('g-maxpos').textContent = g.max_open_positions;
            if (g.min_stop_loss_pct !== undefined) {
                document.getElementById('g-sl').textContent = `${g.min_stop_loss_pct}% - ${g.max_stop_loss_pct}%`;
                document.getElementById('g-rr').textContent = g.min_risk_reward;
            }

            // Daily PnL bar
            const dp = s.daily_pnl_pct || 0;
            const dpVal = document.getElementById('daily-pnl-val');
            dpVal.textContent = `${dp>=0?'+':''}${dp.toFixed(2)}%`; dpVal.className = pnlClass(dp);
            const limit = g.max_daily_loss_pct;
            const lossFrac = dp < 0 ? Math.min(100, (Math.abs(dp)/limit)*100) : 0;
            const bar = document.getElementById('daily-pnl-bar');
            bar.style.width = `${lossFrac}%`;
            bar.style.background = lossFrac > 80 ? '#ef4444' : (lossFrac > 50 ? '#f59e0b' : '#22c55e');
            document.getElementById('daily-pnl-note').textContent = s.halted ? '🛑 BERHENTI: batas loss harian tercapai' : `Batas berhenti: -${limit}%`;

            // Latest decision
            document.getElementById('latest-decision').innerHTML = renderDecision(s.last_decision);
        } catch(e) { console.error(e); }
    }

    async function updateDecisions() {
        try {
            const d = await fetchJSON('/api/ai/decisions?limit=30');
            const c = document.getElementById('decisions-container');
            if (!d.decisions.length) { c.innerHTML = '<div class="empty-state"><div class="icon">📭</div><div>Belum ada</div></div>'; return; }
            let h = '';
            for (const x of d.decisions) {
                const ts = x.time ? x.time.substring(11,19) : '';
                const vcls = x.approved ? 'verdict-ok' : 'verdict-reject';
                const vtxt = x.approved ? '✓ ' + (x.executed||'OK') : '✗ ' + (x.verdict||'reject');
                h += `<div class="decision-item"><div class="decision-head"><span class="action-pill a-${x.action}">${x.action}</span><strong>${x.coin||''}</strong><span style="font-size:11px;color:#6b7280">conf ${(x.confidence||0).toFixed(2)}</span><span class="decision-time">${ts}</span></div><div class="decision-reason">${x.reasoning||''}</div><div class="${vcls}" style="font-size:11px;margin-top:4px">${vtxt}</div></div>`;
            }
            c.innerHTML = h;
        } catch(e) { console.error(e); }
    }

    async function updateMetrics() {
        try {
            const m = await fetchJSON('/api/metrics');
            if (m.error) return;
            const t = m.trades, a = m.ai;
            document.getElementById('m-winrate').textContent = `${t.win_rate_pct.toFixed(1)}%`;
            document.getElementById('m-wl').textContent = `${t.wins} W / ${t.losses} L`;
            const netEl = document.getElementById('m-netpnl');
            netEl.textContent = `$${t.net_pnl>=0?'+':''}${fmt(t.net_pnl,4)}`; netEl.className = `card-value ${pnlClass(t.net_pnl)}`;
            const roiEl = document.getElementById('m-roi');
            roiEl.textContent = `ROI: ${t.realized_roi_pct>=0?'+':''}${t.realized_roi_pct.toFixed(2)}%`; roiEl.className = `card-sub ${pnlClass(t.realized_roi_pct)}`;
            document.getElementById('m-pf').textContent = t.profit_factor;
            document.getElementById('m-exp').textContent = `Expectancy: $${fmt(t.expectancy_per_trade,4)}`;
            document.getElementById('m-dd').textContent = `${t.max_drawdown_pct.toFixed(2)}%`;
            document.getElementById('m-trades').textContent = `Trade selesai: ${t.closed_trades}`;
            const acts = Object.entries(a.by_action||{}).map(([k,v])=>`${k}:${v}`).join(' · ');
            document.getElementById('m-ai-note').textContent = `AI: ${a.total_decisions} keputusan (approval ${a.approval_rate_pct.toFixed(0)}%, avg conf ${a.avg_confidence_open}) — ${acts}`;
        } catch(e) { console.error(e); }
    }

    async function aiStart() {
        const interval = parseInt(document.getElementById('ai-interval').value) || 300;
        const d = await postJSON('/api/ai/start', {interval});
        showToast(d.message, d.status==='ok'?'success':'error'); refreshAll();
    }
    async function aiStop() { const d = await postJSON('/api/ai/stop'); showToast(d.message, d.status==='ok'?'success':'error'); refreshAll(); }
    async function aiStep() {
        showToast('⚡ Menjalankan satu siklus AI...', 'success');
        const d = await postJSON('/api/ai/step');
        if (d.status==='ok') { showToast(`🤖 ${d.decision.action} ${d.decision.coin||''} — ${d.verdict.approved?'approved':'rejected'}`); }
        else { showToast(`❌ ${d.message}`, 'error'); }
        refreshAll();
    }
    async function closePos(coin) { await postJSON('/api/control', {action:'close', coin}); showToast(`🔒 Closed ${coin}`); refreshAll(); }
    async function closeAll() { await postJSON('/api/control', {action:'close_all'}); showToast('🔒 Semua posisi ditutup'); refreshAll(); }
    async function doReset() {
        if (!confirm('Reset akun ke $100 dan hentikan AI?')) return;
        await postJSON('/api/control', {action:'reset'}); showToast('🔄 Akun di-reset ke $100'); refreshAll();
    }

    function refreshAll() { updateAccount(); updatePrices(); updatePositions(); updateHistory(); updateAI(); updateDecisions(); }

    setInterval(() => { document.getElementById('time-badge').textContent = new Date().toLocaleTimeString('id-ID'); }, 1000);
    refreshAll();
    updateMetrics();
    setInterval(refreshAll, 3000);
    setInterval(updateMetrics, 15000);  // metrik di-refresh lebih jarang
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║      🧠 AI Trading Dashboard — LLM (Paper)               ║")
    print("║      💰 Modal: $100 | Risiko: 5x / 1% / loss harian 5%    ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║   🌐 Buka browser: http://127.0.0.1:5000                   ║")
    if not AI_AVAILABLE:
        print("║   ⚠️  AI nonaktif: API key LLM belum diisi                 ║")
    print("║   Ctrl+C untuk berhenti                                    ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    # Bind ke localhost saja demi keamanan (dashboard bisa eksekusi trade)
    app.run(host="127.0.0.1", port=5000, debug=False)
