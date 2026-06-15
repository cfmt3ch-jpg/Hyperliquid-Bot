"""
Hyperliquid Client - Wrapper untuk SDK Hyperliquid
===================================================
Menyediakan koneksi ke Hyperliquid API (testnet & mainnet).
"""

import json
from typing import Optional
from pathlib import Path

import eth_account
from eth_account.signers.local import LocalAccount

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from config import load_config, TRADING_SETTINGS


class HyperliquidClient:
    """Client utama untuk berinteraksi dengan Hyperliquid."""

    def __init__(self):
        config = load_config()
        self.address = config["account_address"]

        # Pilih testnet atau mainnet
        if TRADING_SETTINGS["use_testnet"]:
            self.base_url = constants.TESTNET_API_URL
            print("🟡 Menggunakan TESTNET (uang virtual)")
        else:
            self.base_url = constants.MAINNET_API_URL
            print("🔴 Menggunakan MAINNET (uang ASLI!)")
            # Konfirmasi keamanan sebelum trading dengan uang asli
            confirm = input(
                "⚠️  Anda akan trading dengan UANG ASLI di MAINNET.\n"
                "    Ketik 'MAINNET' untuk lanjut, atau ENTER untuk batal: "
            ).strip()
            if confirm != "MAINNET":
                raise SystemExit("⛔ Dibatalkan. Set use_testnet=True untuk berlatih dengan aman.")

        # Setup akun: dukung private key langsung ATAU keystore terenkripsi
        secret_key = config.get("secret_key", "")
        keystore_path = config.get("keystore_path", "")

        if keystore_path:
            secret_key = self._load_key_from_keystore(keystore_path)
        if not secret_key:
            raise ValueError("❌ secret_key kosong di config.json (atau isi keystore_path)!")

        account: LocalAccount = eth_account.Account.from_key(secret_key)
        if not self.address:
            self.address = account.address

        print(f"📍 Account: {self.address}")
        if self.address != account.address:
            print(f"🔑 Agent:   {account.address}")

        # Inisialisasi Info & Exchange
        self.info = Info(self.base_url, skip_ws=True)
        self.exchange = Exchange(
            account, self.base_url, account_address=self.address
        )

    @staticmethod
    def _load_key_from_keystore(keystore_path: str) -> str:
        """Decrypt private key dari file keystore (format Ethereum V3)."""
        import getpass

        path = Path(keystore_path)
        if not path.exists():
            raise FileNotFoundError(f"❌ Keystore tidak ditemukan: {keystore_path}")

        with open(path) as f:
            encrypted = json.load(f)

        password = getpass.getpass("🔐 Password keystore: ")
        try:
            private_key = eth_account.Account.decrypt(encrypted, password)
        except ValueError:
            raise ValueError("❌ Password keystore salah!")
        return private_key.hex()

    # ── Info / Market Data ──────────────────────────────────────

    def get_user_state(self) -> dict:
        """Ambil status akun (balance, posisi, dll)."""
        return self.info.user_state(self.address)

    def get_positions(self) -> list:
        """Ambil daftar posisi yang sedang terbuka."""
        user_state = self.get_user_state()
        positions = []
        for pos in user_state.get("assetPositions", []):
            positions.append(pos["position"])
        return positions

    def get_account_value(self) -> float:
        """Ambil total nilai akun dalam USD."""
        user_state = self.get_user_state()
        return float(user_state["marginSummary"]["accountValue"])

    def get_all_mids(self) -> dict:
        """Ambil harga mid untuk semua koin."""
        return self.info.all_mids()

    def get_price(self, coin: str) -> float:
        """Ambil harga mid untuk koin tertentu."""
        mids = self.get_all_mids()
        return float(mids.get(coin, 0))

    def get_l2_snapshot(self, coin: str) -> dict:
        """Ambil order book L2 untuk koin."""
        return self.info.l2_snapshot(coin)

    # ── Trading Operations ──────────────────────────────────────

    def market_open(
        self, coin: str, is_buy: bool, size: float,
        slippage: float = 0.01
    ) -> dict:
        """
        Buka posisi MARKET order.

        Args:
            coin: Nama koin (e.g. "ETH", "BTC")
            is_buy: True = Long, False = Short
            size: Ukuran posisi (dalam unit koin)
            slippage: Toleransi slippage (default 1%)
        """
        side = "BUY" if is_buy else "SELL"
        print(f"📊 Market {side} {size} {coin}...")
        result = self.exchange.market_open(coin, is_buy, size, None, slippage)
        self._print_order_result(result)
        return result

    def market_close(self, coin: str, size: Optional[float] = None) -> dict:
        """
        Tutup posisi (market close).

        Args:
            coin: Nama koin
            size: Ukuran yang ditutup (None = tutup semua)
        """
        print(f"🔒 Closing {coin} position...")
        if size:
            # Untuk partial close, gunakan market_open dengan arah berlawanan
            positions = self.get_positions()
            for pos in positions:
                if pos["coin"] == coin:
                    current_size = float(pos["szi"])
                    is_buy = current_size < 0  # Balik arah
                    result = self.exchange.market_open(
                        coin, is_buy, abs(size), None, 0.01
                    )
                    self._print_order_result(result)
                    return result
        else:
            result = self.exchange.market_close(coin)
            self._print_order_result(result)
            return result

    def limit_order(
        self, coin: str, is_buy: bool, size: float, price: float,
        tif: str = "Gtc"
    ) -> dict:
        """
        Pasang LIMIT order.

        Args:
            coin: Nama koin
            is_buy: True = Buy, False = Sell
            size: Ukuran posisi
            price: Harga limit
            tif: Time-in-force: "Gtc" (Good Till Cancel), "Ioc" (Immediate or Cancel), "Alo" (Add Liquidity Only)
        """
        side = "BUY" if is_buy else "SELL"
        print(f"📋 Limit {side} {size} {coin} @ ${price:,.2f} (TIF: {tif})")
        order_type = {"limit": {"tif": tif}}
        result = self.exchange.order(coin, is_buy, size, price, order_type)
        self._print_order_result(result)
        return result

    def cancel_order(self, coin: str, oid: int) -> dict:
        """Batalkan order berdasarkan order ID."""
        print(f"❌ Cancel order #{oid} untuk {coin}")
        return self.exchange.cancel(coin, oid)

    def cancel_all_orders(self, coin: str) -> dict:
        """Batalkan semua order untuk koin tertentu."""
        open_orders = self.info.open_orders(self.address)
        cancelled = []
        for order in open_orders:
            if order["coin"] == coin:
                result = self.exchange.cancel(coin, order["oid"])
                cancelled.append(result)
        print(f"❌ Dibatalkan {len(cancelled)} order untuk {coin}")
        return cancelled

    # ── TP/SL (Take Profit / Stop Loss) ─────────────────────────

    def set_tp_sl(
        self, coin: str, is_buy: bool, size: float,
        take_profit: float, stop_loss: float
    ) -> dict:
        """
        Pasang Take Profit dan Stop Loss.

        Args:
            coin: Nama koin
            is_buy: True = Long, False = Short
            size: Ukuran posisi
            take_profit: Harga take profit
            stop_loss: Harga stop loss
        """
        # Take Profit
        tp_side = not is_buy  # TP adalah arah berlawanan
        tp_result = self.exchange.order(
            coin, tp_side, size, take_profit,
            {"trigger": {"triggerPx": take_profit, "isMarket": True, "tpsl": "tp"}}
        )
        print(f"✅ Take Profit @ ${take_profit:,.2f}")

        # Stop Loss
        sl_result = self.exchange.order(
            coin, tp_side, size, stop_loss,
            {"trigger": {"triggerPx": stop_loss, "isMarket": True, "tpsl": "sl"}}
        )
        print(f"✅ Stop Loss @ ${stop_loss:,.2f}")

        return {"tp": tp_result, "sl": sl_result}

    # ── Leverage ────────────────────────────────────────────────

    def set_leverage(self, leverage: int, coin: str, is_cross: bool = True) -> dict:
        """
        Set leverage untuk koin tertentu.

        Args:
            leverage: Leverage (1-50)
            coin: Nama koin
            is_cross: True = Cross margin, False = Isolated margin
        """
        mode = "cross" if is_cross else "isolated"
        print(f"⚙️ Setting {coin} leverage: {leverage}x ({mode})")
        return self.exchange.update_leverage(leverage, coin, is_cross)

    # ── Helper ──────────────────────────────────────────────────

    def print_account_summary(self):
        """Cetak ringkasan akun."""
        user_state = self.get_user_state()
        margin = user_state["marginSummary"]

        print("\n" + "=" * 50)
        print("📊 RINGKASAN AKUN HYPERLIQUID")
        print("=" * 50)
        print(f"  Nilai Akun:   ${float(margin['accountValue']):>12,.2f}")
        print(f"  Margin Used:  ${float(margin['totalMarginUsed']):>12,.2f}")
        print(f"  PnL:          ${float(margin['totalNtlPos']):>12,.2f}")
        print("=" * 50)

        positions = self.get_positions()
        if positions:
            print("\n📌 POSISI TERBUKA:")
            for pos in positions:
                coin = pos["coin"]
                size = float(pos["szi"])
                entry = float(pos["entryPx"])
                side = "LONG" if size > 0 else "SHORT"
                pnl = float(pos.get("unrealizedPnl", 0))
                print(f"  {side:>5} {coin}: {abs(size)} @ ${entry:,.2f} | PnL: ${pnl:,.2f}")
        else:
            print("\n  Tidak ada posisi terbuka.")
        print()

    def _print_order_result(self, result: dict):
        """Cetak hasil order."""
        if result.get("status") == "ok":
            for status in result["response"]["data"]["statuses"]:
                if "filled" in status:
                    f = status["filled"]
                    print(f"  ✅ Filled: {f['totalSz']} @ ${float(f['avgPx']):,.2f} (oid: {f['oid']})")
                elif "resting" in status:
                    print(f"  ⏳ Resting: oid={status['resting']['oid']}")
                elif "error" in status:
                    print(f"  ❌ Error: {status['error']}")
        else:
            print(f"  ❌ Gagal: {result}")
