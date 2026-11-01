"""Generate fake scam material for manual testing (forward these to the bot or upload on the test page).

    .venv/bin/python -m tests.manual.make_assets

Everything here is FAKE and harmless: QR codes pay nobody, the .apk is a text file,
and the domains are invented (the script checks they don't resolve).
Dev-only dependency: pillow.
"""
from __future__ import annotations

import socket
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.tools.qris import crc16_ccitt, decode_qris

OUT = Path(__file__).parent / "assets"
FONTS = Path("/System/Library/Fonts/Supplemental")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default(size)


# --------------------------------------------------------------------------- QRIS

def tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def qris_payload(merchant: str, city: str, nmid: str, break_crc: bool = False) -> str:
    mai = tlv("00", "ID.CO.QRIS.WWW") + tlv("02", nmid) + tlv("03", "UMI")
    body = (tlv("00", "01") + tlv("01", "11") + tlv("26", tlv("00", "ID.CO.BANKFAKE.WWW") + tlv("01", "9360099900001234"))
            + tlv("51", mai) + tlv("52", "5812") + tlv("53", "360") + tlv("58", "ID")
            + tlv("59", merchant) + tlv("60", city) + "6304")
    crc = crc16_ccitt(body)
    if break_crc:
        crc = f"{(int(crc, 16) ^ 0x1F2E):04X}"
    return body + crc


def qr_image(payload: str, px: int = 560) -> Image.Image:
    qr = cv2.QRCodeEncoder.create().encode(payload)
    qr = cv2.resize(qr, (px, px), interpolation=cv2.INTER_NEAREST)
    return Image.fromarray(qr).convert("RGB")


