# Cek Dulu: WhatsApp Scam & Hoax Shield

> Forward any suspicious WhatsApp message to Cek Dulu and get a plain Bahasa Indonesia verdict, the reason, and what to do next. If it's dangerous, your kid gets alerted too.

Built for the Meta Global AI Developer Hackathon (Indie track). See [PLAN.md](PLAN.md) for the full design.

## The problem

Older Indonesian parents get fake courier APKs, "Ma, ini nomor baru aku" requests for pulsa, and fake bank CS chats every week. They're often unsure, too embarrassed to ask, and sometimes they just pay. Cek Dulu works inside the app they already use: they **forward** the message. There's no app to install and no account to create.

### Why it matters (sources in [scam_research.md](scam_research.md))

- **702,342 scam reports** to OJK's Indonesia Anti-Scam Centre (IASC) between its launch on 22 Nov 2024 and 30 Sep 2026. That's a cumulative total, not a yearly one. 702,833 accounts were blocked, and only Rp207 M has been returned so far ([Kontan, 5 Oct 2026](https://keuangan.kontan.co.id/news/iasc-terima-702342-laporan-kasus-penipuan-hingga-september-2026)).
- **Speed is everything:** about 80% of victims report more than 12 hours after being scammed, while the money moves through 4 layers of accounts within minutes ([Detik, 29 Jun 2026](https://finance.detik.com/moneter/d-8552960/ojk-bongkar-modus-tipu-tipu-nasabah-hingga-bobol-rekening)). Cek Dulu answers *before* the transfer and alerts family right away.
- **Fake calls / impersonation cause the biggest losses** (Rp2.09 T to Jul 2026). Komdigi says many elderly people fall for AI-cloned voices of officials and family ([Kompas, 2 Jul 2026](https://nasional.kompas.com/read/2026/07/02/20330831/komdigi-sebut-banyak-lansia-jadi-korban-scam-ai-yang-tiru-suara-pejabat)). Cek Dulu doesn't claim to detect deepfakes. It checks *what is being asked*: urgency, a transfer to a personal account, an OTP, a "new number".
- Polda Metro Jaya warned about fake police video calls ("OTT kurir narkoba") in Oct 2025. Forward the recording or a voice note, and Cek Dulu flags it as fake police and says to hang up and call 110.

Coverage of every research item: [RESEARCH_COVERAGE.md](RESEARCH_COVERAGE.md).

## How it works

```
WhatsApp / Telegram ──webhook──▶  FastAPI (/webhook)  ──Input──▶  Agent  ──▶  Muse Spark (tools + JSON verdict)
   ▲                         │                            │
   └──── reply / guardian ◀──┘                            ├─ transcribe_audio → Muse Voice Transcribe
         alert template                                   ├─ describe_image   → Muse Spark (image input)
                                                          ├─ decode_qris      → OpenCV + EMVCo TLV + CRC16
                                                          ├─ check_link       → redirect expand + reputation
                                                          └─ safe_word_check  → family safe-word hint
```

1. The webhook checks `X-Hub-Signature-256`, dedupes by message id, returns 200 right away and processes the message in the background.
2. Voice notes **and videos** (e.g. a recorded "police" video call) are converted with ffmpeg and transcribed. Images are run through a QRIS decoder, then go straight into the Muse Spark verdict call (single call: vision + judgement). Account and phone numbers in the text are spotted so the bot can point to cekrekening.id / aduannomor.id.
3. Muse Spark gets the content wrapped as **data** (prompt-injection guard), calls `check_link` / `safe_word_check` when it needs them, and returns a verdict JSON (`AMAN` / `WASPADA` / `BAHAYA`).
4. The verdict is validated against a schema (repaired once, otherwise it falls back to WASPADA). Safety floors then apply: never "100% aman", a low-confidence AMAN becomes WASPADA, and an APK attachment is always BAHAYA.
5. The reply goes back to the parent. On **BAHAYA**, every linked guardian gets the `guardian_alert` utility template, or a free-form text if the template isn't approved yet.

## Channels

The agent core is channel-agnostic. There are two chat adapters plus the web test page:

| | WhatsApp | Telegram |
|---|---|---|
| Endpoint | `/webhook` | `/telegram/webhook` |
| Auth | `X-Hub-Signature-256` (app secret) | `X-Telegram-Bot-Api-Secret-Token` |
| Guardian linking | `daftar wali 08xx` (by phone number) | `/daftar_wali` → parent sends the deep link to their child, who taps it |
| Guardian alert | Utility template `guardian_alert` | Normal bot message (no template needed) |
| Local dev | Needs a public URL | `python -m app.channels.telegram_bot poll` (no URL needed) |

### Telegram quick start

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Add `META_API_KEY` to `.env`.
3. `.venv/bin/python -m app.channels.telegram_bot poll`, then forward a scam message to your bot.

In production, set `TELEGRAM_WEBHOOK_URL=https://<service>.onrender.com/telegram/webhook` and a random `TELEGRAM_WEBHOOK_SECRET`; the webhook is registered on startup. Polling and the webhook can't run at the same time; `poll` removes the webhook, so restart the Render service (or run `set-webhook`) afterwards.

Telegram commands: `/daftar_wali`, `/kata_aman <petunjuk>`, `/status`, `/hapus_wali`, `/bantuan`. The plain-text WhatsApp phrases work too.

### WhatsApp commands (parent)

| Command | What it does |
|---|---|
| *(forward anything)* | Check the message, photo, or voice note |
| `daftar wali 08xxxxxxxxxx` | Link a guardian (child / relative) |
| `kata aman <petunjuk>` | Save a **hint** for the family safe word (the word itself is never stored) |
| `hapus wali` | Remove guardians |
| `status` | Show linked guardians |
| `bantuan` | Help |

## Meta tools used

| Meta tool | Used for |
|---|---|
| **Muse Spark** (`muse-spark-1.3`) | The agent brain: tool calling + structured verdict JSON |
| **Muse vision** (Muse Spark image input) | Reading screenshots, transfer proofs and QRIS photos |
| **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | Forwarded voice notes → text |
| **WhatsApp Cloud API** | Webhook, replies, the `guardian_alert` utility template |
| **Muse Code** | Coding agent used to build the project |

## Setup

```bash
uv venv .venv --python 3.11 && uv pip install -p .venv -r requirements.txt   # or: python -m venv .venv && pip install -r requirements.txt
cp .env.example .env    # fill in values locally; never commit .env
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 for the test page, or:

```bash
curl -s localhost:8000/analyze -H 'content-type: application/json' \
  -d '{"type":"text","text":"Halo, CS BCA. Rekening Anda diblokir, kirim kode OTP."}' | jq .verdict
```

`/analyze` accepts `{"type": "text|image|audio|document", "text": "...", "media_base64": "...", "mime": "..."}`.

### Tests

```bash
.venv/bin/python -m pytest -q                                   # offline: QRIS, links, agent loop (fake model), WA + Telegram webhooks, guardian flow
RUN_LIVE=1 META_API_KEY=... .venv/bin/python -m pytest -k live -s  # test matrix in tests/cases.yaml against Muse Spark
```

### Deploy (Railway)

1. Push to GitHub. In Railway: **New Project → Deploy from GitHub repo**. `railway.json` sets the start command and health check, and `.python-version` pins Python 3.11.
2. **+ New → Database → PostgreSQL** in the same project. On the app service, add the variable `DATABASE_URL=${{Postgres.DATABASE_URL}}`. Tables are created on startup, and new columns are added automatically.
3. Add the secret variables (names below). Then **Settings → Networking → Generate Domain**.
4. Telegram: set `TELEGRAM_WEBHOOK_URL=https://<app>.up.railway.app/telegram/webhook` and a random `TELEGRAM_WEBHOOK_SECRET`; the webhook is registered on startup.
5. WhatsApp: in the Meta app → WhatsApp → Configuration, set the callback URL to `https://<app>.up.railway.app/webhook` with your `WA_VERIFY_TOKEN`, then subscribe to `messages`.

ffmpeg comes bundled via `imageio-ffmpeg`, so no system package is needed. (`render.yaml` is kept as an alternative.)

### Guardian alert template

Submit in WhatsApp Manager, category **Utility**, language `id`, name `guardian_alert`:

> Halo, {{1}} baru saja menerima pesan yang terdeteksi BAHAYA ({{2}}). Mohon hubungi beliau sebelum ada uang yang ditransfer. - Cek Dulu

`{{1}}` = parent name + masked number, `{{2}}` = scam category.

### Environment variables (names only)

`META_API_KEY`, `META_API_BASE_URL`, `MUSE_MODEL`, `MUSE_REASONING_EFFORT`, `ASR_MODEL`, `LLM_PROVIDER` (`meta` | `fireworks`), `FIREWORKS_API_KEY`, `FIREWORKS_BASE_URL`, `FIREWORKS_MODEL`, `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_WABA_ID`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `WA_GRAPH_VERSION`, `WA_GUARDIAN_TEMPLATE`, `WA_TEMPLATE_LANG`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL`, `DATABASE_URL`, `GOOGLE_SAFE_BROWSING_KEY` (optional).

## Privacy & safety

- Media is analyzed in memory and never stored; only the extracted text and the verdict are kept.
- The first message includes a consent line.
- Forwarded content is treated as data, never as instructions, and the output is schema-validated.
- `check_link` follows redirects hop by hop and refuses private/loopback addresses (SSRF guard).
- Secrets only live in env vars.

## Roadmap

APK static checks, SAM 3.1, Muse Wonder escalation for low-confidence cases, IASC report filing, pgvector similar-case lookup, Prompt Guard, more regional languages.

*Cek dulu, baru transfer.*
