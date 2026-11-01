# Research coverage

How each item in [scam_research.md](scam_research.md) is handled in Cek Dulu. "Live test" means it's checked against the real Muse Spark API (`RUN_LIVE=1 pytest -k live`). The full run on 1 Nov 2026 passed **38/38**. After the contacts map was added, the text + image matrix (41 text cases incl. 9 false-positive controls and a "sudah transfer" case, plus 4 images) passed **45/45**, and no reply contained a number outside `contacts.py`.

## 1. Top scam types (IASC ranking)

| # | Modus | How Cek Dulu handles it | Category | Live test(s) |
|---|---|---|---|---|
| 1 | Belanja online | Prompt pattern (too-cheap promo, DP to a personal account, "jangan lewat aplikasi"). Account numbers are spotted and flagged to the model (a transfer to a personal account is a red flag) | `belanja_online` | `belanja_online`, `toko_online_palsu_chat.png` (screenshot) |
| 2 | Fake call / impersonation | Voice notes **and videos** are transcribed. `safe_word_check` runs for "family" requests. Advice is to call back on the old number. Guardian is alerted | `minta_pulsa`, `cs_bank_palsu`, `aparat_palsu` | `pulsa_*`, `cs_bank_*`, `voice_kecelakaan.ogg`, `voice_polisi_gadungan.ogg`, `video_polisi_gadungan.mp4` |
| 3 | Investasi bodong (incl. AI robot trading, fake gold sites) | Prompt pattern. Link check flags Antam/logammulia lookalikes. Code appends Kontak OJK 157 | `investasi` | `investasi_1`, `investasi_ai_robot` (must show WA 081-157-157-157), `emas_palsu` |
| 4 | Lowongan / part-time job | Prompt pattern (like/task, then "deposit for premium tasks") | `lowongan_kerja` | `investasi_kerja` |
| 5 | Media sosial (hacked account) | Treated as impersonation ("akun lama kena hack, pinjam dulu") + safe-word check | `minta_pulsa` | `akun_dibajak` |
| 6 | Hadiah / undian palsu | Prompt pattern (pay tax/admin first) | `hadiah_palsu` | `hadiah`, `hadiah_bri`, `phishing_undian` |
| 7 | Phishing | `check_link`: shortener, cheap TLD, brand lookalike, `.apk`, IP, punycode; follows redirects safely; optional Google Safe Browsing | (by lure) | `kurir_apk_typo`, `cs_bank_tarif`, `hoaks_bansos`, `phishing_undian`, `resi_palsu_chat.png` |
| 8 | Social engineering (fake CS, tariff change) | Prompt pattern ("tarif Rp150rb/bulan, isi formulir") | `cs_bank_palsu` | `cs_bank_tarif`, `tarif_bca` |
| 9 | Pinjol fiktif | Prompt pattern (upfront admin fee, "tanpa BI checking") | `pinjol` | `pinjol` |
| 10 | APK via WhatsApp | A `.apk` attachment is **always BAHAYA** (deterministic rule). Next steps include "already installed: turn off internet, call the bank" | `kurir_apk` | `kurir_apk_1` (must mention already-installed step), `kurir_apk_2`, `Cek_Resi_JNT.apk` |
| – | QRIS sticker swap (shops **and mosque donation boxes**) | Muse calls the `decode_qris` tool (2 OpenCV detectors, survives tilted/blurry photos) and compares the merchant name with the shop/mosque name. Checksum verified; a bad CRC is never AMAN | `qris_palsu` | `qris_palsu_tempelan.png`, `qris_kotak_infaq.png`; offline: decode of all stickers + checksum |
| – | Fake gold sites | Link check flags `antam`/`logammulia` names not on logammulia.com (e.g. `antamlogamulia-id.com`) | `investasi` | `emas_palsu` |

## 2. Headline numbers

