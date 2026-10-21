"""
Greentek Scraper - the app that runs on each scraper PC
=======================================================
Each PC has its own Start and Stop buttons. Pressing Start joins today's
run on the Source Optimizer server (or opens a new one). The PC then takes
batches of products that no other PC is working on, checks them on Amazon,
and sends every result straight to the server.

PCs never wait for each other. If this PC stops or crashes, the products it
was holding go back to the pool for the other PCs.

Start it with start_scraper.bat
"""

import argparse
import asyncio
import json
import os
import queue
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "scraper_settings.json"

DEFAULTS = {
    "server": "http://localhost:8000",
    "scraper_key": "",
    "pc_name": socket.gethostname(),
    "windows": 2,
    "browser": "chrome",          # chrome, msedge or chromium
    "headless": False,
    "light_mode": True,
    "page_timeout_seconds": 45,
    "captcha_wait_minutes": 10,
    "amazon_home": "https://www.amazon.com/",
}

PRICE_SELECTORS = [
    "#corePrice_feature_div .a-price .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    "#apex_offerDisplay_desktop .a-price .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    "#price_inside_buybox",
    "#newBuyBoxPrice",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
    "#priceblock_saleprice",
]
DELIVERY_PRIMARY = "#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE"
DELIVERY_SECONDARY = "#mir-layout-DELIVERY_BLOCK-slot-SECONDARY_DELIVERY_MESSAGE_LARGE"
DELIVERY_FALLBACKS = ["#mir-layout-DELIVERY_BLOCK", "#deliveryBlockMessage", "#delivery-block-ags-dcp-container_0"]


def load_settings():
    s = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    if not s.get("agent_id"):
        s["agent_id"] = "pc-" + uuid.uuid4().hex[:12]
        save_settings(s)
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception:
        pass


def clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("‎", "")).strip()


CODE_MARKERS = re.compile(r"\(function\s*\(|P\.when\(|\{\s*var\s|window\.|document\.|=>\s*\{")


def strip_code(text):
    """Cut off any leftover JavaScript that slipped into a text block."""
    m = CODE_MARKERS.search(text or "")
    return clean(text[:m.start()]) if m else clean(text)


class ServerError(Exception):
    pass


def http_post(server, path, body, timeout=30, key=""):
    url = server.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Agent-Key": key,
                                          "User-Agent": "GreentekScraper/1.1"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:
            msg = str(e)
        raise ServerError(msg)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ConnectionError(f"Can't reach the server at {server}") from e


def keep_awake(on):
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


# ---------------------------------------------------------------- page reading
async def is_captcha(page):
    url = page.url.lower()
    if "validatecaptcha" in url or "/errors/" in url:
        return True
    try:
        if await page.locator("form[action*='validateCaptcha']").count():
            return True
        body = clean(await page.locator("body").inner_text(timeout=3000))
        return "Enter the characters you see below" in body or "Type the characters you see in this image" in body
    except Exception:
        return False


async def continue_shopping_gate(page):
    try:
        btn = page.locator("button:has-text('Continue shopping'), input[value*='Continue shopping' i]")
        if await btn.count():
            await btn.first.click()
            await page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass


