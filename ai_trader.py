"""
AI Trader (LLM generik)
=======================
Mengirim konteks pasar+akun ke LLM dan menerima keputusan trading
terstruktur (JSON). Bekerja dengan provider APA SAJA yang OpenAI-compatible
(DeepSeek, OpenAI, OpenRouter, Groq, Together, Ollama, dll) — cukup atur
base_url, model, dan api_key di config.

AI hanya MENGUSULKAN. Keputusan akhir & ukuran posisi divalidasi oleh
RiskManager. AI tidak pernah menentukan size.
"""

import json
import urllib.request
import urllib.error

from config import get_llm_config


SYSTEM_PROMPT = """Anda adalah asisten analis trading untuk perpetual futures di Hyperliquid.
Tugas Anda: menganalisis data pasar dan status akun, lalu mengusulkan SATU keputusan trading.

DATA YANG ANDA TERIMA (per koin):
- price: harga saat ini
- timeframes: indikator untuk 2 timeframe (mis. "15m" utama & "1h" konfirmasi tren), masing-masing berisi:
    - trend, sma_short, sma_long, change_pct
    - rsi (+ rsi_state: overbought >=70 / oversold <=30 / neutral)
    - macd {macd, signal, histogram, state: bullish/bearish}
    - atr & atr_pct (volatilitas)
- fibonacci: swing_high/low, levels retracement, nearest_level, price_position_pct (0=di low, 100=di high)
- funding_rate: hourly_pct & annualized_pct (biaya menahan posisi perpetual)

PEDOMAN ANALISIS:
- Konfirmasi arah dengan MULTI-TIMEFRAME: idealnya tren 15m searah dengan 1h.
- RSI: hindari open_long saat overbought, hindari open_short saat oversold.
- MACD: state bullish mendukung long, bearish mendukung short; perhatikan histogram.
- ATR%: pakai untuk menilai volatilitas — set stop_loss_pct minimal sekitar 1-2x ATR% agar tidak kena noise.
- Fibonacci: level retracement bisa jadi area support/resistance; entry dekat level lebih baik.
- Funding rate: jika annualized_pct tinggi & positif, long mahal (bayar funding); pertimbangkan biaya ini.

ATURAN OUTPUT (WAJIB):
- Balas HANYA dengan satu objek JSON valid, tanpa teks lain, tanpa markdown.
- Skema JSON:
{
  "action": "open_long" | "open_short" | "close" | "hold",
  "coin": "<simbol koin dari watched_coins, atau null jika hold>",
  "leverage": <integer 1-5>,
  "take_profit_pct": <angka, mis. 4.0>,
  "stop_loss_pct": <angka, mis. 2.0>,
  "confidence": <angka 0.0 - 1.0>,
  "reasoning": "<alasan singkat, sebut indikator kunci yang dipakai>"
}

PEDOMAN UMUM:
- Anda TIDAK menentukan ukuran posisi (size); itu diatur oleh manajemen risiko.
- Selalu sertakan stop_loss_pct untuk open_long/open_short.
- Gunakan "hold" jika sinyal antar-timeframe/indikator saling bertentangan atau tidak jelas.
- Gunakan "close" jika posisi terbuka sebaiknya ditutup.
- Bersikap konservatif: lebih baik hold daripada memaksa trade berisiko.
- Confidence harus mencerminkan seberapa selaras indikator-indikator tersebut; jangan selalu tinggi.
"""


class LLMTrader:
    def __init__(self, api_key: str = None, settings: dict = None):
        cfg = get_llm_config()
        self.base_url = (cfg["base_url"] or "").rstrip("/")
        self.model = cfg["model"]
        self.use_response_format = cfg["use_response_format"]
        self.max_tokens = cfg.get("max_tokens", 3000)
        self.api_key = api_key or cfg["api_key"]
        if not self.api_key:
            raise ValueError(
                "❌ API key LLM kosong! Isi 'api_key' di config.json "
                "atau set environment variable LLM_API_KEY."
            )

    def decide(self, context: dict) -> dict:
        """
        Kirim konteks ke LLM, kembalikan keputusan terstruktur.

        Returns:
            dict keputusan (lihat skema di SYSTEM_PROMPT).
            Jika gagal/parse error, kembalikan {"action": "hold", ...}.
        """
        user_msg = (
            "Berikut konteks pasar dan akun saat ini (JSON):\n\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
            + "\n\nBerikan satu keputusan trading sesuai skema."
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }
        # response_format JSON tidak didukung semua provider; opsional.
        if self.use_response_format:
            payload["response_format"] = {"type": "json_object"}

        try:
            raw = self._post("/chat/completions", payload)
            content = raw["choices"][0]["message"]["content"]
            decision = self._extract_json(content)
            return self._normalize(decision)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            return self._fallback(f"Koneksi LLM gagal: {e}")
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._fallback(f"Gagal parse respons AI: {e}")

    # ── Internal ──

    def _post(self, path: str, payload: dict) -> dict:
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Ekstrak objek JSON dari respons, toleran terhadap fence/teks tambahan."""
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Cari blok { ... } seimbang pertama
        start = text.find("{")
        if start == -1:
            raise ValueError("Tidak ada objek JSON di respons")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        raise ValueError("JSON tidak lengkap di respons")

    @staticmethod
    def _normalize(decision: dict) -> dict:
        """Pastikan field ada & tipe benar; beri default aman."""
        action = str(decision.get("action", "hold")).lower().strip()
        if action not in {"open_long", "open_short", "close", "hold"}:
            action = "hold"
        return {
            "action": action,
            "coin": (decision.get("coin") or None),
            "leverage": int(decision.get("leverage") or 1),
            "take_profit_pct": decision.get("take_profit_pct"),
            "stop_loss_pct": decision.get("stop_loss_pct"),
            "confidence": float(decision.get("confidence") or 0.0),
            "reasoning": str(decision.get("reasoning", "")),
        }

    @staticmethod
    def _fallback(reason: str) -> dict:
        return {
            "action": "hold",
            "coin": None,
            "leverage": 1,
            "take_profit_pct": None,
            "stop_loss_pct": None,
            "confidence": 0.0,
            "reasoning": f"[FALLBACK] {reason}",
        }
