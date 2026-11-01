# Cek Dulu: Scam & Hoax Checker for Indonesian Parents (Telegram + WhatsApp)

> Forward any suspicious message, screenshot, QRIS sticker, voice note or video call recording to the Cek Dulu bot and get a plain Bahasa Indonesia verdict, the reason, and what to do next. If it's dangerous, your kid gets alerted too.

**Try it:** the live demo runs on **Telegram**. The WhatsApp Cloud API adapter is fully built (webhook, signature check, alert template) and covered by tests, using the same agent core.

Built for the Meta Global AI Developer Hackathon (Indie track). See [PLAN.md](PLAN.md) for the full design.

## The problem

Older Indonesian parents get fake courier APKs, "Ma, ini nomor baru aku" requests for pulsa, and fake bank CS chats every week. They're often unsure, too embarrassed to ask, and sometimes they just pay. Cek Dulu works inside a chat app they already use: they **forward** the message. There's no app to install and no account to create.

### Why it matters (sources in [scam_research.md](scam_research.md))

- **702,342 scam reports** to OJK's Indonesia Anti-Scam Centre (IASC) between its launch on 22 Nov 2024 and 30 Sep 2026. That's a cumulative total, not a yearly one. 702,833 accounts were blocked, and only Rp207 M has been returned so far ([Kontan, 5 Oct 2026](https://keuangan.kontan.co.id/news/iasc-terima-702342-laporan-kasus-penipuan-hingga-september-2026)).
- **Speed is everything:** about 80% of victims report more than 12 hours after being scammed, while the money moves through 4 layers of accounts within minutes ([Detik, 29 Jun 2026](https://finance.detik.com/moneter/d-8552960/ojk-bongkar-modus-tipu-tipu-nasabah-hingga-bobol-rekening)). Cek Dulu answers *before* the transfer and alerts family right away.
- **Fake calls / impersonation cause the biggest losses** (Rp2.09 T to Jul 2026). Komdigi says many elderly people fall for AI-cloned voices of officials and family ([Kompas, 2 Jul 2026](https://nasional.kompas.com/read/2026/07/02/20330831/komdigi-sebut-banyak-lansia-jadi-korban-scam-ai-yang-tiru-suara-pejabat)). Cek Dulu doesn't claim to detect deepfakes. It checks *what is being asked*: urgency, a transfer to a personal account, an OTP, a "new number".
- Polda Metro Jaya warned about fake police video calls ("OTT kurir narkoba") in Oct 2025. Forward the recording or a voice note, and Cek Dulu flags it as fake police and says to hang up and call 110.

Coverage of every research item: [RESEARCH_COVERAGE.md](RESEARCH_COVERAGE.md).

## How it works

```
Telegram / WhatsApp ─webhook─▶ FastAPI ─▶ "🔎 Sedang dicek…"
                                  │
                                  ▼
   voice/video ─▶ Muse Voice Transcribe ─┐
   image ───────▶ (sent as image input) ─┼─▶ Muse Spark agent loop ──▶ verdict JSON ──▶ safety rules ──▶ reply
   text ─────────────────────────────────┘     │  tools the model calls:
                                               ├─ check_link       → redirect expand + reputation (+ Safe Browsing)
                                               ├─ decode_qris      → OpenCV + EMVCo TLV + CRC16 (images)
                                               └─ safe_word_check  → family safe-word hint
                                                                         on BAHAYA ──▶ guardian alert
```

1. The webhook checks its secret (`X-Telegram-Bot-Api-Secret-Token` / `X-Hub-Signature-256`), dedupes by message id, returns 200 right away and works in the background. The parent immediately sees **"🔎 Sedang dicek…"**, which Telegram then edits into the verdict.
2. Voice notes **and videos** (e.g. a recorded "police" video call) are converted with ffmpeg and transcribed by Muse Voice first: the model can't judge audio it can't read. Images go straight into the Muse Spark call as image input. Account and phone numbers in the text are spotted and flagged to the model.
3. Muse Spark gets the content wrapped as **data** (prompt-injection guard) and **decides which tools to call**: `check_link` for URLs, `decode_qris` when it sees a QR code, `safe_word_check` for "it's me, your kid, send money". It then returns a verdict JSON (`AMAN` / `WASPADA` / `BAHAYA`).
4. The verdict is validated against a schema (repaired once, otherwise it falls back to WASPADA). Deterministic safety rules then apply: an APK is always BAHAYA (with "already installed? turn off internet, call the bank"), a Safe Browsing hit is BAHAYA, a QRIS with a bad checksum is never AMAN, a low-confidence AMAN becomes WASPADA, never "100% aman", and no "jangan khawatir" on a warning. **Every change is recorded** ("Muse said WASPADA, rule `apk_selalu_bahaya` made it BAHAYA") and shown on the test page.
5. **Official contacts are added by code, not the model.** Muse outputs a `scam_type` (fake_police, impersonation, bank_otp, investment, pinjol, other) and whether money was already sent. The code then appends contacts from the verified list in [`app/agent/contacts.py`](app/agent/contacts.py): 110, IASC, OJK 157, Halo BCA. Any other number in the model's text is removed, so a phone number can never be hallucinated. If the scammer's own number is in the text, screenshot or voice note, the reply gets a step "Laporkan nomor 0857-xxxx-xxxx di aduannomor.id (situs resmi Komdigi)". Numbers from text and voice must really appear in it; the bot never guesses who owns a number or where it's from.
6. On **BAHAYA** the guardian is alerted *first*, and the reply then says exactly what happened: "Sudah kami kabari: Hans", already told a few minutes ago, delivery failed (call them yourself), or "Belum ada wali terdaftar, ketik /daftar_wali".
7. Every reply ends with what actually ran, for example `🔎 Dicek dengan: Muse Voice → Muse Spark → cek link → aturan keamanan`.
8. **It talks like family, not a call centre.** On first use the bot asks one question, "Mau aku panggil apa?" (Mama, Bu Sri, Om Budi), and stores it in `user_profiles` with a casual or polite style. Guardians who link become named contacts with their relation. Muse gets this profile, so the reply reads like a text from your kid. Code also replaces any "Ibu/Bapak" that slips through. Example:
   ```
   🔴 BAHAYA · Ini penipuan, Mama. Jangan diikutin ya.
   Polisi asli nggak periksa lewat telepon dan nggak pernah minta transfer.
   1. Tutup teleponnya sekarang ya.
   2. Jangan transfer apa pun dulu.
   3. Telepon Hans sekarang ya, Mama.
   📞 Polisi: 110
   ✅ Hans udah aku kabarin ya, Mama.
   ```
9. **Number and account checks.**
   - **Phone numbers:** an optional **public lookup** on AduanNomor.id: the same check the site's own "cek nomor" form runs. It is *not* an official Komdigi API or a partnership.
     - 3 s timeout, cached per number, silent on any failure.
     - If the number was reported, code (not the model) writes "Nomor ini sudah dilaporkan 12x di AduanNomor.id (Komdigi)."
     - A real captured response in `app/data/aduannomor_fixtures.json` is the fallback if the server's IP gets blocked. Add one with `python -m app.tools.aduannomor capture 08xx`. `ADUANNOMOR_MODE=live|fixture|off`.
   - **Bank accounts:** no live lookup, because cekrekening.id sits behind a bot check and has no public API. The reply links to https://cekrekening.id/cek-rekening and https://cekrekening.id/laporkan-rekening.
   - **Our own count:** a `reported_numbers` table (digits only) adds "sudah 3x dilaporkan pengguna Cek Dulu". It counts distinct *other* users.
10. If the parent replies "terus gimana?" or "aku sudah transfer" within 15 minutes, Muse answers in the context of that verdict. "Already paid" gets the bank → IASC steps (with the official contacts appended by code) and an urgent guardian alert (no cooldown).

## Channels

The agent core is channel-agnostic. There are two chat adapters plus the web test page:

| | WhatsApp | Telegram |
|---|---|---|
| Endpoint | `/webhook` | `/telegram/webhook` |
| Auth | `X-Hub-Signature-256` (app secret) | `X-Telegram-Bot-Api-Secret-Token` |
| Guardian linking | `daftar wali 08xx anak Hans` (by phone number, relation and name optional) | `/daftar_wali anak` → parent sends the signed deep link to their child, who taps it |
| Guardian alert | Utility template `guardian_alert` | Normal bot message (no template needed) |
| "Checking" indicator | Separate "Sedang dicek…" message | "Sedang dicek…" edited into the verdict, plus "typing…" |
| Local dev | Needs a public URL | `python -m app.channels.telegram_bot poll` (no URL needed) |

### Telegram quick start

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Add `META_API_KEY` to `.env`.
3. `.venv/bin/python -m app.channels.telegram_bot poll`, then forward a scam message to your bot.

In production, set `TELEGRAM_WEBHOOK_URL=https://<app>.up.railway.app/telegram/webhook` and a random `TELEGRAM_WEBHOOK_SECRET` (required: without it the webhook isn't registered and requests are rejected). Polling and the webhook can't run at the same time; `poll` removes the webhook, so redeploy the Railway service (or run `set-webhook`) afterwards.

Every Telegram verdict has a **🔎 Detail** button (`callback_data = detail:<check_id>`). Tapping it replies with a compact trace of that check, read from the database (`verdicts.details`):
- each step as tool → short result;
- the model(s) used, including a Spark → Wonder escalation;
- the confidence;
- every "Muse said X → rule changed it to Y" override.

Everything is HTML-escaped, and only the chat that owns the check can open it. The main reply is unchanged.

Telegram commands:
- `/panggil <nama>`, `/gaya santai|sopan`
- `/daftar_wali [anak|cucu|keponakan|…]` (alias `/link`)
- `/kata_aman <petunjuk>`, `/status`, `/hapus_wali`, `/bantuan`

The plain-text WhatsApp phrases work too.

### WhatsApp commands (parent)

| Command | What it does |
|---|---|
| *(forward anything)* | Check the message, photo, or voice note |
| `panggil Mama` | What the bot calls you (asked once on first use) |
| `gaya santai` / `gaya sopan` | Casual (default) or polite tone |
| `daftar wali 08xxxxxxxxxx anak Hans` | Link a guardian; relation and name are optional and are used in replies ("Telepon Hans sekarang ya, Mama") |
| `kata aman <petunjuk>` | Save a **hint** for the family safe word (the word itself is never stored) |
| `hapus wali` | Remove guardians |
| `status` | Show linked guardians |
| `bantuan` | Help |

## Meta tools used

| Meta tool | Used for |
|---|---|
| **Muse Spark** (`muse-spark-1.3`) via the Meta Model API | The agent brain: picks and calls tools, returns a structured verdict JSON, answers follow-ups |
| **Muse Spark image input** | Reading screenshots, transfer proofs and QRIS photos in the same verdict call |
| **Muse Voice Transcribe** (`muse-voice-transcribe-1.0`) | Forwarded voice notes and video call recordings → text (Indonesian language bias) |
| **Muse Wonder** (`muse-wonder-1.0`) | Second opinion: when Spark's confidence is below 0.7 (or it can't answer), the same request with tools and image goes to Wonder once. Footer: "Muse Spark → Muse Wonder (cek ulang)". Live: Spark 0.65 → Wonder, about 13 s instead of about 5 s |
| **SAM 3.1** (`sam-3.1`) | When Muse sees a QR code that the local scan missed (a small, blurry QRIS sticker in a 12 MP warung photo), `decode_qris` asks SAM for "QR code", crops it and decodes the crop |
| **WhatsApp Cloud API** | Second channel: webhook, signature check, replies, `guardian_alert` utility template |

Every verdict is produced by Muse Spark, or by Muse Wonder when Spark is unsure. The footer names the model that actually answered. `LLM_PROVIDER=fireworks` / `FIREWORKS_API_KEY` exist only as a dev-time fallback for API outages. The fallback is off unless that key is set (it isn't for the demo), and if it ever answers, the reply footer names the model with "(cadangan)".

## Demo simulator (`/wa`)

`http://localhost:8000/wa` is a WhatsApp-*style* chat simulator for screen recordings. It shows two phones side by side: the parent and the guardian. It runs on the **same backend**: onboarding, panggilan, guardian alerts, follow-ups, Wonder/SAM and lookups. It uses a separate web channel (`/demo/*`) and does not talk to WhatsApp.
- The panel has one-click scenarios: APK, "polisi" voice note and video, a QRIS photo that needs SAM, the "nomor baru" scam, the undian scam, a safe message, and follow-ups such as "aduh aku udah transfer".
- "Langsung siap" prepares Mama + wali Hans with an empty chat.
- Press **H** to hide all controls while recording.
- On a deployed server it needs the `ANALYZE_TOKEN` (field in the panel).

Say in the video that this is a simulator of the chat flow. The live channels are Telegram and the WhatsApp Cloud API adapter.

## Setup

```bash
uv venv .venv --python 3.11 && uv pip install -p .venv -r requirements.txt   # or: python -m venv .venv && pip install -r requirements.txt
cp .env.example .env    # fill in values locally; never commit .env
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 for the test page, or:

```bash
curl -s localhost:8000/analyze -H 'content-type: application/json' -H "X-Analyze-Token: $ANALYZE_TOKEN" \
  -d '{"type":"text","text":"Halo, CS BCA. Rekening Anda diblokir, kirim kode OTP."}' | jq .verdict
```

`/analyze` accepts `{"type": "text|image|audio|video|document", "text": "...", "media_base64": "...", "mime": "..."}`. Each call spends Muse credits, so it's gated:
- It needs `X-Analyze-Token` whenever `ANALYZE_TOKEN` is set.
- In production it's **off** unless `ANALYZE_TOKEN` is set.
- It's rate-limited per IP (`ANALYZE_RATE_PER_MIN`, default 10) and media is capped at 10 MB.

Locally, with no token set, it's open. The test page has a token field.

### Tests

```bash
.venv/bin/python -m pytest -q                                   # offline: QRIS, links, agent loop (fake model), WA + Telegram webhooks, guardian flow
RUN_LIVE=1 META_API_KEY=... .venv/bin/python -m pytest -k live -s  # ~40 cases in tests/cases.yaml + images, voice, video against Muse Spark
```

### Deploy (Railway)

1. Push to GitHub. In Railway: **New Project → Deploy from GitHub repo**. `railway.json` sets the start command and health check, and `.python-version` pins Python 3.11.
2. **+ New → Database → PostgreSQL** in the same project. On the app service, add the variable `DATABASE_URL=${{Postgres.DATABASE_URL}}`. Tables are created on startup, and new columns are added automatically.
3. Add the secret variables (names below), including a random `ANALYZE_TOKEN` if you want the test page online. Then **Settings → Networking → Generate Domain**.
4. Telegram: set `TELEGRAM_WEBHOOK_URL=https://<app>.up.railway.app/telegram/webhook` and a random `TELEGRAM_WEBHOOK_SECRET` (`openssl rand -hex 24`); the webhook is registered on startup. Check `/health`: `telegram_secret_check` and `analyze_locked` should both be `true`.
5. WhatsApp: in the Meta app → WhatsApp → Configuration, set the callback URL to `https://<app>.up.railway.app/webhook` with your `WA_VERIFY_TOKEN`, then subscribe to `messages`.

Railway sets `RAILWAY_ENVIRONMENT_ID`, which makes the app **fail closed**: a missing webhook secret or app secret rejects requests instead of skipping the check. ffmpeg comes bundled via `imageio-ffmpeg`, so no system package is needed. (`render.yaml` is kept as an alternative.)

### Guardian alert template

Submit in WhatsApp Manager, category **Utility**, language `id`, name `guardian_alert`:

> Halo, {{1}} baru saja menerima pesan yang terdeteksi BAHAYA ({{2}}). Mohon hubungi beliau sebelum ada uang yang ditransfer. - Cek Dulu

`{{1}}` = parent name + masked number, `{{2}}` = scam category.

### Environment variables (names only)

`META_API_KEY`, `META_API_BASE_URL`, `MUSE_MODEL`, `MUSE_REASONING_EFFORT`, `ASR_MODEL`, `LLM_PROVIDER` (`meta` | `fireworks`), `FIREWORKS_API_KEY`, `FIREWORKS_BASE_URL`, `FIREWORKS_MODEL`, `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_WABA_ID`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `WA_GRAPH_VERSION`, `WA_GUARDIAN_TEMPLATE`, `WA_TEMPLATE_LANG`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL`, `DATABASE_URL`, `ANALYZE_TOKEN`, `ANALYZE_RATE_PER_MIN`, `APP_ENV` (optional, `production` forces fail-closed), `GOOGLE_SAFE_BROWSING_KEY` (optional).

## Privacy & safety

- Media is analyzed in memory and never stored; only the extracted text and the verdict are kept.
- The first message includes a consent line.
- Forwarded content is treated as data, never as instructions, and the output is schema-validated.
- `check_link` follows redirects hop by hop and refuses private/loopback addresses (SSRF guard).
- Webhooks are authenticated, and in production they fail closed if a secret is missing. `/analyze` is token-gated and rate-limited.
- The bot token never reaches the logs: httpx URL logging is silenced, and download errors don't include the URL.
- Secrets only live in env vars.

## Roadmap

**Official Komdigi / OJK API integration** (number and account checks; today we use a public lookup and links), APK static checks, IASC report filing, pgvector similar-case lookup, Prompt Guard, more regional languages.

*Cek dulu, baru transfer.*
