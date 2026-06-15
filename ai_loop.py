#!/usr/bin/env python3
"""
🧠 AI Trading Loop (DeepSeek) — Fase 1: Paper Trading
======================================================
Menjalankan siklus keputusan AI di atas PaperTradingEngine (uang virtual,
harga real-time). AI memutuskan, RiskManager memvalidasi, engine eksekusi.

Cara menjalankan:
    python ai_loop.py            # loop otomatis
    python ai_loop.py --once     # satu siklus keputusan saja (uji)

⚠️  Fase 1 = SIMULASI. Tidak ada uang asli yang dipakai.
"""

import sys
import time
import threading
import argparse
from datetime import datetime, date

from paper_trading import PaperTradingEngine
from ai_context import build_context
from ai_trader import DeepSeekTrader
from risk_manager import RiskManager
from decision_logger import DecisionLogger
from config import AI_SETTINGS

INITIAL_BALANCE = 100.0


class AITradingLoop:
    def __init__(self, engine, trader, risk, logger: DecisionLogger = None):
        self.engine = engine
        self.trader = trader
        self.risk = risk
        self.logger = logger
        # Tracking kerugian harian
        self.today = date.today()
        self.day_start_equity = engine.get_account_value()
        # Posisi yang dikelola TP/SL: coin -> {tp_price, sl_price, is_buy}
        self.managed = {}
        # State terakhir untuk ditampilkan dashboard
        self.last_decision = None
        self.last_verdict = None
        self.last_step_time = None
        self.decision_log = []  # riwayat keputusan AI (in-memory)
        # Lock guard mutasi engine (step vs monitor TP/SL)
        self.lock = threading.RLock()
        self._monitor_running = False
        self._monitor_thread = None
        self._last_context = None  # konteks terakhir untuk logging

    # ── Tracking harian ──

    def _daily_pnl_pct(self) -> float:
        equity = self.engine.get_account_value()
        if self.day_start_equity <= 0:
            return 0.0
        return ((equity - self.day_start_equity) / self.day_start_equity) * 100

    def _roll_day_if_needed(self):
        if date.today() != self.today:
            self.today = date.today()
            self.day_start_equity = self.engine.get_account_value()
            print(f"📅 Hari baru. Reset ekuitas awal: ${self.day_start_equity:,.2f}")

    # ── Manajemen TP/SL ──

    def _check_tp_sl(self):
        """Tutup posisi yang menyentuh TP atau SL."""
        for coin in list(self.managed.keys()):
            if coin not in self.engine.positions:
                del self.managed[coin]
                continue
            m = self.managed[coin]
            price = self.engine.get_price(coin)
            hit = None
            if m["is_buy"]:
                if m["tp_price"] and price >= m["tp_price"]:
                    hit = "TP"
                elif m["sl_price"] and price <= m["sl_price"]:
                    hit = "SL"
            else:
                if m["tp_price"] and price <= m["tp_price"]:
                    hit = "TP"
                elif m["sl_price"] and price >= m["sl_price"]:
                    hit = "SL"
            if hit:
                self.engine.market_close(coin)
                del self.managed[coin]
                print(f"  {'🎯' if hit == 'TP' else '🛑'} {hit} {coin} @ ${price:,.2f} — posisi ditutup.")
                # Catat event TP/SL ke log keputusan
                event = {
                    "action": "tp_hit" if hit == "TP" else "sl_hit",
                    "coin": coin, "confidence": None,
                    "reasoning": f"{hit} tersentuh @ ${price:,.4f}",
                }
                self._record(event, True, f"{hit} auto-close", f"closed {coin} @ ${price:,.4f}")

    # ── Thread monitor TP/SL cepat (terpisah dari siklus AI) ──

    def monitor_tick(self):
        """Cek TP/SL sekali. Dipanggil thread monitor cepat."""
        with self.lock:
            self._roll_day_if_needed()
            self._check_tp_sl()

    def start_monitor(self, interval: int = None):
        """Mulai thread pemantau TP/SL (jauh lebih cepat dari siklus AI)."""
        if self._monitor_running:
            return
        interval = interval or AI_SETTINGS.get("tp_sl_check_seconds", 3)
        self._monitor_running = True

        def _run():
            print(f"👁️ Monitor TP/SL berjalan tiap {interval}s.")
            while self._monitor_running:
                try:
                    self.monitor_tick()
                except Exception as e:
                    print(f"Monitor TP/SL error: {e}")
                time.sleep(interval)
            print("👁️ Monitor TP/SL berhenti.")

        self._monitor_thread = threading.Thread(target=_run, daemon=True)
        self._monitor_thread.start()

    def stop_monitor(self):
        self._monitor_running = False

    # ── Eksekusi order hasil validasi ──

    def _execute(self, order: dict) -> str:
        action = order["action"]
        if action == "hold":
            print("  💤 Hold.")
            return "hold"
        coin = order["coin"]

        if action == "close":
            self.engine.market_close(coin)
            self.managed.pop(coin, None)
            print(f"  🔒 Closed {coin}.")
            return f"closed {coin}"

        # open_long / open_short
        result = self.engine.market_open(
            coin, order["is_buy"], order["size"], order["leverage"]
        )
        if result.get("status") != "ok":
            print(f"  ❌ Gagal buka posisi: {result.get('error')}")
            return f"error: {result.get('error')}"

        entry = float(result["response"]["data"]["statuses"][0]["filled"]["avgPx"])
        tp_pct = order.get("take_profit_pct")
        sl_pct = order.get("stop_loss_pct")
        is_buy = order["is_buy"]

        tp_price = sl_price = None
        if tp_pct:
            tp_price = entry * (1 + tp_pct / 100) if is_buy else entry * (1 - tp_pct / 100)
        if sl_pct:
            sl_price = entry * (1 - sl_pct / 100) if is_buy else entry * (1 + sl_pct / 100)

        self.managed[coin] = {"is_buy": is_buy, "tp_price": tp_price, "sl_price": sl_price}
        side = "LONG" if is_buy else "SHORT"
        print(f"  ✅ {side} {coin} {order['size']:.6f} @ ${entry:,.2f} "
              f"| TP: {f'${tp_price:,.2f}' if tp_price else '-'} "
              f"| SL: {f'${sl_price:,.2f}' if sl_price else '-'}")
        return f"opened {side} {coin} @ ${entry:,.2f}"

    # ── Rekam state untuk dashboard ──

    def _record(self, decision: dict, approved: bool, reason: str, executed: str):
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "action": decision.get("action"),
            "coin": decision.get("coin"),
            "confidence": decision.get("confidence"),
            "reasoning": decision.get("reasoning"),
            "approved": approved,
            "verdict": reason,
            "executed": executed,
        }
        self.last_decision = decision
        self.last_verdict = {"approved": approved, "reason": reason}
        self.last_step_time = entry["time"]
        self.decision_log.append(entry)
        # Batasi panjang log in-memory agar tidak membengkak
        if len(self.decision_log) > 100:
            self.decision_log = self.decision_log[-100:]
        # Persist ke file (dengan ringkasan konteks bila ada)
        if self.logger:
            self.logger.log(entry, self._last_context)

    # ── Satu siklus keputusan ──

    def step(self):
        # Bagian cepat: cek TP/SL + circuit breaker (di bawah lock)
        with self.lock:
            self._roll_day_if_needed()
            self._check_tp_sl()
            daily_pnl = self._daily_pnl_pct()
            equity = self.engine.get_account_value()
            breached = self.risk.daily_loss_breached(daily_pnl)

        print(f"\n{'─'*60}")
        print(f"🧠 Siklus AI @ {datetime.now():%H:%M:%S} | "
              f"Equity: ${equity:,.2f} | PnL hari ini: {daily_pnl:+.2f}%")

        if breached:
            reason = (f"Kerugian harian {daily_pnl:.2f}% menembus batas "
                      f"-{self.risk.max_daily_loss_pct}%. Tidak trading sampai besok.")
            print(f"  🛑 {reason}")
            halt_decision = {"action": "halt", "coin": None, "confidence": 0.0,
                             "reasoning": "Circuit breaker kerugian harian aktif."}
            self._record(halt_decision, False, reason, "halted")
            return

        # Bagian lambat: ambil data pasar + panggil AI (TANPA lock,
        # agar monitor TP/SL tetap responsif selama panggilan jaringan).
        context = build_context(self.engine, daily_pnl_pct=daily_pnl)
        self._last_context = context
        decision = self.trader.decide(context)
        print(f"  🤖 AI: {decision['action']} {decision.get('coin') or ''} "
              f"(conf {decision['confidence']:.2f}) — {decision['reasoning']}")

        # Bagian cepat: validasi + eksekusi (di bawah lock)
        with self.lock:
            verdict = self.risk.evaluate(decision, context)
            if not verdict.approved:
                print(f"  ⚖️ Risk REJECT: {verdict.reason}")
                self._record(decision, False, verdict.reason, "rejected")
                return
            print(f"  ⚖️ Risk OK: {verdict.reason}")
            executed = self._execute(verdict.order)
            self._record(decision, True, verdict.reason, executed)

    def run(self, interval: int):
        print(f"🚀 AI loop berjalan tiap {interval}s. Ctrl+C untuk berhenti.")
        self.start_monitor()
        try:
            while True:
                self.step()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n⛔ AI loop dihentikan.")
            self.stop_monitor()
            self.engine.print_account_summary()

    def run(self, interval: int):
        print(f"🚀 AI loop berjalan tiap {interval}s. Ctrl+C untuk berhenti.")
        try:
            while True:
                self.step()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n⛔ AI loop dihentikan.")
            self.engine.print_account_summary()


def main():
    parser = argparse.ArgumentParser(description="🧠 AI Trading Loop (DeepSeek) — Paper")
    parser.add_argument("--once", action="store_true", help="Jalankan satu siklus saja")
    args = parser.parse_args()

    print("📝 FASE 1: PAPER TRADING (simulasi, harga real-time, uang virtual)\n")
    engine = PaperTradingEngine(initial_balance=INITIAL_BALANCE)

    try:
        trader = DeepSeekTrader()
    except ValueError as e:
        print(e)
        sys.exit(1)

    risk = RiskManager()
    logger = DecisionLogger()
    loop = AITradingLoop(engine, trader, risk, logger)

    if args.once:
        loop.step()
        engine.print_account_summary()
    else:
        loop.run(AI_SETTINGS["loop_interval_seconds"])


if __name__ == "__main__":
    main()
