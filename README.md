# 🧠 AI Trading Bot — Hyperliquid (DeepSeek)
### Dibuat oleh AIsha untuk Master Caesar

Bot trading **AI-driven** untuk **Hyperliquid DEX** (perpetual futures).
AI (DeepSeek) yang memutuskan trade secara dinamis — bukan strategi tetap.
Setiap keputusan AI divalidasi oleh **Risk Manager** sebelum dieksekusi.

---

## ⚠️ PERINGATAN PENTING

```
Trading crypto sangat berisiko tinggi!
AI bisa salah. Anda bisa kehilangan SELURUH modal Anda.
Saat ini bot berjalan dalam mode PAPER TRADING (simulasi, uang virtual).
Jangan pindah ke uang asli sebelum yakin dengan perilaku AI.
```

---

## 🧩 Cara Kerja (Alur AI-Driven)

```
Data pasar + status akun  →  AI (DeepSeek) mengusulkan keputusan
                          →  Risk Manager memvalidasi (guardrail keras)
                          →  Engine mengeksekusi (simulasi paper trading)
                          →  TP/SL dikelola otomatis tiap siklus
```

AI hanya **mengusulkan arah** (long / short / close / hold + TP/SL + confidence).
AI **tidak pernah** menentukan ukuran posisi — itu dihitung Risk Manager.

---

## 📋 Persiapan

### 1. Install Dependencies
```bash
python -m pip install -r requirements.txt
```

### 2. Konfigurasi API Key DeepSeek
Salin template lalu isi:
```bash
copy config.json.example config.json
```
Edit `config.json`:
```json
{
    "account_address": "",
    "secret_key": "",
    "keystore_path": "",
    "deepseek_api_key": "sk-...kunci_deepseek_anda..."
}
```
Untuk **paper trading**, cukup isi `deepseek_api_key`. Field wallet boleh kosong.

Alternatif (lebih aman) — environment variable:
```powershell
setx DEEPSEEK_API_KEY "sk-...kunci_anda..."
```

---

## 🚀 Cara Menjalankan

### Dashboard Web (Direkomendasikan)
```bash
python dashboard.py
```
Buka browser: **http://127.0.0.1:5000**

Dashboard menyediakan:
- ▶ Start / ⏹ Stop loop AI + ⚡ Run Once (satu siklus manual)
- Keputusan AI terbaru (action, confidence, reasoning) + verdict risiko
- Panel guardrail risiko (PnL harian vs batas, cap leverage/size/posisi)
- Riwayat keputusan AI, posisi, history trade, harga real-time
- 🚨 Kontrol darurat: Close All & Reset

### CLI (tanpa browser)
```bash
python ai_loop.py            # loop otomatis
python ai_loop.py --once     # satu siklus keputusan saja (uji)
```

> ⚠️ Jangan menjalankan `dashboard.py` dan `ai_loop.py` bersamaan — keduanya
> memakai file state yang sama dan akan saling menimpa.

---

## ⚖️ Guardrail Risiko

Diatur di `AI_SETTINGS` dalam `config.py`:

| Parameter | Default | Penjelasan |
|-----------|---------|------------|
| `max_leverage` | 5x | Leverage maksimal (usulan AI di-clamp ke sini) |
| `max_position_pct` | 1% | Margin maksimal per trade (% nilai akun) |
| `max_daily_loss_pct` | 5% | Bot berhenti trading bila loss harian tembus ini |
| `min_confidence` | 0.6 | Confidence minimal AI agar trade dieksekusi |
| `max_open_positions` | 3 | Maksimal posisi terbuka bersamaan |
| `require_stop_loss` | True | Stop loss wajib pada setiap posisi |
| `coins` | BTC, ETH, SOL | Koin yang dipantau AI |
| `loop_interval_seconds` | 300 | Jeda antar siklus keputusan |

---

## 📁 Struktur File

```
Hyperliquid-Bot/
├── config.json.example   ← Template config
├── config.json           ← Config Anda (JANGAN share!)
├── config.py             ← Pengaturan + guardrail AI (AI_SETTINGS)
├── ai_context.py         ← Kumpulkan data pasar+akun untuk AI
├── ai_trader.py          ← Klien DeepSeek (keputusan JSON terstruktur)
├── risk_manager.py       ← Validasi keras keputusan AI + hitung size
├── ai_loop.py            ← Orkestrasi loop AI (CLI)
├── dashboard.py          ← Dashboard web AI-driven (Flask)
├── paper_trading.py      ← Engine simulasi (uang virtual, harga real)
├── hyperliquid_client.py ← Wrapper API Hyperliquid (untuk fase live nanti)
├── requirements.txt      ← Dependencies
└── README.md             ← File ini
```

---

## 🗺️ Roadmap Fase

- **Fase 1 (sekarang):** AI trading di **paper trading** (simulasi). Tanpa risiko.
- **Fase 2:** Sambungkan loop yang sama ke `HyperliquidClient` di **testnet**.
- **Fase 3:** Mainnet dengan ukuran sangat kecil, hanya bila puas dengan hasil sebelumnya.

---

## 🔒 Keamanan

1. **JANGAN** share `config.json` atau API key Anda.
2. Dashboard hanya bind ke `127.0.0.1` (lokal). Jangan ekspos ke internet.
3. Mulai selalu dari **paper trading**.
4. Pantau perilaku AI sebelum menaikkan modal/fase.

---

## 📚 Referensi

- [Hyperliquid Docs](https://hyperliquid.gitbook.io/hyperliquid-docs)
- [DeepSeek API Docs](https://api-docs.deepseek.com)
- [Hyperliquid App](https://app.hyperliquid.xyz)

---

*Dibuat dengan ❤️ oleh AIsha untuk Master Caesar*
