# Manual test cases (forward to the bot, or use the test page)

Everything here is fake. QRs pay nobody, the `.apk` is a text file, and the domains don't exist.
Regenerate the files with `.venv/bin/python -m tests.manual.make_assets` (files go to `tests/manual/assets/`).

**How to send:**
- Text: copy the message and send it to the bot.
- Images: send as a normal photo. If the QR doesn't decode, send it **as a file** (Telegram compresses photos).
- APK / voice: send the file.

| # | Case | What to send | Expected | What should show up |
|---|---|---|---|---|
| 1 | Mama minta pulsa | Text 1 | 🔴 BAHAYA · minta_pulsa | New number + urgent money request |
| 2 | Fake courier link | Text 2 | 🔴 BAHAYA · kurir_apk | `check_link` → `meniru_jnt`, `unduh_apk` |
| 3 | Fake BCA CS + OTP | Text 3 | 🔴 BAHAYA · cs_bank_palsu | OTP request, `check_link` → `meniru_bca` |
| 4 | Investasi bodong | Text 4 | 🔴 BAHAYA · investasi | "dijamin", % per hari |
| 5 | Health hoax | Text 5 | 🟡 WASPADA · hoaks | No source, "sebarkan" |
| 6 | Normal family message | Text 6 | 🟢 AMAN (or 🟡) | No money, no link |
| 7 | APK attachment | `assets/Cek_Resi_JNT.apk` + caption from text 7 | 🔴 BAHAYA · kurir_apk | APK safety floor, always BAHAYA |
| 8 | Fake courier screenshot | `assets/resi_palsu_chat.png` | 🔴 BAHAYA · kurir_apk | `describe_image` reads the link from the image |
| 9 | Tampered QRIS sticker | `assets/qris_palsu_tempelan.png` + caption "Mau bayar di Warung Bu Sri, aman ga?" | 🟡/🔴 · qris_palsu | `decode_qris` → merchant **ANDI CELL** ≠ printed **WARUNG BU SRI** |
| 10 | Voice note "aku kecelakaan" | `assets/voice_kecelakaan.ogg` | 🟡 WASPADA / 🔴 · minta_pulsa | `transcribe_audio`, plus `safe_word_check` question |

**From scam_research.md (section 5 demo coverage):**

| File | Caption | Expect |
|---|---|---|
| `assets/toko_online_palsu_chat.png` | `Bu ini promo kulkas murah, beli ga ya` | 🔴 belanja_online, says to check the account at **cekrekening.id** |
| `assets/qris_kotak_infaq.png` | `Ini QRIS di kotak infaq masjid, mau sedekah` | 🔴 qris_palsu: sticker says MASJID AL-IKHLAS, QR pays **TOKO PULSA JAYA** |
| `assets/video_polisi_gadungan.mp4` | (none) | 🔴 aparat_palsu: hang up, **110**, tell family |

**Bonus files:**
- `assets/voice_polisi_gadungan.ogg`: fake "Polda Metro Jaya" call (drug-package accusation, "jangan tutup telepon", transfer to a "PPATK" account). Expect 🔴 BAHAYA · aparat_palsu. Self-written script, based on Polda Metro Jaya's Oct 2025 warning.
- `assets/qris_crc_rusak.png`: the checksum is broken, so expect at least WASPADA.
- `assets/qris_asli.png`: everything matches, so expect AMAN or WASPADA. It must never be called "100% aman".
- `assets/cs_bank_palsu_chat.png`: the BCA CS scam as a screenshot.

To test the guardian alert, run `/daftar_wali` first and open the link from a second Telegram account. Cases 1–4, 7 and 8 should then alert that account. The second alert for the same category within 10 minutes is suppressed on purpose.

---

### Text 1: mama minta pulsa
```
Ma, ini nomor baru aku ya, hp lama jatuh ke air. Tolong beliin pulsa 100rb dulu ke nomor ini dong ma, penting bgt buat kerjaan. Nanti sore aku ganti 🙏
```

### Text 2: fake courier link
```
J&T Express: Paket Anda dengan resi JP8812345670 GAGAL DIKIRIM karena alamat tidak lengkap. Silakan update alamat & cek resi melalui aplikasi resmi kami: http://jnt-cekresi-paket.xyz/resi/JP8812345670.apk
Batas konfirmasi hari ini pukul 17.00 WIB.
```

### Text 3: fake BCA CS + OTP
```
Yth. Nasabah BCA, rekening Anda terdeteksi transaksi mencurigakan sebesar Rp4.750.000. Untuk membatalkan transaksi, segera login di https://bca-verifikasi-nasabah.top/login lalu sebutkan kode OTP yang masuk ke SMS Anda kepada petugas kami. Abaikan = rekening diblokir permanen.
```

### Text 4: investasi bodong
```
💰 PELUANG EMAS!! Investasi emas digital PROFIT 10% PER HARI, DIJAMIN TANPA RUGI! Modal 500rb jadi 5 JUTA dalam seminggu. Sudah 12.000 member sukses. Gabung grup VIP sekarang sebelum slot penuh, hubungi admin Kak Rina 0857-9911-2233
```

### Text 5: health hoax
```
PENTING!!! Info dari dokter RS Jepang: minum air rebusan daun sirsak + bawang putih tiap pagi bisa menyembuhkan kanker, diabetes, dan darah tinggi dalam 3 hari tanpa obat dokter. Sudah terbukti! Sebarkan ke 10 grup keluarga, jangan berhenti di Anda 🙏🙏
```

### Text 6: normal family message
```
Ibu-ibu, arisan bulan ini di rumah Tante Rini ya, hari Minggu jam 4 sore. Jangan lupa bawa kue masing-masing. Yang belum bayar kas tolong dibawa sekalian 😊
```

### Text 7: caption for the APK file
```
Paket atas nama ibu tertahan di gudang, silakan buka aplikasi ini untuk cek resi ya bu
```

### Extra texts (more link checks)
```
Nasabah BRI yth, mulai besok tarif transfer berubah jadi Rp150.000/bulan. Jika TIDAK SETUJU wajib isi formulir di https://bri-tarif-baru-2026.online/form sebelum jam 12 siang.
```
```
Bantuan Sosial Lansia Rp3.500.000 dari Kemensos SUDAH CAIR. Segera daftar dengan foto KTP & KK di http://bansos-lansia-kemensos.click/daftar sebelum kuota habis!
```
```
SiCepat: kurir kami sudah 2x ke rumah Anda tapi tidak ada orang. Jadwalkan ulang pengiriman: http://sicepat-tracking-resi.site/cek?resi=SCP0099812
```
