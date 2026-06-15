"""
Paper Trading Engine untuk Hyperliquid
=======================================
Simulasi trading dengan harga real-time dari Hyperliquid,
tanpa menggunakan uang sungguhan.

Fitur:
- Harga real-time dari Hyperliquid API
- Simulasi posisi Long/Short
- Tracking PnL (realized & unrealized)
- Support limit & market order
- Log semua transaksi
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from hyperliquid.info import Info
from hyperliquid.utils import constants


class PaperPosition:
    """Representasi posisi paper trading."""

    def __init__(self, coin: str, is_buy: bool, size: float, entry_price: float, leverage: int = 1):
        self.coin = coin
        self.is_buy = is_buy
        self.size = size  # positif = long, negatif = short
        self.entry_price = entry_price
        self.leverage = leverage
        self.entry_time = datetime.now()
        self.margin_used = (size * entry_price) / leverage

    @property
    def side(self) -> str:
        return "LONG" if self.is_buy else "SHORT"

    def unrealized_pnl(self, current_price: float) -> float:
        if self.is_buy:
            return (current_price - self.entry_price) * self.size
        else:
            return (self.entry_price - current_price) * abs(self.size)

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.is_buy:
            return ((current_price - self.entry_price) / self.entry_price) * 100 * self.leverage
        else:
            return ((self.entry_price - current_price) / self.entry_price) * 100 * self.leverage

    def to_dict(self) -> dict:
        return {
            "coin": self.coin,
            "side": self.side,
            "size": self.size,
            "entry_price": self.entry_price,
            "leverage": self.leverage,
            "margin_used": self.margin_used,
            "entry_time": self.entry_time.isoformat(),
        }


class PaperTradingEngine:
    """
    Mesin paper trading yang simulasi trading di Hyperliquid.
    Menggunakan harga real-time, tapi semua order adalah simulasi.
    """

    def __init__(self, initial_balance: float = 100.0):
        # Koneksi ke Hyperliquid untuk harga real-time (public, no auth)
        self.info = Info(constants.MAINNET_API_URL, skip_ws=True)

        # State akun
        self.initial_balance = initial_balance
        self.balance = initial_balance  # Available balance
        self.positions: dict[str, PaperPosition] = {}  # coin -> position
        self.trade_history: list[dict] = []
        self.order_id_counter = 1000

        # File untuk persistence
        self.state_file = Path(__file__).parent / "paper_state.json"
        self.history_file = Path(__file__).parent / "paper_history.json"

        # Load state jika ada
        self._load_state()

        print(f"📝 Paper Trading Mode")
        print(f"   Modal: ${self.initial_balance:,.2f}")
        print(f"   Balance: ${self.balance:,.2f}")

    # ── Harga Real-time ─────────────────────────────────────────

    def get_price(self, coin: str) -> float:
        """Ambil harga real-time dari Hyperliquid."""
        mids = self.info.all_mids()
        price = float(mids.get(coin, 0))
        if price == 0:
            raise ValueError(f"❌ Koin '{coin}' tidak ditemukan di Hyperliquid")
        return price

    def get_all_prices(self) -> dict:
        """Ambil semua harga."""
        return {k: float(v) for k, v in self.info.all_mids().items()}

    # ── Market Order (Simulasi) ─────────────────────────────────

    def market_open(
        self, coin: str, is_buy: bool, size: float,
        leverage: int = 1, slippage_pct: float = 0.05
    ) -> dict:
        """
        Buka posisi market (simulasi).

        Args:
            coin: Nama koin
            is_buy: True = Long, False = Short
            size: Ukuran posisi (dalam unit koin)
            leverage: Leverage (default 1x)
            slippage_pct: Simulasi slippage (default 0.05%)
        """
        price = self.get_price(coin)

        # Simulasi slippage
        if is_buy:
            fill_price = price * (1 + slippage_pct / 100)
        else:
            fill_price = price * (1 - slippage_pct / 100)

        # Cek apakah sudah ada posisi di koin ini
        if coin in self.positions:
            existing = self.positions[coin]
            if existing.is_buy == is_buy:
                # Tambah posisi (average)
                total_size = existing.size + size
                avg_price = (
                    (existing.entry_price * existing.size) + (fill_price * size)
                ) / total_size
                existing.size = total_size
                existing.entry_price = avg_price
                self.balance -= (size * fill_price) / leverage
            else:
                # Kurangi atau balik posisi
                if size >= existing.size:
                    # Close + buka sisa
                    pnl = existing.unrealized_pnl(fill_price)
                    self.balance += pnl + existing.margin_used
                    remaining = size - existing.size
                    if remaining > 0:
                        new_margin = (remaining * fill_price) / leverage
                        self.balance -= new_margin
                        self.positions[coin] = PaperPosition(
                            coin, is_buy, remaining, fill_price, leverage
                        )
                    else:
                        del self.positions[coin]
                else:
                    # Partial close
                    pnl_per_unit = existing.unrealized_pnl(fill_price) / existing.size
                    self.balance += pnl_per_unit * size
                    existing.size -= size
                    existing.margin_used = (existing.size * existing.entry_price) / existing.leverage
        else:
            # Posisi baru
            margin_required = (size * fill_price) / leverage
            if margin_required > self.balance:
                return {
                    "status": "error",
                    "error": f"Insufficient balance. Need ${margin_required:,.2f}, have ${self.balance:,.2f}"
                }
            self.balance -= margin_required
            self.positions[coin] = PaperPosition(coin, is_buy, size, fill_price, leverage)

        # Generate order ID
        oid = self.order_id_counter
        self.order_id_counter += 1

        # Catat trade
        trade_record = {
            "oid": oid,
            "type": "market_open",
            "coin": coin,
            "side": "BUY" if is_buy else "SELL",
            "size": size,
            "fill_price": fill_price,
            "leverage": leverage,
            "timestamp": datetime.now().isoformat(),
            "balance_after": self.balance,
        }
        self.trade_history.append(trade_record)
        self._save_state()

        return {
            "status": "ok",
            "response": {
                "data": {
                    "statuses": [{
                        "filled": {
                            "oid": oid,
                            "totalSz": str(size),
                            "avgPx": str(round(fill_price, 6)),
                        }
                    }]
                }
            }
        }

    def market_close(self, coin: str, size: Optional[float] = None) -> dict:
        """
        Tutup posisi (simulasi).

        Args:
            coin: Nama koin
            size: Ukuran yang ditutup (None = tutup semua)
        """
        if coin not in self.positions:
            return {"status": "error", "error": f"No open position for {coin}"}

        pos = self.positions[coin]
        price = self.get_price(coin)

        # Slippage saat close
        slippage_pct = 0.05
        if pos.is_buy:
            fill_price = price * (1 - slippage_pct / 100)
        else:
            fill_price = price * (1 + slippage_pct / 100)

        close_size = size if size else pos.size
        close_size = min(close_size, pos.size)

        # Hitung PnL
        if pos.is_buy:
            pnl = (fill_price - pos.entry_price) * close_size
        else:
            pnl = (pos.entry_price - fill_price) * close_size

        # Kembalikan margin + PnL
        margin_release = (close_size * pos.entry_price) / pos.leverage
        self.balance += margin_release + pnl

        # Update posisi
        if close_size >= pos.size:
            del self.positions[coin]
        else:
            pos.size -= close_size
            pos.margin_used = (pos.size * pos.entry_price) / pos.leverage

        oid = self.order_id_counter
        self.order_id_counter += 1

        trade_record = {
            "oid": oid,
            "type": "market_close",
            "coin": coin,
            "size": close_size,
            "fill_price": fill_price,
            "pnl": pnl,
            "timestamp": datetime.now().isoformat(),
            "balance_after": self.balance,
        }
        self.trade_history.append(trade_record)
        self._save_state()

        return {
            "status": "ok",
            "response": {
                "data": {
                    "statuses": [{
                        "filled": {
                            "oid": oid,
                            "totalSz": str(close_size),
                            "avgPx": str(round(fill_price, 6)),
                        }
                    }]
                }
            }
        }

    # ── Limit Order (Simulasi Sederhana) ────────────────────────

    def limit_order(
        self, coin: str, is_buy: bool, size: float, price: float
    ) -> dict:
        """Pasang limit order (simulasi - langsung resting)."""
        oid = self.order_id_counter
        self.order_id_counter += 1

        margin_required = (size * price) / 1  # Default leverage 1
        if margin_required > self.balance:
            return {"status": "error", "error": f"Insufficient balance"}

        self.balance -= margin_required

        # Simpan sebagai pending order
        if not hasattr(self, 'pending_orders'):
            self.pending_orders = []
        self.pending_orders.append({
            "oid": oid, "coin": coin, "is_buy": is_buy,
            "size": size, "price": price,
        })

        self._save_state()
        return {
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}}
        }

    # ── Info Akun ───────────────────────────────────────────────

    def get_account_value(self) -> float:
        """Total nilai akun (balance + unrealized PnL)."""
        total = self.balance
        for coin, pos in list(self.positions.items()):
            price = self.get_price(coin)
            total += pos.unrealized_pnl(price) + pos.margin_used
        return total

    def get_unrealized_pnl(self) -> float:
        """Total unrealized PnL."""
        total = 0
        for coin, pos in list(self.positions.items()):
            price = self.get_price(coin)
            total += pos.unrealized_pnl(price)
        return total

    def get_realized_pnl(self) -> float:
        """Total realized PnL dari history."""
        return sum(t.get("pnl", 0) for t in self.trade_history if t["type"] == "market_close")

    def get_positions_summary(self) -> list[dict]:
        """Ringkasan semua posisi terbuka."""
        result = []
        for coin, pos in list(self.positions.items()):
            price = self.get_price(coin)
            pnl = pos.unrealized_pnl(price)
            pnl_pct = pos.pnl_pct(price)
            result.append({
                "coin": coin,
                "side": pos.side,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "current_price": price,
                "leverage": pos.leverage,
                "unrealized_pnl": pnl,
                "pnl_pct": pnl_pct,
                "margin_used": pos.margin_used,
            })
        return result

    def print_account_summary(self):
        """Cetak ringkasan akun paper trading."""
        account_value = self.get_account_value()
        unrealized = self.get_unrealized_pnl()
        realized = self.get_realized_pnl()
        roi = ((account_value - self.initial_balance) / self.initial_balance) * 100

        print()
        print("═" * 60)
        print("  📝 PAPER TRADING - RINGKASAN AKUN")
        print("═" * 60)
        print(f"  💰 Modal Awal:      ${self.initial_balance:>12,.2f}")
        print(f"  📊 Nilai Akun:     ${account_value:>12,.2f}")
        print(f"  💵 Available:       ${self.balance:>12,.2f}")
        print(f"  📈 Unrealized PnL: ${unrealized:>+12,.2f}")
        print(f"  ✅ Realized PnL:   ${realized:>+12,.2f}")
        print(f"  📊 ROI:            {roi:>+11.2f}%")
        print("═" * 60)

        positions = self.get_positions_summary()
        if positions:
            print("\n  📌 POSISI TERBUKA:")
            print("  " + "─" * 56)
            for p in positions:
                emoji = "🟢" if p["unrealized_pnl"] >= 0 else "🔴"
                print(
                    f"  {emoji} {p['side']:>5} {p['coin']}: "
                    f"{p['size']:.6f} @ ${p['entry_price']:,.2f} "
                    f"| PnL: ${p['unrealized_pnl']:+,.4f} ({p['pnl_pct']:+.2f}%)"
                )
            print("  " + "─" * 56)
        else:
            print("\n  📭 Tidak ada posisi terbuka.")

        total_trades = len([t for t in self.trade_history if t["type"] == "market_close"])
        print(f"\n  📜 Total trades selesai: {total_trades}")
        print()

    def print_trade_history(self, last_n: int = 10):
        """Cetak history trading."""
        recent = self.trade_history[-last_n:]
        if not recent:
            print("  📭 Belum ada trade history.")
            return

        print(f"\n  📜 TRADE HISTORY (last {len(recent)}):")
        print("  " + "─" * 70)
        for t in recent:
            ts = t["timestamp"][:19]
            pnl_str = f" | PnL: ${t['pnl']:+,.4f}" if "pnl" in t else ""
            side_str = t.get("side", "CLOSE")
            print(
                f"  [{ts}] {t['type']:>12} | {side_str:>5} "
                f"{t['size']:.6f} {t['coin']} @ ${t['fill_price']:,.2f}"
                f"{pnl_str} | Bal: ${t['balance_after']:,.2f}"
            )
        print("  " + "─" * 70)
        print()

    # ── Persistence ─────────────────────────────────────────────

    def _save_state(self):
        """Simpan state ke file."""
        state = {
            "initial_balance": self.initial_balance,
            "balance": self.balance,
            "order_id_counter": self.order_id_counter,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "last_saved": datetime.now().isoformat(),
        }
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

        with open(self.history_file, "w") as f:
            json.dump(self.trade_history, f, indent=2)

    def _load_state(self):
        """Load state dari file jika ada."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
                self.balance = state.get("balance", self.initial_balance)
                self.order_id_counter = state.get("order_id_counter", 1000)
                self.initial_balance = state.get("initial_balance", self.initial_balance)

                # Reconstruct positions
                for coin, pos_data in state.get("positions", {}).items():
                    pos = PaperPosition(
                        pos_data["coin"],
                        pos_data["side"] == "LONG",
                        pos_data["size"],
                        pos_data["entry_price"],
                        pos_data.get("leverage", 1),
                    )
                    self.positions[coin] = pos

                print("  📂 Loaded previous paper trading state.")
            except Exception as e:
                print(f"  ⚠️ Could not load state: {e}")

        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    self.trade_history = json.load(f)
            except Exception:
                self.trade_history = []

    def reset(self):
        """Reset paper trading ke awal."""
        self.balance = self.initial_balance
        self.positions.clear()
        self.trade_history.clear()
        self.order_id_counter = 1000
        if self.state_file.exists():
            self.state_file.unlink()
        if self.history_file.exists():
            self.history_file.unlink()
        print(f"🔄 Paper trading di-reset ke ${self.initial_balance:,.2f}")