async def first_text(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count():
                # Text only: leave out <script>/<style> code that Amazon puts inside some blocks
                t = clean(await loc.first.evaluate(
                    "el => { const c = el.cloneNode(true);"
                    " c.querySelectorAll('script,style,noscript,template').forEach(n => n.remove());"
                    " return c.textContent; }", timeout=1500))
                t = strip_code(t)
                if t:
                    return t
        except Exception:
            continue
    return ""


# Reads the variation Amazon shows as selected on the page, in the catalog's own style:
#     Color: Beige/Size: 18" x 18" (Pack of 2)        (one "Label: value" per option, joined with "/")
# Three page layouts exist: the current one-row-per-option layout, the classic swatch layout and drop-downs.
VARIATION_JS = r"""() => {
  const clean = (s) => (s || '').replace(/[‎‏ ]/g, ' ').replace(/\s+/g, ' ').trim();
  const pairs = [];
  const add = (label, value) => {
    label = clean(label).replace(/\s*:\s*$/, '');
    value = clean(value);
    if (!label || !value || /^(select|choose|please select)\b/i.test(value)) return;
    if (!pairs.some((p) => p[0].toLowerCase() === label.toLowerCase())) pairs.push([label, value]);
  };
  // 1. current layouts (several options, or a single one): a "Color:" label next to the selected value
  document.querySelectorAll('[id^="inline-twister-expanded-dimension-text-"]').forEach((val) => {
    const holder = val.parentElement;
    const lab = holder && holder.querySelector('.a-color-secondary');
    if (lab) add(lab.textContent, val.textContent);
    else if (holder) { const m = clean(holder.textContent).match(/^([^:]+):\s*(.+)$/); if (m) add(m[1], m[2]); }
  });
  if (!pairs.length) document.querySelectorAll('[id^="inline-twister-row-"]').forEach((row) => {
    const title = row.querySelector('[id^="inline-twister-dim-title-"]');
    if (!title) return;
    const m = clean(title.textContent).match(/^([^:]+):\s*(.+)$/);
    if (m) add(m[1], m[2]);
  });
  if (!pairs.length) document.querySelectorAll('#twister_feature_div [id^="variation_"], #twister [id^="variation_"]').forEach((box) => {
    const lab = box.querySelector('label'); const sel = box.querySelector('.selection');
    if (lab && sel) add(lab.textContent, sel.textContent);
  });
  if (!pairs.length) document.querySelectorAll('#twister_feature_div select[name^="dropdown_selected_"], #twister select[name^="dropdown_selected_"]').forEach((sel) => {
    const opt = sel.options[sel.selectedIndex]; if (!opt) return;
    const box = sel.closest('[id^="variation_"]'); const lab = box && box.querySelector('label');
    if (lab) add(lab.textContent, opt.textContent);
  });
  // Last resort: the page's own data lists every option and marks the selected one ("color_name" -> "Color")
  if (!pairs.length) document.querySelectorAll('script[type="a-state"]').forEach((s) => {
    if (!s.textContent.includes('sortedDimValuesForAllDims')) return;
    try {
      const dims = JSON.parse(s.textContent).sortedDimValuesForAllDims || {};
      Object.keys(dims).forEach((key) => {
        const hit = (dims[key] || []).find((v) => v.dimensionValueState === 'SELECTED');
        if (hit) add(key.replace(/_name$/, '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()), hit.dimensionValueDisplayText);
      });
    } catch (e) { /* unreadable: leave it */ }
  });
  // Pages without options still carry an EMPTY #twister_feature_div; only a container with content counts, so
  // products without variations don't make the scraper wait.
  const boxes = [...document.querySelectorAll('#twister_feature_div, #twister, #twisterContainer')];
  const hasTwister = boxes.some((b) => b.children.length > 0) || !!document.querySelector('[id^="inline-twister-row-"], [id^="variation_"]');
  return { text: pairs.map((p) => p[0] + ': ' + p[1]).join('/'), hasTwister };
}"""


# The main product image Amazon shows (it follows the selected variation). Only the address is read, so
# the scraper's "light mode" (which does not download images) is unaffected. The server tidies the address.
IMAGE_JS = r"""() => {
  const bad = (u) => !u || !/^https?:\/\//i.test(u) || /transparent-pixel|grey-pixel|loading|spinner|sprite|blank/i.test(u);
  const largest = (el) => {
    try {
      const m = JSON.parse(el.getAttribute('data-a-dynamic-image') || '{}');
      return Object.keys(m).sort((a, b) => m[b][0] * m[b][1] - m[a][0] * m[a][1])[0];
    } catch (e) { return ''; }
  };
  const cands = [];
  document.querySelectorAll('#landingImage, #imgBlkFront, #main-image, #ebooksImgBlkFront, #imgTagWrapperId img').forEach((el) => {
    cands.push(el.getAttribute('data-old-hires'), largest(el), el.getAttribute('src'));
  });
  const og = document.querySelector('meta[property="og:image"]');
  if (og) cands.push(og.content);
  const url = cands.find((u) => !bad(u)) || '';
  const hasBlock = !!document.querySelector('#imgTagWrapperId, #imageBlock, #main-image-container, #landingImage');
  return { url, hasBlock };
}"""


async def read_image(page):
    """The main image address, or None when it could not be read (so the server keeps what it has)."""
    try:
        for _ in range(4):                      # the image tag can appear a moment after the title
            r = await page.evaluate(IMAGE_JS)
            if r["url"]:
                return r["url"]
            if not r["hasBlock"]:
                return None
            await asyncio.sleep(0.5)
        return None
    except Exception:
        return None


async def read_variation(page):
    """The selected variation text, '' when the product has no variations, None when it could not be read
    (so the server keeps what it already has instead of blanking it)."""
    try:
        for _ in range(4):                      # the option rows can appear a moment after the title
            r = await page.evaluate(VARIATION_JS)
            if r["text"] or not r["hasTwister"]:
                return r["text"]
            await asyncio.sleep(0.6)
        return ""
    except Exception:
        return None


async def extract(page):
    title = await first_text(page, ["#productTitle"])
    price = await first_text(page, PRICE_SELECTORS)
    m = re.search(r"\$[\d,]+(?:\.\d{2})?", price)
    price = m.group(0) if m else ""
    availability = await first_text(page, ["#availability span", "#availability"])
    if not availability and await page.locator("#outOfStock, #outOfStockBuyBox_feature_div").count():
        availability = "Currently unavailable."
    primary = await first_text(page, [DELIVERY_PRIMARY])
    secondary = await first_text(page, [DELIVERY_SECONDARY])
    delivery = "    ".join(x for x in (primary, secondary) if x) or await first_text(page, DELIVERY_FALLBACKS)
    delivery = re.sub(r"\s*Details\s*$", "", delivery)
    return {"title": title, "price": price or "Price not found",
            "availability": availability or "Availability not found",
            "delivery": delivery or "Delivery not found",
            "variation": await read_variation(page),
            "image": await read_image(page)}


# ---------------------------------------------------------------- the scraper engine
class Engine:
    def __init__(self, settings, emit):
        self.s = settings
        self.emit = emit                 # emit(kind, data) -> the window
        self.thread = None
        self.stop_flag = threading.Event()
        self.running = False
        self.reset_stats()

    def reset_stats(self):
        self.done = 0
        self.captchas = 0
        self.failed = 0
        self.started = None
        self.recent = deque()           # timestamps of finished products, for speed
        self.state = "stopped"
        self.message = ""

    # -- controls (called from the window) --
    def start(self, confirm_new=False):
        if self.running:
            return
        self.stop_flag.clear()
        self.running = True
        self.thread = threading.Thread(target=self._thread_main, args=(confirm_new,), daemon=True)
        self.thread.start()

    def stop(self):
        if self.running:
            self.stop_flag.set()
            self.set_state("stopping", "Finishing the current product, then stopping…")

    # -- helpers --
    def log(self, text):
        self.emit("log", f"{datetime.now():%H:%M:%S}  {text}")

    def set_state(self, state, message=None):
        self.state = state
        if message is not None:
            self.message = message
        self.emit("state", (state, self.message))

    def rate(self):
        now = time.time()
        while self.recent and now - self.recent[0] > 600:
            self.recent.popleft()
        if not self.started:
            return 0
        window = min(600, now - self.started)
        return len(self.recent) / window * 3600 if window > 30 else 0

    def push_stats(self):
        self.emit("stats", {"done": self.done, "captchas": self.captchas, "failed": self.failed, "rate": self.rate()})

    async def post(self, path, body, timeout=30):
        return await asyncio.to_thread(http_post, self.s["server"], path, body, timeout, self.s.get("scraper_key", ""))

    def base(self):
        return {"agent_id": self.s["agent_id"], "name": self.s["pc_name"]}

    # -- thread entry --
    def _thread_main(self, confirm_new):
        keep_awake(True)
        try:
            asyncio.run(self._main(confirm_new))
        except Exception as e:
            self.log(f"Stopped because of an error: {e}")
        finally:
            keep_awake(False)
            self.running = False
            self.set_state("stopped", "Stopped")
            self.emit("stopped", None)

    async def _main(self, confirm_new):
        self.set_state("starting", "Connecting to the server…")
        try:
            r = await self.post("/api/agent/start", {**self.base(), "workers": int(self.s["windows"]),
                                                     "confirm_new": confirm_new})
        except (ConnectionError, ServerError) as e:
            self.log(str(e) + (". Check the server address and that the server is running." if isinstance(e, ConnectionError) else ""))
            return
        if r.get("action") == "confirm":
            self.emit("confirm", r.get("message"))
            return
        self.run_id = r["run_id"]
        self.conf = r
        if int(self.s["windows"]) > 3:
            self.log(f"Using {self.s['windows']} browser windows. If CAPTCHAs keep coming up, lower this to 2 or 3.")
        self.log(("Opened a new run" if r["action"] == "created" else "Joined the run in progress") +
                 f" (#{self.run_id}, {r['total']:,} products). Delivery ZIP {r['zip_code']}.")
        self.reset_stats()
        self.started = time.time()
        self.buffer = deque()
        self.unsent = []
        self.run_complete = False
        self.claim_lock = asyncio.Lock()
        self.pause_factor = 1.0
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log("Playwright is missing. Run setup.bat first.")
            return
        async with async_playwright() as pw:
            contexts = []
            try:
                for n in range(1, int(self.s["windows"]) + 1):
                    if self.stop_flag.is_set():
                        break
                    ctx = await self.open_browser(pw, n)
                    if ctx:
                        contexts.append((n, ctx))
                if not contexts:
                    self.log("No browser window could be opened.")
                    return
                self.set_state("running", f"Checking products with {len(contexts)} browser window(s)")
                hb = asyncio.create_task(self.heartbeat_loop())
                await asyncio.gather(*(self.worker(n, ctx) for n, ctx in contexts))
                hb.cancel()
            finally:
                await self.shutdown()
                for _, ctx in contexts:
                    try:
                        await ctx.close()
                    except Exception:
                        pass

    async def open_browser(self, pw, n):
        profile = HERE / "profiles" / f"window{n}"
        profile.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=bool(self.s["headless"]), viewport={"width": 1280, "height": 860},
                    locale="en-US", chromium_sandbox=(os.name == "nt"), ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled",
                          f"--window-position={40 * (n - 1)},{40 * (n - 1)}"])
        channels = [self.s["browser"]] + [c for c in ("chrome", "msedge", "chromium") if c != self.s["browser"]]
        last = None
        for ch in channels:
            try:
                if ch == "chromium":
                    ctx = await pw.chromium.launch_persistent_context(str(profile), **opts)
                else:
                    ctx = await pw.chromium.launch_persistent_context(str(profile), channel=ch, **opts)
                break
            except Exception as e:
                last = e
                ctx = None
        if not ctx:
            self.log(f"Window {n}: couldn't open Chrome or Edge ({str(last).splitlines()[0][:100]}).")
            return None
        if self.s["light_mode"]:
            async def block(route):
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await ctx.route("**/*", block)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await self.set_zip(page, n)
        return ctx

    async def set_zip(self, page, n):
        zip_code = self.conf["zip_code"]
        try:
            await page.goto(self.s["amazon_home"], wait_until="domcontentloaded",
                            timeout=self.s["page_timeout_seconds"] * 1000)
            await continue_shopping_gate(page)
            if zip_code in await first_text(page, ["#glow-ingress-line2"]):
                self.log(f"Window {n}: delivery ZIP is {zip_code}.")
                return
            await page.locator("#nav-global-location-popover-link, #glow-ingress-block").first.click(timeout=10000)
            await page.locator("#GLUXZipUpdateInput").fill(zip_code, timeout=10000)
            await page.locator("#GLUXZipUpdate input, #GLUXZipUpdate").first.click()
            await page.wait_for_timeout(2500)
            for sel in ["#GLUXConfirmClose", "button[name='glowDoneButton']", ".a-popover-footer input.a-button-input"]:
                loc = page.locator(sel)
                if await loc.count():
                    try:
                        await loc.first.click(timeout=3000)
                        break
                    except Exception:
                        pass
            await page.wait_for_timeout(1500)
            await page.reload(wait_until="domcontentloaded")
            if zip_code in await first_text(page, ["#glow-ingress-line2"]):
                self.log(f"Window {n}: delivery ZIP set to {zip_code}.")
                return
        except Exception:
            pass
        self.log(f"Window {n}: couldn't set ZIP {zip_code} automatically. In that browser window click "
                 f"'Deliver to' (top left), enter {zip_code} and click Apply. Scraping continues meanwhile.")

    async def next_item(self):
        async with self.claim_lock:
            waited = False
            while not self.stop_flag.is_set():
                if self.buffer:
                    return self.buffer.popleft()
                try:
                    r = await self.post("/api/agent/claim", {**self.base(), "run_id": self.run_id,
                                                             "n": 10 * int(self.s["windows"])})
                except ConnectionError as e:
                    self.set_state("running", f"{e}. Retrying in 15 seconds…")
                    await self.sleep(15)
                    continue
                except ServerError as e:
                    self.log(f"Server error: {e}")
                    await self.sleep(15)
                    continue
                if r.get("progress"):
                    self.emit("progress", r["progress"])
                if r["items"]:
                    self.buffer.extend(r["items"])
                    if waited:
                        self.set_state("running", "Checking products")
                    continue
                if r["run_state"] != "open":
                    if not getattr(self, "run_complete", False):
                        self.run_complete = True
                        self.log("The run is complete. Every product has been checked.")
                    return None
                if not waited:
                    self.set_state("running", "Waiting: other PCs are finishing the last products")
                    waited = True
                await self.sleep(20)
            return None

    async def sleep(self, secs):
        end = time.time() + secs
        while time.time() < end and not self.stop_flag.is_set():
            await asyncio.sleep(min(1, end - time.time()))

    async def worker(self, n, ctx):
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        while not self.stop_flag.is_set():
            item = await self.next_item()
            if not item:
                break
            res = await self.scrape(page, item, n)
            await self.send_result(res)
            self.push_stats()
            pause = random.uniform(self.conf["delay_min_seconds"], self.conf["delay_max_seconds"]) * self.pause_factor
            await self.sleep(pause)

    async def scrape(self, page, item, n):
        t0 = time.time()
        out = {"title": "", "price": "", "availability": "", "delivery": ""}
        result = "error"
        timeout = self.s["page_timeout_seconds"] * 1000
        for attempt in (1, 2):
            try:
                resp = await page.goto(item["link"], wait_until="domcontentloaded", timeout=timeout)
                await continue_shopping_gate(page)
                if await is_captcha(page):
                    self.captchas += 1
                    self.pause_factor = min(self.pause_factor * 2, 16)
                    if self.s["headless"]:
                        result = "captcha"
                        break
                    self.set_state("captcha", f"CAPTCHA in window {n}. Type the characters in that browser window.")
                    self.log(f"Window {n}: CAPTCHA on {item['sku']}. Waiting up to {self.s['captcha_wait_minutes']} min for someone to solve it.")
                    self.emit("alert", n)
                    deadline = time.time() + self.s["captcha_wait_minutes"] * 60
                    while time.time() < deadline and not self.stop_flag.is_set() and await is_captcha(page):
                        await asyncio.sleep(3)
                    if await is_captcha(page):
                        result = "captcha"
                        self.set_state("running", "Checking products")
                        break
                    self.set_state("running", "Checking products")
                    resp = await page.goto(item["link"], wait_until="domcontentloaded", timeout=timeout)
                status = resp.status if resp else 0
                if status != 404:
                    try:
                        await page.wait_for_selector("#productTitle, #availability, #dp", timeout=8000)
                    except Exception:
                        pass
                if status == 404 or not await page.locator("#productTitle, #dp").count():
                    out.update({"price": "Price not found", "availability": "Unable to access page",
                                "delivery": "Delivery not found"})
                    result = "unavailable_page"
                    break
                out = await extract(page)
                result = "ok"
                break
            except Exception as e:
                result = "timeout" if "Timeout" in type(e).__name__ else "error"
                if attempt == 2:
                    self.log(f"Window {n}: {item['sku']} failed ({str(e).splitlines()[0][:90]})")
            if attempt == 1:
                await self.sleep(6)
        if result in ("ok", "unavailable_page"):
            self.done += 1
            self.recent.append(time.time())
            self.pause_factor = max(1.0, self.pause_factor * 0.97)
        else:
            self.failed += 1
        res = {**self.base(), "run_id": self.run_id, "sku": item["sku"], "result": result,
               "price": out["price"], "availability": out["availability"], "delivery": out["delivery"],
               "title": out["title"], "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "seconds": round(time.time() - t0, 1)}
        if out.get("variation") is not None:         # only when the page was read: never blank an existing value by accident
            res["variation"] = out["variation"]
        if out.get("image"):
            res["image"] = out["image"]
        return res

    async def send_result(self, res):
        try:
            await self.post("/api/agent/result", res)
        except (ConnectionError, ServerError):
            self.unsent.append(res)

    async def flush_unsent(self):
        while self.unsent:
            try:
                await self.post("/api/agent/result", self.unsent[0])
                self.unsent.pop(0)
            except (ConnectionError, ServerError):
                return False
        return True

    async def heartbeat_loop(self):
        while True:
            try:
                await self.flush_unsent()
                r = await self.post("/api/agent/heartbeat", {
                    **self.base(), "state": self.state if self.state in ("running", "captcha") else "running",
                    "workers": int(self.s["windows"]), "run_id": self.run_id, "done": self.done,
                    "captchas": self.captchas, "rate": round(self.rate()), "message": self.message}, timeout=15)
                if r.get("progress"):
                    self.emit("progress", r["progress"])
            except Exception:
                pass
            self.push_stats()
            await asyncio.sleep(15)

    async def shutdown(self):
        if not await self.flush_unsent():
            self.log(f"{len(self.unsent)} results couldn't be sent because the server is unreachable.")
            try:
                (HERE / "unsent_results.json").write_text(json.dumps(self.unsent), encoding="utf-8")
                self.log("They were saved to unsent_results.json.")
            except Exception:
                pass
        try:
            await self.post("/api/agent/release", {**self.base(), "run_id": self.run_id, "state": "stopped",
                                                   "done": self.done, "captchas": self.captchas,
                                                   "message": ("Run complete. " if self.run_complete else "") +
                                                              f"Checked {self.done:,} products this session"}, timeout=15)
        except Exception:
            pass
        self.buffer.clear()
        self.log(f"Stopped. Checked {self.done:,} products this session, {self.captchas} CAPTCHAs.")


# ---------------------------------------------------------------- the window
def run_gui():
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    s = load_settings()
    events = queue.Queue()
    engine = Engine(s, lambda kind, data: events.put((kind, data)))

    GREEN, RED, INK, MUTED, BG, AMBER = "#1D6A4F", "#B3362B", "#17211C", "#5B6961", "#F3F5F2", "#9A6410"
    root = tk.Tk()
    root.title(f"Greentek Scraper - {s['pc_name']}")
    root.configure(bg=BG)
    root.geometry("640x680")
    root.minsize(520, 520)
    F = ("Segoe UI", 10)

    top = tk.Frame(root, bg=BG, padx=16, pady=12)
    top.pack(fill="x")
    tk.Label(top, text="Greentek Scraper", font=("Segoe UI Semibold", 15), bg=BG, fg=INK).grid(row=0, column=0, columnspan=4, sticky="w")

    tk.Label(top, text="This PC's name", font=F, bg=BG, fg=MUTED).grid(row=1, column=0, sticky="w", pady=(10, 2))
    name_var = tk.StringVar(value=s["pc_name"])
    tk.Entry(top, textvariable=name_var, font=F, width=22).grid(row=2, column=0, sticky="we", padx=(0, 10))
    tk.Label(top, text="Server address", font=F, bg=BG, fg=MUTED).grid(row=1, column=1, sticky="w", pady=(10, 2))
    server_var = tk.StringVar(value=s["server"])
    tk.Entry(top, textvariable=server_var, font=F, width=28).grid(row=2, column=1, sticky="we", padx=(0, 10))
    tk.Label(top, text="Scraper key (from the app: Scraper > Settings)", font=F, bg=BG, fg=MUTED).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 2))
    key_var = tk.StringVar(value=s.get("scraper_key", ""))
    tk.Entry(top, textvariable=key_var, font=F, show="•").grid(row=4, column=0, columnspan=2, sticky="we", padx=(0, 10))
    tk.Label(top, text="Browser windows", font=F, bg=BG, fg=MUTED).grid(row=1, column=2, sticky="w", pady=(10, 2))
    win_var = tk.StringVar(value=str(s["windows"]))
    tk.Spinbox(top, from_=1, to=6, textvariable=win_var, width=4, font=F, state="readonly").grid(row=2, column=2, sticky="w")
    tk.Label(top, text="2 is safest. 4-6: watch\nthe CAPTCHA count.", font=("Segoe UI", 8), bg=BG, fg=MUTED,
             justify="left").grid(row=3, column=2, rowspan=2, sticky="nw", pady=(8, 0))
    top.grid_columnconfigure(1, weight=1)

    btns = tk.Frame(root, bg=BG, padx=16)
    btns.pack(fill="x")
    start_btn = tk.Button(btns, text="Start", font=("Segoe UI Semibold", 13), bg=GREEN, fg="white",
                          activebackground=GREEN, activeforeground="white", relief="flat", padx=26, pady=8)
    stop_btn = tk.Button(btns, text="Stop", font=("Segoe UI Semibold", 13), bg="white", fg=RED,
                         activebackground="white", relief="solid", bd=1, padx=26, pady=7, state="disabled")
    start_btn.pack(side="left")
    stop_btn.pack(side="left", padx=10)

    status_var = tk.StringVar(value="Stopped. Press Start to begin checking products.")
    status_lbl = tk.Label(root, textvariable=status_var, font=("Segoe UI Semibold", 11), bg=BG, fg=MUTED,
                          anchor="w", justify="left", wraplength=580, padx=16, pady=10)
    status_lbl.pack(fill="x")

    stats = tk.Frame(root, bg="white", padx=16, pady=10, highlightthickness=1, highlightbackground="#D8DFD8")
    stats.pack(fill="x", padx=16)
    stat_vars = {}
    for i, (k, label) in enumerate([("done", "Checked on this PC"), ("rate", "Speed (per hour)"),
                                    ("captchas", "CAPTCHAs"), ("run", "Whole run (all PCs)")]):
        tk.Label(stats, text=label, font=("Segoe UI", 9), bg="white", fg=MUTED).grid(row=0, column=i, sticky="w", padx=(0, 18))
        v = tk.StringVar(value="—")
        tk.Label(stats, textvariable=v, font=("Segoe UI Semibold", 14), bg="white", fg=INK).grid(row=1, column=i, sticky="w", padx=(0, 18))
        stat_vars[k] = v

    tk.Label(root, text="Activity", font=("Segoe UI", 9), bg=BG, fg=MUTED, anchor="w", padx=16).pack(fill="x", pady=(12, 2))
    log_box = scrolledtext.ScrolledText(root, height=12, font=("Consolas", 9), state="disabled", relief="flat",
                                        highlightthickness=1, highlightbackground="#D8DFD8")
    log_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    def add_log(text):
        log_box.configure(state="normal")
        log_box.insert("end", text + "\n")
        lines = int(log_box.index("end-1c").split(".")[0])
        if lines > 400:
            log_box.delete("1.0", f"{lines - 400}.0")
        log_box.see("end")
        log_box.configure(state="disabled")

    def set_inputs(enabled):
        for w in top.winfo_children():
            if isinstance(w, (tk.Entry, tk.Spinbox)):
                w.configure(state=("normal" if enabled else "disabled") if isinstance(w, tk.Entry)
                            else ("readonly" if enabled else "disabled"))

    def do_start(confirm_new=False):
        server = server_var.get().strip()
        if not server.startswith("http"):
            server = "http://" + server
        s.update({"server": server, "pc_name": name_var.get().strip() or socket.gethostname(),
                  "windows": int(win_var.get()), "scraper_key": key_var.get().strip()})
        server_var.set(server)
        save_settings(s)
        root.title(f"Greentek Scraper - {s['pc_name']}")
        start_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        set_inputs(False)
        engine.start(confirm_new)

    def do_stop():
        engine.stop()
        stop_btn.configure(state="disabled")

    start_btn.configure(command=do_start)
    stop_btn.configure(command=do_stop)

    def poll():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "log":
                    add_log(data)
                elif kind == "state":
                    state, msg = data
                    color = {"running": GREEN, "captcha": AMBER, "starting": INK, "stopping": INK}.get(state, MUTED)
                    label = {"running": "Running", "captcha": "Needs attention", "starting": "Starting",
                             "stopping": "Stopping", "stopped": "Stopped"}.get(state, state)
                    status_var.set(f"{label}. {msg}" if msg and msg != label else label)
                    status_lbl.configure(fg=color)
                elif kind == "stats":
                    stat_vars["done"].set(f"{data['done']:,}")
                    stat_vars["rate"].set(f"{data['rate']:,.0f}" if data["rate"] else "—")
                    stat_vars["captchas"].set(f"{data['captchas']:,}")
                elif kind == "progress":
                    done = data.get("done", 0) + data.get("failed", 0)
                    stat_vars["run"].set(f"{done:,} / {data.get('total', 0):,}")
                elif kind == "alert":
                    try:
                        root.bell()
                        root.deiconify()
                        root.lift()
                    except Exception:
                        pass
                elif kind == "confirm":
                    if messagebox.askyesno("Start a new full run?", data, parent=root):
                        root.after(300, lambda: do_start(True))
                elif kind == "stopped":
                    start_btn.configure(state="normal")
                    stop_btn.configure(state="disabled")
                    set_inputs(True)
        except queue.Empty:
            pass
        root.after(300, poll)

    def on_close():
        if engine.running:
            if not messagebox.askyesno("Stop and close?", "The scraper is running. Stop it and close this window?", parent=root):
                return
            engine.stop()
            status_var.set("Stopping, then closing…")

            def wait_close():
                if engine.running:
                    root.after(500, wait_close)
                else:
                    root.destroy()
            wait_close()
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    add_log(f"Ready. Server: {s['server']}")
    root.after(300, poll)
    root.mainloop()