In the README "Why it matters" section, with the research's caveat that IASC counts are cumulative since 22 Nov 2024. The "80% report after 12 hours" finding drives the product: answer *before* the transfer, alert the guardian right away, and put "call the bank, then IASC" first in the emergency follow-up.

## 3. Elderly, parents and AI voice

- The system prompt says to **never claim deepfake detection** and to judge the request instead (urgency, personal account, OTP, "new number", "don't tell anyone").
- No "% of victims are elderly" figure is used anywhere, because the research found no official breakdown.

## 4. Official reporting channels

All contacts live in [`app/agent/contacts.py`](app/agent/contacts.py). **The model never writes a contact.** It outputs `scam_type` and `money_sent`, and the code appends the matching contacts. Any other phone, short-code or account number in model text is removed (rule `nomor_tidak_resmi_dihapus`), and so is any in follow-up answers. A test checks that no reply contains a number outside the file.

| scam_type / situation | Contacts appended |
|---|---|
| `fake_police`, `impersonation` | Polisi **110** (plus the money-sent contacts if a transfer happened) |
| `bank_otp`, or `money_sent` for any type (incl. "aku sudah transfer" follow-ups) | Halo BCA **1500888** (other banks: number on the back of the ATM card), then **https://iasc.ojk.go.id**, ideally within 12 hours so funds can be blocked |
| `investment`, `pinjol` | Kontak OJK **157** / WA **081-157-157-157** |
| A scammer phone number in a BAHAYA/WASPADA text, screenshot or voice note | Extra step **"Laporkan nomor 0857-xxxx-xxxx di aduannomor.id (situs resmi Komdigi)"**. Numbers are normalized from +62/62/08 to 08xx; in text and transcripts they must actually appear there; on screenshots Muse reads them. The bot never says who owns a number or where it's from |

AMAN verdicts get no contacts.

**Not used, on purpose:**
- Other banks' call centers (Mandiri 14000, BRI 1500017, …): not verified yet (TODO in `contacts.py`).
- cekrekening.id, aduankonten.id, SIPASTI, patrolisiber.id: not on the verified list.

## 5. Demo and test coverage

| Recommendation | Done |
|---|---|
| Fake call / family impersonation: voice → ASR → verdict + call-back + guardian alert | ✅ voice + video, safe-word check, guardian alert (and an emergency alert if the parent says they already paid) |
| APK: instant bahaya + "already installed?" steps | ✅ |
| Belanja online: screenshot + account number | ✅ (account flagged; cekrekening.id left out until verified) |
| QRIS swap | ✅ shop + mosque |
| Phishing / hadiah palsu / investment, lookalike domains | ✅ |
| AMAN controls (real J&T update, family chat, legit bank promo) | ✅ `aman_jnt_asli`, `aman_keluarga`, `aman_promo_bank`, `aman_ojk_info`, `aman_arisan`, `aman_ultah`, plus strict AMAN `aman_posyandu`, `aman_cucu_sampai`, `aman_driver_gofood`, and `aman_tagihan_pln`, `aman_otp_sendiri` |
| Borderline WASPADA (not every odd message is a scam) | ✅ `waspada_nomor_baru`, `waspada_teman_pinjam`, `waspada_lowongan_umum`, `waspada_promo_link_asli` |

## Case study: fake police video call

| Research point | Handled by |
|---|---|
| Red flags: police don't interrogate over WA video; "don't hang up / don't tell family"; PPATK/Kejaksaan never ask for transfers | `aparat_palsu` pattern in the system prompt |
| Next steps: hang up, call 110 / police station, tell family, don't transfer | Prompt; live test `polisi_gadungan` must mention 110 or the police station |
| Demo with a self-written script, not a real recording | `voice_polisi_gadungan.ogg` and `video_polisi_gadungan.mp4` (generated by `tests/manual/make_assets.py`, frame says "CONTOH UNTUK TES") |
| Pitch line | README "Why it matters" |
