"""Record the /wa demo as a video, live against the running backend (real Muse calls).

    .venv/bin/uvicorn app.main:app --port 8765          # in another terminal
    uv pip install -p .venv playwright                   # once (dev tool, not in requirements.txt)
    .venv/bin/python -m tests.manual.record_demo         # story flow (follows the voiceover) -> demo_out/ronda_story.mp4
    .venv/bin/python -m tests.manual.record_demo tour    # five-flow tour -> demo_out/ronda_demo.mp4

Uses the installed Google Chrome (no browser download). Every check is real: the waits are as long as
Muse actually takes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("DEMO_BASE", "http://localhost:8765")
OUT = Path(__file__).resolve().parents[2] / "demo_out"
W, H = 1440, 900

OVERLAY = r"""
(() => {
  const css = document.createElement("style");
  css.textContent = `
    #__cur { position: fixed; z-index: 2147483647; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%;
      background: rgba(255,255,255,.85); border: 2px solid rgba(17,27,33,.75); box-shadow: 0 2px 6px rgba(0,0,0,.3);
      pointer-events: none; transition: transform .08s; left: -50px; top: -50px; }
    #__cur.down { transform: scale(.7); background: rgba(37,211,102,.9); }
    #__cap { position: fixed; z-index: 2147483646; left: 34px; top: 50%; transform: translateY(-50%); width: 250px;
      font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #111b21;
      background: #fff; border-radius: 16px; padding: 18px 18px 20px; box-shadow: 0 10px 30px rgba(0,0,0,.15);
      transition: opacity .35s; opacity: 0; }
    #__cap .n { font-size: 12px; font-weight: 700; letter-spacing: .6px; color: #008069; text-transform: uppercase; }
    #__cap .t { font-size: 18px; font-weight: 700; margin: 6px 0 6px; line-height: 1.25; }
    #__cap .d { color: #54656f; }
    #__card { position: fixed; inset: 0; z-index: 2147483645; display: grid; place-items: center; text-align: center;
      background: radial-gradient(circle at 50% 40%, #0b7a63, #064d40); color: #fff; transition: opacity .6s;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    #__card h1 { font-size: 76px; margin: 0; letter-spacing: 1px; }
    #__card p { font-size: 24px; margin: 14px 0 0; opacity: .9; }
    #__card small { display: block; margin-top: 26px; font-size: 16px; opacity: .75; }
    #__card .shield { font-size: 64px; }
  `;
  document.head.appendChild(css);
  const cur = document.createElement("div"); cur.id = "__cur"; document.body.appendChild(cur);
  addEventListener("mousemove", (e) => { cur.style.left = e.clientX + "px"; cur.style.top = e.clientY + "px"; }, true);
  addEventListener("mousedown", () => cur.classList.add("down"), true);
  addEventListener("mouseup", () => cur.classList.remove("down"), true);
  const cap = document.createElement("div"); cap.id = "__cap"; document.body.appendChild(cap);
  window.__caption = (n, t, d) => {
    cap.style.opacity = 0;
    setTimeout(() => { cap.innerHTML = `<div class="n">${n}</div><div class="t">${t}</div><div class="d">${d || ""}</div>`;
                       cap.style.opacity = 1; }, 250);
  };
  window.__card = (html) => {
    let c = document.getElementById("__card");
    if (!c) { c = document.createElement("div"); c.id = "__card"; document.body.appendChild(c); }
    c.innerHTML = `<div>${html}</div>`; c.style.opacity = 1; c.style.display = "grid";
  };
  window.__hideCard = () => { const c = document.getElementById("__card"); if (c) { c.style.opacity = 0;
    setTimeout(() => { c.style.display = "none"; }, 650); } };
})();
"""


class Demo:
    def __init__(self, page):
        self.page = page

    def pause(self, s: float) -> None:
        time.sleep(s)

    def caption(self, n: str, title: str, desc: str = "") -> None:
        self.page.evaluate("([n, t, d]) => window.__caption(n, t, d)", [n, title, desc])
        self.pause(0.6)

    def click(self, selector: str, pause: float = 0.5) -> None:
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=28)
        self.pause(0.25)
        self.page.mouse.down()
        self.pause(0.08)
        self.page.mouse.up()
        self.pause(pause)

    def type(self, selector: str, text: str) -> None:
        self.click(selector, pause=0.3)
        self.page.keyboard.type(text, delay=55)
        self.pause(0.6)
        self.page.keyboard.press("Enter")

    def verdicts(self) -> int:
        return self.page.evaluate(
            "[...document.querySelectorAll('#p-parent > .chat .msg.in')].filter(m => m.innerText.includes('Dicek dengan')).length")

    def bot_messages(self) -> int:
        return self.page.evaluate("document.querySelectorAll('#p-parent > .chat .msg.in').length")

    def wait_verdict(self, before: int, timeout: float = 45) -> None:
        self.page.wait_for_function(
            "(n) => [...document.querySelectorAll('#p-parent > .chat .msg.in')]"
            ".filter(m => m.innerText.includes('Dicek dengan')).length > n", arg=before, timeout=timeout * 1000)

    def wait_bot_reply(self, before: int, timeout: float = 45) -> None:
        self.page.wait_for_function(
            "(n) => { const m = [...document.querySelectorAll('#p-parent > .chat .msg.in')];"
            " return m.length > n && !/^(🔎|🎧) Sedang |^🎬 /.test(m[m.length - 1].innerText); }",
            arg=before, timeout=timeout * 1000)

    def forward_to_ronda(self, chat_id: str, indices: list[int]) -> None:
        self.click(f'#p-parent .view.list .row[data-id="{chat_id}"]', pause=2.2)
        if len(indices) == 1:
            self.click(f'#p-parent .view.convo .fbtn[data-fwd="{indices[0]}"]', pause=1.0)
        else:
            for i in indices:
                self.click(f'#p-parent .view.convo .lrow[data-i="{i}"]', pause=0.35)
            self.pause(0.4)
            self.click("#p-parent .view.convo .fwdsel", pause=1.0)
        self.click('#p-parent .picker .row[data-to="ronda"]', pause=0.6)

    def back_to_list(self) -> None:
        self.click("#p-parent > .header svg.tap", pause=1.2)


def title_card(page, d) -> None:
    page.evaluate("""window.__card(`<div class="shield">🛡️</div><h1>Ronda</h1>
        <p>Asisten cek penipuan untuk orang tua di Indonesia</p>
        <small>Live demo · Muse Spark · Muse Wonder · Muse Voice · SAM 3.1</small>`)""")
    page.evaluate("document.getElementById('setup').click()")  # fresh session: Mama + guardian Hans
    page.wait_for_timeout(1500)
    page.evaluate("document.body.classList.add('clean')")
    d.pause(3.2)
    page.evaluate("window.__hideCard()")
    d.pause(1.0)


def end_card(page, d, tagline: str) -> None:
    page.evaluate("document.getElementById('__cap').style.opacity = 0")  # no step caption over the end card
    page.evaluate("(t) => window.__card(`<div class=\"shield\">🛡️</div><h1>Ronda</h1><p>${t}</p>"
                  "<small>Telegram + WhatsApp · Muse Spark · Muse Wonder · Muse Voice · SAM 3.1</small>`)", tagline)
    d.pause(4)


def story(page, d) -> None:
    """Follows the voiceover: fake police video call -> BAHAYA + family alert -> "udah transfer" -> QRIS."""
    title_card(page, d)

    d.caption("1 · Polisi palsu", "A “police” video call", "Fake uniform, fake office: “jangan beritahu siapa pun”.")
    d.pause(1.2)
    before = d.verdicts()
    d.forward_to_ronda("polisi", [0, 1])
    d.caption("1 · Polisi palsu", "Forward it to Ronda", "Muse Voice listens to the call, Muse Spark reasons, Muse Wonder double-checks videos.")
    d.wait_verdict(before, timeout=40)
    d.caption("1 · Polisi palsu", "BAHAYA: fake police",
              "Real police never ask for money or investigate over a video call. The family is alerted right away →")
    d.pause(7)

    d.caption("2 · Terlanjur", "“aduh aku udah transfer 2 juta”", "Scammers want her alone. Ronda does the opposite.")
    before = d.bot_messages()
    d.type("#p-parent > .composer textarea", "aduh aku udah transfer 2 juta")
    d.wait_bot_reply(before)
    d.caption("2 · Terlanjur", "Emergency steps by Muse Wonder",
              "Call the bank, report to IASC within 12 hours. Hans gets an urgent alert →")
    d.pause(7)

    d.caption("3 · QRIS", "A QRIS sticker at the warung", "Mama takes a photo before paying.")
    d.page.mouse.move(*_center(page, "#p-parent > .composer .clip"), steps=25)
    d.pause(0.6)
    before = d.verdicts()
    page.evaluate("SCENARIOS.qris_warung()")
    d.caption("3 · QRIS", "SAM 3.1 finds the QR code", "Tiny and blurry in a big photo: SAM crops it, then the QR is decoded.")
    d.wait_verdict(before, timeout=40)
    d.caption("3 · QRIS", "BAHAYA: wrong name", "The sticker says Warung Bu Sri, the QR pays ANDI CELL.")
    d.pause(6)

    end_card(page, d, "Cek dulu, sebelum terlambat.")


def tour(page, d) -> None:
    """Five flows: nomor baru, already paid, APK in the family group, fake BRI letter, unknown opener."""
    title_card(page, d)

    d.caption("1 · Nomor baru", "“Ma, ini aku, ganti nomor”",
              "A new number pretends to be her son and asks for Rp2 juta, “jangan bilang Papa”.")
    d.pause(1.5)
    before = d.verdicts()
    d.forward_to_ronda("nomor_baru", [0, 1, 2])
    d.caption("1 · Nomor baru", "Forward to Ronda", "Muse Spark checks it, with a safe-word check.")
    d.wait_verdict(before)
    d.caption("1 · Nomor baru", "BAHAYA, in Mama's words",
              "Calls her “Mama”, names Hans, flags the friend's account. Hans gets an alert right away →")
    d.pause(6)

    d.caption("2 · Panik", "“aduh aku udah transfer 2 juta”", "Mama replies in her own words.")
    before = d.bot_messages()
    d.type("#p-parent > .composer textarea", "aduh aku udah transfer 2 juta")
    d.wait_bot_reply(before)
    d.caption("2 · Panik", "Emergency steps",
              "Muse Wonder writes the reply; bank + IASC (<12 jam) are added by code. Hans gets an urgent alert →")
    d.pause(6.5)

    d.back_to_list()
    d.caption("3 · Grup keluarga", "A wedding invitation… as an .apk",
              "An unknown member posts “Kami harap kehadirannya” with an Android app.")
    d.pause(1.2)
    before = d.verdicts()
    d.forward_to_ronda("keluarga", [4])
    d.wait_verdict(before)
    d.caption("3 · Grup keluarga", "APK = always BAHAYA", "Plus what to do if it was already installed.")
    d.pause(5)

    d.back_to_list()
    d.caption("4 · “BRI Care”", "Tarif transfer Rp150 ribu/bulan",
              "The real-world letter template: nomor surat, pilih 1/2, “dianggap setuju”, a form link.")
    d.pause(1.2)
    before = d.verdicts()
    d.forward_to_ronda("tarif_bri", [0])
    d.caption("4 · “BRI Care”", "Muse calls check_link", "Follows the link and checks the domain.")
    d.wait_verdict(before)
    d.pause(5)

    d.back_to_list()
    d.caption("5 · Nomor asing", "“Aku menemukan kontakmu…”", "A friendly opener from an unknown number.")
    d.pause(1.2)
    before = d.verdicts()
    d.forward_to_ronda("kontak", [0])
    d.wait_verdict(before)
    d.caption("5 · Nomor asing", "WASPADA, not BAHAYA", "“Jangan dibalas”: replying tells scammers the number is active.")
    d.pause(5)

    end_card(page, d, "Teruskan dulu ke Ronda, baru transfer.")


def _center(page, selector: str) -> tuple[float, float]:
    box = page.locator(selector).first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


FLOWS = {"story": (story, "ronda_story.mp4"), "tour": (tour, "ronda_demo.mp4")}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "story"
    flow, filename = FLOWS[name]
    OUT.mkdir(exist_ok=True)
    raw = OUT / "raw"
    shutil.rmtree(raw, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(raw),
                                  record_video_size={"width": W, "height": H}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(f"{BASE}/wa")
        page.wait_for_selector("#p-parent .view.list .row")
        page.evaluate(OVERLAY)
        flow(page, Demo(page))
        video = page.video.path()
        ctx.close()
        browser.close()

    mp4 = OUT / filename
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(mp4)], check=True)
    shutil.rmtree(raw, ignore_errors=True)
    print("saved", mp4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