def qris_sticker(printed_name: str, printed_nmid: str, payload: str, path: Path) -> None:
    """A shop's QRIS standee. `printed_*` is what the sticker shows; `payload` is what the QR really pays."""
    W, H = 760, 1080
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 150], fill=(200, 16, 46))
    d.text((40, 42), "QRIS", font=font(64, True), fill="white")
    d.text((W - 340, 58), "QR Code Standar\nPembayaran Nasional", font=font(24), fill="white")
    d.text((W // 2, 210), printed_name, font=font(46, True), fill="black", anchor="mm")
    d.text((W // 2, 262), f"NMID : {printed_nmid}", font=font(26), fill=(60, 60, 60), anchor="mm")
    img.paste(qr_image(payload), ((W - 560) // 2, 310))
    d.text((W // 2, 920), "SATU QRIS UNTUK SEMUA", font=font(30, True), fill=(200, 16, 46), anchor="mm")
    d.text((W // 2, 970), "Cek aplikasi penyelenggara di: www.aspi-qris.id", font=font(22), fill=(90, 90, 90), anchor="mm")
    d.rectangle([0, H - 40, W, H], fill=(200, 16, 46))
    img.save(path)


# --------------------------------------------------------------------------- fake chat screenshot

def chat_screenshot(sender: str, bubbles: list[str], path: Path) -> None:
    W = 720
    body_font, link_font, small = font(28), font(21), font(20)
    # Links stay on one line (smaller font, blue) like a real chat; prose wraps.
    wrapped = [[b] if b.startswith("http") else textwrap.wrap(b, 38) for b in bubbles]
    H = 200 + sum(len(w) * 38 + 70 for w in wrapped) + 60
    img = Image.new("RGB", (W, H), (236, 229, 221))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 120], fill=(7, 94, 84))
    d.ellipse([24, 30, 84, 90], fill=(200, 200, 200))
    d.text((104, 34), sender, font=font(30, True), fill="white")
    d.text((104, 74), "online", font=small, fill=(220, 240, 235))
    y = 160
    for lines in wrapped:
        h = len(lines) * 38 + 50
        d.rounded_rectangle([24, y, W - 120, y + h], radius=16, fill="white")
        ty = y + 16
        for line in lines:
            is_link = line.startswith("http")
            d.text((44, ty + (6 if is_link else 0)), line, font=link_font if is_link else body_font,
                   fill=(20, 110, 200) if is_link else "black")
            ty += 38
        d.text((W - 200, y + h - 30), "10.42", font=small, fill=(130, 130, 130))
        y += h + 20
    img.save(path)


# --------------------------------------------------------------------------- voice note

def voice_note(text: str, path: Path) -> bool:
    aiff = path.with_suffix(".aiff")
    try:
        subprocess.run(["say", "-v", "Damayanti", "-r", "185", "-o", str(aiff), text], check=True)
        import imageio_ffmpeg

        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(aiff),
                        "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k", str(path)], check=True)
        return True
    except (OSError, subprocess.CalledProcessError) as exc:
        print("  (skipped voice note:", exc, ")")
        return False
    finally:
        aiff.unlink(missing_ok=True)


def video_call_mp4(caller: str, audio: Path, path: Path) -> bool:
    """Still 'video call' frame + an existing voice note -> mp4 (tests the video path)."""
    W, H = 720, 1280
    img = Image.new("RGB", (W, H), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((W // 2, 120), "Panggilan video WhatsApp", font=font(30), fill=(200, 200, 200), anchor="mm")
    d.text((W // 2, 180), caller, font=font(40, True), fill="white", anchor="mm")
    d.rounded_rectangle([110, 300, W - 110, 900], radius=30, fill=(40, 52, 70))
    d.text((W // 2, 600), "[ kamera: ruang 'kantor polisi' ]", font=font(26), fill=(170, 180, 200), anchor="mm")
    d.text((W // 2, 1180), "CONTOH UNTUK TES - BUKAN REKAMAN ASLI", font=font(24, True), fill=(230, 80, 80), anchor="mm")
    frame = path.with_suffix(".png")
    img.save(frame)
    try:
        import imageio_ffmpeg

        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(frame),
                        "-i", str(audio), "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                        "-r", "2", "-c:a", "aac", "-b:a", "64k", "-shortest", str(path)], check=True)
        return True
    except (OSError, subprocess.CalledProcessError) as exc:
        print("  (skipped video:", exc, ")")
        return False
    finally:
        frame.unlink(missing_ok=True)


# --------------------------------------------------------------------------- main

FAKE_LINKS = [
    "http://jnt-cekresi-paket.xyz/resi/JP8812345670.apk",
    "https://bca-verifikasi-nasabah.top/login",
    "http://sicepat-tracking-resi.site/cek?resi=SCP0099812",
    "https://bri-tarif-baru-2026.online/form",
    "http://bansos-lansia-kemensos.click/daftar",
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. Tampered: sticker says WARUNG BU SRI, QR actually pays ANDI CELL (valid CRC: a real scammer QR pasted over)
    qris_sticker("WARUNG BU SRI", "ID1020021234567",
                 qris_payload("ANDI CELL", "JAKARTA UTARA", "ID1023099887766"), OUT / "qris_palsu_tempelan.png")
    # 2. Broken checksum: name matches but payload was edited by hand
    qris_sticker("WARUNG BU SRI", "ID1020021234567",
                 qris_payload("WARUNG BU SRI", "BANDUNG", "ID1020021234567", break_crc=True), OUT / "qris_crc_rusak.png")
    # 3. Legit-looking: everything matches
    qris_sticker("WARUNG BU SRI", "ID1020021234567",
                 qris_payload("WARUNG BU SRI", "BANDUNG", "ID1020021234567"), OUT / "qris_asli.png")

    # 4. Fake courier chat screenshot
    chat_screenshot("+62 857-2231-0098", [
        "Selamat siang, kami dari J&T Express. Paket atas nama Ibu tertahan di gudang karena alamat kurang lengkap.",
        "Silakan cek resi dan konfirmasi alamat lewat aplikasi berikut ya Bu, paling lambat hari ini:",
        FAKE_LINKS[0],
        "Kalau tidak dikonfirmasi paket akan dikembalikan ke pengirim.",
    ], OUT / "resi_palsu_chat.png")

    # 5. Fake bank CS screenshot
    chat_screenshot("CS BCA Resmi", [
        "Yth. Nasabah, terdeteksi transaksi mencurigakan Rp4.750.000 di rekening Anda.",
        "Untuk membatalkan, segera verifikasi di link berikut dan masukkan kode OTP yang kami kirim:",
        FAKE_LINKS[1],
        "Abaikan pesan ini = rekening diblokir permanen dalam 1x24 jam.",
    ], OUT / "cs_bank_palsu_chat.png")

    # 6. Dummy APK (just text; the bot never opens documents anyway)
    (OUT / "Cek_Resi_JNT.apk").write_text("FAKE TEST FILE for Cek Dulu. This is not an Android app.\n")

    # 7. Voice note
    voice_note("Ma, ini aku. Aku kecelakaan motor, sekarang di rumah sakit. Hp-ku rusak, ini pinjam hp teman. "
               "Tolong transfer dua juta sekarang ya Ma buat biaya rumah sakit. Jangan bilang Papa dulu.",
               OUT / "voice_kecelakaan.ogg")

    # 8. Fake police (Polda Metro Jaya warned about this modus, Oct 2025). Self-written script, not a real recording.
    voice_note("Selamat siang Ibu, saya Bripka Andi dari Polda Metro Jaya. Nama Ibu tercatat sebagai penerima paket "
               "narkoba. Ibu jangan tutup telepon ini dan jangan beritahu keluarga. Untuk membuktikan Ibu tidak "
               "terlibat, transfer seluruh tabungan ke rekening penampungan PPATK yang saya kirim sekarang.",
               OUT / "voice_polisi_gadungan.ogg")

    # 9. Fake police "video call" recording (audio = the script above)
    video_call_mp4("Bripka Andi - Polda Metro Jaya", OUT / "voice_polisi_gadungan.ogg", OUT / "video_polisi_gadungan.mp4")

    # 10. QRIS stuck over a mosque donation box (BI / CNN Indonesia: sticker-swap on kotak infaq)
    qris_sticker("MASJID AL-IKHLAS", "ID1020027654321",
                 qris_payload("TOKO PULSA JAYA", "BEKASI", "ID1023055501234"), OUT / "qris_kotak_infaq.png")

    # 11. Fake online shop (IASC #1 by report count): too-cheap promo + DP to a personal account
    chat_screenshot("Toko Elektronik Official", [
        "PROMO CUCI GUDANG!! Kulkas 2 pintu Sharp cuma Rp1.250.000 (harga normal Rp4,5 jt). Stok tinggal 3 kak.",
        "Kalau mau, transfer DP Rp500.000 dulu ke rek admin ya kak: BRI 0123 4567 8901 a.n. Siska Wulandari",
        "Barang langsung dikirim besok. Jangan lewat aplikasi ya kak biar tidak kena potongan admin.",
    ], OUT / "toko_online_palsu_chat.png")

    # Self-checks
    print("QR decode check:")
    for name in ("qris_palsu_tempelan.png", "qris_crc_rusak.png", "qris_asli.png", "qris_kotak_infaq.png"):
        q = decode_qris((OUT / name).read_bytes())
        print(f"  {name:26} merchant={q and q['merchant_name']!r:18} crc_valid={q and q['crc_valid']}")
    print("Fake domains must NOT resolve:")
    for url in FAKE_LINKS:
        host = urlparse(url).hostname
        try:
            socket.getaddrinfo(host, None)
            print(f"  WARNING {host} resolves! Someone owns it; don't use it.")
        except socket.gaierror:
            print(f"  ok  {host}")
    print("Wrote:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
