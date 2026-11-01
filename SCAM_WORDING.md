# How scammers actually word their messages (Indonesia, 2022–2026)

Research for Ronda on 1 Nov 2026: how real scam messages are worded, based on what Indonesian news and bank warnings quote. Verbatim quotes are marked as such. Everything else describes the pattern; few outlets print full messages. Every pattern below has a test case in `tests/cases.yaml`, with made-up numbers and links. All of them pass live against Muse Spark.

## 1. Fake bank letter: "tarif transfer jadi Rp150 ribu/bulan"

The most fully documented one. It's styled as an **official letter**: a letter number, the bank logo as the profile picture, and a deadline at midnight.

Verbatim (Republika, Dec 2024, BRI version):
> "Sehubungan dengan adanya peningkatan kualitas layanan transaksi transfer antar bank di tahun 2024,"
> "mulai hari ini malam pukul 00.00 WIB, Bank BRI melakukan perubahan skema tarif transaksi dari Rp 6.500/transaksi"
> "dan BI Fast Rp 2.500 akan diubah menjadi Rp 150 ribu per bulan (unlimited),"
> "1. Setuju dengan tarif baru Rp 150 ribu per bulan unlimited transaksi."
> "2. Tidak setuju dan mau tetap ke tarif lama Rp 6.500/ BI Fast Rp 2.500 per transaksi,"
> "Untuk penagihan potongan setiap bulannya akan langsung auto debet dari rekening tabungan,"

Markers:
- a letter number ("0311/Bank BRI/VI/2024");
- "Bapak/Ibu nasabah";
- a numbered **choice** (setuju / tidak setuju), where *disagreeing* sends you to the link;
- "auto debet";
- "jika tidak ada respons dianggap setuju".

The link's form asks for the account number and balance. One BRI customer lost about Rp99.5 million. The same template has been used for BCA since 2022.

