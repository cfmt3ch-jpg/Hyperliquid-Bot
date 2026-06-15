"""
Risk Manager
============
Lapisan validasi KERAS antara keputusan AI dan eksekusi order.
AI hanya mengusulkan; Risk Manager yang memutuskan boleh/tidak,
serta menghitung ukuran posisi (size TIDAK ditentukan AI demi keamanan).

Aturan (dari config.AI_SETTINGS):
- Leverage maksimal
- Margin maksimal per trade (% nilai akun)
- Maksimal kerugian harian (% ekuitas awal hari)
- Confidence minimal
- Maksimal jumlah posisi terbuka
- Wajib stop loss
"""

from config import AI_SETTINGS

VALID_ACTIONS = {"open_long", "open_short", "close", "hold"}


class RiskDecision:
    """Hasil evaluasi risiko atas sebuah usulan AI."""

    def __init__(self, approved: bool, reason: str, order: dict = None):
        self.approved = approved
        self.reason = reason
        self.order = order or {}

    def __repr__(self):
        status = "APPROVED" if self.approved else "REJECTED"
        return f"<RiskDecision {status}: {self.reason}>"


class RiskManager:
    def __init__(self, settings: dict = None):
        s = settings or AI_SETTINGS
        self.max_leverage = s["max_leverage"]
        self.max_position_pct = s["max_position_pct"]
        self.max_daily_loss_pct = s["max_daily_loss_pct"]
        self.min_confidence = s["min_confidence"]
        self.max_open_positions = s["max_open_positions"]
        self.require_stop_loss = s["require_stop_loss"]

    # ── Pengecekan tingkat akun (sebelum trade apa pun) ──

    def daily_loss_breached(self, daily_pnl_pct: float) -> bool:
        """True jika kerugian harian sudah menembus batas."""
        return daily_pnl_pct <= -abs(self.max_daily_loss_pct)

    # ── Validasi usulan keputusan AI ──

    def evaluate(self, decision: dict, context: dict) -> RiskDecision:
        """
        Validasi satu keputusan AI terhadap aturan risiko.

        Args:
            decision: output AI (action, coin, leverage, tp/sl, confidence, ...)
            context: konteks dari build_context (untuk harga & status akun)

        Returns:
            RiskDecision
        """
        account = context["account"]
        daily_pnl_pct = account.get("daily_pnl_pct", 0.0)

        # 1. Circuit breaker kerugian harian
        if self.daily_loss_breached(daily_pnl_pct):
            return RiskDecision(
                False,
                f"Kerugian harian {daily_pnl_pct:.2f}% menembus batas "
                f"-{self.max_daily_loss_pct}%. Trading dihentikan hari ini."
            )

        action = decision.get("action")
        if action not in VALID_ACTIONS:
            return RiskDecision(False, f"Action tidak valid: {action}")

        # HOLD: tidak ada eksekusi, selalu "lolos"
        if action == "hold":
            return RiskDecision(True, "Hold — tidak ada aksi.", {"action": "hold"})

        coin = decision.get("coin")
        if not coin or coin not in context["market"]:
            return RiskDecision(False, f"Koin '{coin}' tidak dipantau / tidak ada harga.")

        # CLOSE: tutup posisi (selalu diizinkan jika posisi ada)
        if action == "close":
            open_coins = [p["coin"] for p in account["open_positions"]]
            if coin not in open_coins:
                return RiskDecision(False, f"Tidak ada posisi {coin} untuk ditutup.")
            return RiskDecision(True, f"Tutup posisi {coin}.",
                                {"action": "close", "coin": coin})

        # ── OPEN LONG / SHORT ──
        # 2. Confidence minimal
        confidence = float(decision.get("confidence", 0))
        if confidence < self.min_confidence:
            return RiskDecision(
                False,
                f"Confidence {confidence:.2f} < minimal {self.min_confidence}."
            )

        # 3. Batas jumlah posisi terbuka
        open_coins = [p["coin"] for p in account["open_positions"]]
        if coin not in open_coins and len(open_coins) >= self.max_open_positions:
            return RiskDecision(
                False,
                f"Sudah {len(open_coins)} posisi terbuka (maks {self.max_open_positions})."
            )

        # 4. Leverage di-clamp ke batas
        leverage = int(decision.get("leverage", 1))
        leverage = max(1, min(leverage, self.max_leverage))

        # 5. Wajib stop loss
        sl_pct = decision.get("stop_loss_pct")
        if self.require_stop_loss and not sl_pct:
            return RiskDecision(False, "Stop loss wajib diisi tapi tidak ada.")
        tp_pct = decision.get("take_profit_pct")

        # 6. Hitung size dari aturan risiko (BUKAN dari AI)
        account_value = account["account_value"]
        margin_per_trade = account_value * (self.max_position_pct / 100.0)
        price = context["market"][coin]["price"]
        if price <= 0:
            return RiskDecision(False, f"Harga {coin} tidak valid.")

        notional = margin_per_trade * leverage
        size = notional / price

        if size <= 0:
            return RiskDecision(False, "Size hasil perhitungan <= 0 (balance terlalu kecil).")

        is_buy = action == "open_long"
        order = {
            "action": action,
            "coin": coin,
            "is_buy": is_buy,
            "size": size,
            "leverage": leverage,
            "margin": round(margin_per_trade, 4),
            "take_profit_pct": float(tp_pct) if tp_pct else None,
            "stop_loss_pct": float(sl_pct) if sl_pct else None,
            "price_ref": price,
        }
        return RiskDecision(
            True,
            f"{action} {coin}: size {size:.6f} @ {leverage}x "
            f"(margin ${margin_per_trade:.2f}, conf {confidence:.2f}).",
            order
        )