# ---------------------------------------------------------------- no-window mode (for testing)
def run_cli(args):
    s = load_settings()
    for k in ("server", "pc_name", "windows", "browser", "amazon_home", "scraper_key"):
        v = getattr(args, k, None)
        if v is not None:
            s[k] = v
    if args.headless:
        s["headless"] = True
    if args.agent_id:
        s["agent_id"] = args.agent_id
    done_evt = threading.Event()

    def emit(kind, data):
        if kind == "log":
            print(f"[{s['pc_name']}] {data}", flush=True)
        elif kind == "confirm":
            print(f"[{s['pc_name']}] CONFIRM: {data}", flush=True)
        elif kind == "stopped":
            done_evt.set()

    eng = Engine(s, emit)
    eng.start(confirm_new=args.confirm_new)
    try:
        while not done_evt.wait(0.5):
            if args.stop_after and eng.done >= args.stop_after:
                eng.stop()
    except KeyboardInterrupt:
        eng.stop()
        done_evt.wait(60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--nogui", action="store_true")
    ap.add_argument("--server")
    ap.add_argument("--pc-name", dest="pc_name")
    ap.add_argument("--windows", type=int)
    ap.add_argument("--browser")
    ap.add_argument("--amazon-home", dest="amazon_home")
    ap.add_argument("--agent-id", dest="agent_id")
    ap.add_argument("--key", dest="scraper_key")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--confirm-new", dest="confirm_new", action="store_true")
    ap.add_argument("--stop-after", dest="stop_after", type=int, default=0)
    a = ap.parse_args()
    if a.nogui:
        run_cli(a)
    else:
        run_gui()