Test: `tarif_bri_surat`, plus `cs_bank_tarif` and `tarif_bca`. Sources: [Republika](https://ekonomi.republika.co.id/berita/sp5trg423/waspada-modus-penipuan-atas-nama-bri-via-whatsapp), [Kontan](https://keuangan.kontan.co.id/news/waspada-modus-penipuan-kenaikan-tarif-transaksi-rp-150000-intai-nasabah-bca-dan-bri), [detik](https://finance.detik.com/moneter/d-6304256/awas-kena-hoaks-lagi-bri-tak-pernah-patok-biaya-transaksi-rp-150000bulan), [Bisnis](https://finansial.bisnis.com/read/20240204/90/1738069/dear-nasabah-bca-hati-hati-penipuan-modus-tarif-transfer-antarbank), [Fortune IDN](https://www.fortuneidn.com/finance/waspadai-modus-penipuan-perubahan-biaya-bca-jadi-rp150-ribu-00-ccw2k-2t0gw3).

## 2. APK files: courier receipt, wedding invitation, e-tilang, PLN/BPJS/tax bill, Lebaran greeting

The **file name carries the lure**, and the text is very short.
- The viral wedding version was a 6.6 MB file named **"Surat Undangan Pernikahan Digital"**, followed only by **"Kami harap kehadirannya"** (quoted in Detik and others).
- One victim in Malang lost Rp1.4 billion.
- In 2025, Kaspersky's "Tria Stealer" campaign spread the same lure as APKs in WhatsApp and Telegram groups, often from hacked contacts.

Fake e-tilang / ETLE messages (still active in Feb 2026):
- They look like an "official confirmation" and threaten a **fine or STNK block** within a short deadline.
- They attach a "surat tilang" APK or link.
- Real ETLE notices come by post, email, or WhatsApp from a blue-tick account.

Tests: `undangan_apk_kehadiran`, `etle_apk`, `kurir_apk_1/2`, `kurir_apk_typo`, `Cek_Resi_JNT.apk`, `resi_palsu_chat.png`. Sources: [detik](https://inet.detik.com/security/d-6537689/awas-penipuan-undangan-pernikahan-di-wa-bisa-kuras-rekening), [Bisnis](https://finansial.bisnis.com/read/20230707/90/1672751/download-undangan-nikah-saldo-tabungan-hilang-rp14-miliar), [Kompas TV](https://www.kompas.tv/lifestyle/613512/malware-tria-stealer-sasar-android-via-apk-undangan-nikah-palsu-begini-cara-hindarinya), [CNBC Indonesia](https://www.cnbcindonesia.com/tech/20250502072032-37-630310/penipuan-whatsapp-korbannya-makin-banyak-ini-modus-terbaru), [detik oto](https://oto.detik.com/berita/d-8364091/masih-ada-penipuan-modus-tilang-elektronik-di-whatsapp-jangan-asal-klik-link), [CNN Indonesia](https://www.cnnindonesia.com/otomotif/20251215144104-579-1306939/waspada-modus-penipuan-tilang-elektronik-via-sms), [Kontan (Lebaran APK)](https://nasional.kontan.co.id/news/bahaya-ucapan-selamat-lebaran-pakai-file-apk-begini-cara-mengatasinya), [Bisnis (tax APK)](https://finansial.bisnis.com/read/20241113/90/1815193/marak-penipuan-tagihan-pajak-berekstensi-apk-bri-edukasi-masyarakat).

## 3. Family impersonation: "Ma, ini nomor baru", the "Hi Mum" variant, "mama minta pulsa"

Same story as the old "mama minta pulsa" SMS:
- a **new number** and a reason (HP rusak, akun dibajak);
- then an **urgent money request**, often with "jangan bilang siapa-siapa".

The advice in the news matches ours: ask something only family knows, or call the old number. No source printed one full canonical message, so our test messages are reconstructions.

Tests: `pulsa_1`, `pulsa_jawa`, `pulsa_kecelakaan`, `akun_dibajak`, `waspada_nomor_baru`, and the `/wa` demo chat. Sources: [BCA](https://www.bca.co.id/id/informasi/awas-modus/2021/01/06/09/15/Awas-Modus-Ngaku-ngaku-Orang-Yang-Kamu-Kenal-Padahal-Penipu), [Viva ("Hi Mum")](https://techno.viva.co.id/platform/7363-waspada-penipuan-hi-mum-di-whatsapp-modus-baru-menyasar-emosi-keluarga), [CNBC Indonesia](https://www.cnbcindonesia.com/tech/20250502072032-37-630310/penipuan-whatsapp-korbannya-makin-banyak-ini-modus-terbaru).

## 4. "Aku menemukan kontakmu…" (new in 2025)

Verbatim (detik, 28 Oct 2025, citing Malwarebytes):
> "Hai, aku menemukan kontakmu disimpan di kontakku, bisa ingatkan aku di mana kita berbincang sebelumnya?"

It's a rapport opener that leads to a romance or investment scam later. The key advice is **don't reply**: replying confirms the number is active.

**Change made:** Ronda used to suggest "ask who they are". The prompt now says not to reply and to block.

Test: `kontak_tersimpan`, which must mention "jangan dibalas" or "abaikan". Source: [detik](https://inet.detik.com/security/d-8182252/modus-penipuan-baru-di-whatsapp-kasih-tahu-keluarga-biar-tak-kena).

## 5. WhatsApp takeover: "salah kirim kode", vote links asking for a PIN

**"I sent my code to your number by mistake":**
- The scammer pretends to be a friend, or someone who mistyped a number while buying a game voucher or pulsa.
- They ask for the **6-digit code**, which is the victim's own WhatsApp verification code.

**Fake voting page (Sept 2025):**
- The message is "tolong vote" with a link.
- The page asks for the WhatsApp number and 6-digit PIN, which is enough to take over the account.

**Change made:** both are now listed in the prompt as account takeover (BAHAYA).

Tests: `otp_salah_kirim`, `voting_pin_wa`. Sources: [Cermati](https://www.cermati.com/artikel/apa-itu-kode-otp-dan-bahayanya-jika-dibagikan), [CNN Indonesia](https://www.cnnindonesia.com/teknologi/20210309183450-185-615680/waspada-pesan-whatsapp-minta-kode-otp-modus-ambil-alih-akun), [detik](https://inet.detik.com/security/d-7857145/waspada-penipuan-kirim-otp-ke-whatsapp-segera-lakukan-ini), [detik (voting)](https://inet.detik.com/security/d-8127866/modus-penipuan-baru-di-whatsapp-orangtua-rentan-jadi-korban).

## 6. Prize / undian: "SELAMAT! Nomor Anda…"

- Accounts imitate the bank, with the bank logo as the profile picture.
- They announce a prize (car, motorbike, umrah, cash).
- They ask for **"pajak hadiah" or an admin fee first**, or for registration through a link.
- BRI's real prize draws never ask for data through links.

Tests: `hadiah`, `hadiah_bri` (also reports the WA number at aduannomor.id), `phishing_undian`, `dana_kaget_link`. Sources: [Republika](https://ekonomi.republika.co.id/berita/sp5trg423/waspada-modus-penipuan-atas-nama-bri-via-whatsapp), [Tirto](https://tirto.id/hoaks-pendaftaran-undian-akhir-tahun-bri-g6Ky), [detik](https://finance.detik.com/moneter/d-7523553/bri-imbau-nasabah-untuk-berhati-hati-terhadap-penipuan-online/amp).

## 7. Part-time jobs: like/follow, then "tugas premium" with a deposit

- It starts as "kerja paruh waktu dari HP": like or follow TikTok/YouTube posts for Rp10–15k per task.
- Small payouts build trust, then "tugas peningkatan" requires a deposit. In one victim's case it went Rp14.7 million, then Rp30 million as the "tugas akhir".
- Scammers often borrow big brand names (TikTok Shop, Mandiri Taspen, KAI).
- OJK has flagged 12 such entities.

Tests: `loker_tiktok_tugas`, `investasi_kerja`, `waspada_lowongan_umum`. Sources: [OCBC](https://www.ocbc.id/id/article/2025/01/17/modus-penipuan-berkedok-lowongan-pekerjaan), [detik](https://finance.detik.com/fintech/d-6794854/marak-penipuan-berkedok-loker-via-wa-begini-modusnya), [Kontan](https://momsmoney.kontan.co.id/news/ini-daftar-penipuan-dengan-modus-penawaran-kerja-paruh-waktu-dengan-sistem-deposit).

## 8. Investment groups: "kelas VIP", a "mentor"/"profesor" and their "asisten"

- People are recruited from Instagram or TikTok ads into WhatsApp/Telegram "stock and crypto education" groups.
- An "asisten" handles the chat, with daily "sinyal" and screenshots of members' profits ("10% per hari").
- Then come deposits to personal accounts. In one Polda Metro Jaya case a victim lost Rp3.05 billion.
- In 2026 Satgas PASTI stopped "Universal Peak" (stock-investment scam).

Tests: `investasi_grup_mentor` (shows OJK 157 and the cekrekening step), `investasi_1`, `investasi_ai_robot`, `emas_palsu`. Sources: [CNBC Indonesia](https://www.cnbcindonesia.com/market/20260618081634-17-743565/ojk-hentikan-modus-penipuan-saham-universal-peak-pinjol-bafi-group), [OJK](https://ojk.go.id/id/Publikasi/Info-Hoax/Pages/HOAX-Penawaran-Investasi-yang-Mengatasnamakan-Anggota-Dewan-Komisioner-OJK-di-Group-Telegram.aspx), [OCBC](https://www.ocbc.id/id/article/2024/01/25/digital-security-scam-whatsapp-telegram).

## Wording tells that show up across modus

| Tell | Examples |
|---|---|
| Fake officialness | letter numbers, "Yth. Nasabah", "Bapak/Ibu", bank logo profile picture, "KONFIRMASI ETLE" |
| Deadlines | "mulai malam ini pukul 00.00", "paling lambat hari ini", "2x24 jam", "sebelum kuota habis" |
| Default-yes traps | "jika tidak ada konfirmasi dianggap setuju", "pilih 1 / 2" |
| Secrecy | "jangan bilang siapa-siapa", "jangan beritahu keluarga", "jangan tutup telepon" |
| Pay first | "pajak hadiah", "biaya admin", "deposit tugas premium", "jaminan" |
| Codes | "6 digit kode", "PIN", "OTP salah kirim" |
| Files | `.apk` named as a receipt, invitation, tilang, bill or greeting |
| Openers that test if the number is alive | "aku menemukan kontakmu", "salah sambung", "apa kabar?" |

Ronda's prompt covers all of these. The live test matrix (`RUN_LIVE=1 pytest -k live_matrix`) passes 50/50 on Muse Spark, including the 9 cases above.

**Limitation:** few outlets print complete scam messages. Only the BRI tariff letter and the "kontakmu" opener are quoted (near-)verbatim. The other cases are reconstructions from the patterns the sources describe.
