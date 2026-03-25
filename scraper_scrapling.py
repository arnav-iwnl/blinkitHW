#!/usr/bin/env python3
"""
Blinkit Scraper — Scrapling edition
-------------------------------------
Strategy:
  1. Try fast `Fetcher` (curl_cffi TLS spoof, no browser) for each ID.
  2. On Cloudflare block, fall back to `StealthyFetcher` (full browser, solves Turnstile).
  3. Checkpoint every N IDs so you can resume anytime.

Install:
    pip install scrapling
    scrapling install          # downloads browser binaries (one-time)

Run:
    python scraper_scrapling.py --start 100000 --end 200000
    python scraper_scrapling.py --resume
"""

import asyncio
import argparse
import json
import random
import time
from pathlib import Path

from scrapling.fetchers import Fetcher, AsyncFetcher, StealthyFetcher

# ── Config ────────────────────────────────────────────────────────────────────
START_ID         = 100_000
END_ID           = 999_999
KEYWORD          = "hot wheels"
OUTPUT_FILE      = Path("hotwheels_scrapling.json")
CHECKPOINT_FILE  = Path("checkpoint_scrapling.json")

# Concurrent workers for the fast Fetcher path.
# StealthyFetcher fallback is always single-threaded (one browser).
CONCURRENCY      = 5

# Delay between fast-path requests per worker (seconds)
DELAY_MIN        = 1.5
DELAY_MAX        = 3.0

# After this many consecutive blocks, pause the whole run briefly
BLOCK_THRESHOLD  = 5
BLOCK_PAUSE      = 60   # seconds to pause when BLOCK_THRESHOLD hit

# Save every N processed IDs
CHECKPOINT_EVERY = 100

# ── Helpers ───────────────────────────────────────────────────────────────────
_GENERIC = {
    "blinkit.com", "blinkit",
    "home | blinkit", "blinkit - online grocery shopping",
}

def is_generic(name: str) -> bool:
    if not name:
        return True
    n = " ".join(name.strip().lower().split())
    return n in _GENERIC or n.startswith("blinkit")

def extract_from_page(page) -> tuple[str | None, str, str]:
    """
    Works with Scrapling's Response/Selector object.
    Returns (name, price, image_url).
    """
    name  = None
    price = "N/A"
    image = "N/A"

    # ── Name ─────────────────────────────────────────────────────────────────
    # 1. JSON-LD structured data
    for script in page.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text)
            nodes = data if isinstance(data, list) else [data]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                types = node.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any("product" in str(t).lower() for t in types):
                    n = node.get("name") or node.get("title")
                    if n and not is_generic(n):
                        name = str(n).strip()
                    # price from JSON-LD
                    offers = node.get("offers")
                    if offers:
                        offer = offers[0] if isinstance(offers, list) else offers
                        p = offer.get("price")
                        if p is not None:
                            ps = str(p).strip()
                            price = ps if ps.startswith(("₹", "Rs", "INR")) else f"₹{ps}"
                    # image from JSON-LD
                    img = node.get("image")
                    if img:
                        image = img[0] if isinstance(img, list) else str(img)
                    if name:
                        break
        except Exception:
            pass
        if name:
            break

    # 2. og:title fallback
    if not name:
        og = page.css_first("meta[property='og:title']")
        if og:
            n = og.attrib.get("content", "").strip()
            if n and not is_generic(n):
                name = n

    # 3. <h1> fallback
    if not name:
        h1 = page.css_first("h1")
        if h1:
            n = h1.text.strip() if hasattr(h1, "text") else ""
            if n and not is_generic(n):
                name = n

    # ── Image fallback ────────────────────────────────────────────────────────
    if image == "N/A":
        og_img = page.css_first("meta[property='og:image']")
        if og_img:
            image = og_img.attrib.get("content", "N/A").strip()

    # ── Price fallback ────────────────────────────────────────────────────────
    if price == "N/A":
        for sel in [".product-price", "[class*='Price']", "[data-testid='product-price']"]:
            el = page.css_first(sel)
            if el:
                price = el.text.strip() if hasattr(el, "text") else "N/A"
                if price:
                    break

    return name, price, image


def is_blocked(page) -> bool:
    """Detect Cloudflare challenge in a Scrapling response."""
    if page is None:
        return True
    try:
        status = getattr(page, "status", 200)
        if status in (429, 403):
            return True
        body = page.text[:3000].lower() if hasattr(page, "text") else ""
        markers = ["just a moment", "checking your browser", "cf-ray", "__cf_chl"]
        return sum(1 for m in markers if m in body) >= 2
    except Exception:
        return False


# ── Stealthy fallback (synchronous, called in executor) ───────────────────────
def stealthy_fetch(url: str):
    """
    Use StealthyFetcher to solve Cloudflare challenge.
    Runs in a thread executor so it doesn't block the event loop.
    """
    try:
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            solve_cloudflare=True,       # auto-solve Turnstile/Interstitial
            block_webrtc=True,           # prevent IP leaks via WebRTC
            hide_canvas=True,            # anti-fingerprint canvas
            disable_resources=True,      # block images/fonts — faster
            network_idle=True,           # wait until network is quiet
            timeout=40_000,
        )
        return page
    except Exception:
        return None


# ── Worker ────────────────────────────────────────────────────────────────────
async def worker(
    worker_id: int,
    queue: asyncio.Queue,
    results: list,
    processed: set,
    lock: asyncio.Lock,
    stats: dict,
    loop,
):
    delay = random.uniform(DELAY_MIN, DELAY_MAX)

    while True:
        try:
            product_id = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        url = f"https://blinkit.com/prn/x/prid/{product_id}"
        page = None

        # ── Fast path: Fetcher (TLS fingerprint, no browser) ─────────────────
        try:
            page = await AsyncFetcher.get(
                url,
                stealthy_headers=True,     # auto-generate realistic headers
                follow_redirects=True,
                timeout=15,
            )
        except Exception:
            page = None

        # ── Fallback: StealthyFetcher (full browser + CF solver) ──────────────
        if is_blocked(page):
            stats["blocks"] += 1
            print(f"  [W{worker_id}] ⚠️  Blocked on {product_id}, trying StealthyFetcher...")

            # Run sync StealthyFetcher in a thread so we don't block the loop
            page = await loop.run_in_executor(None, stealthy_fetch, url)

            if is_blocked(page):
                # Still blocked — global pause if threshold hit
                async with lock:
                    if stats["blocks"] >= BLOCK_THRESHOLD:
                        print(f"\n  [!] {BLOCK_THRESHOLD} consecutive blocks — pausing {BLOCK_PAUSE}s\n")
                        stats["blocks"] = 0
                        await asyncio.sleep(BLOCK_PAUSE)

                await queue.put(product_id)  # retry later
                queue.task_done()
                continue
            else:
                stats["blocks"] = max(0, stats["blocks"] - 1)

        # ── Extract ───────────────────────────────────────────────────────────
        name, price, image = (None, "N/A", "N/A")
        if page is not None:
            try:
                name, price, image = extract_from_page(page)
            except Exception:
                pass

        # ── Record ────────────────────────────────────────────────────────────
        async with lock:
            processed.add(product_id)
            stats["done"] += 1

            if name and not is_generic(name):
                stats["found"] += 1
                if KEYWORD in name.lower():
                    stats["hits"] += 1
                    results.append({
                        "id": product_id,
                        "name": name,
                        "price": price,
                        "image": image,
                        "url": url,
                    })
                    print(f"  [W{worker_id}] ✅ MATCH  {product_id} | {name} | {price}")

            if stats["done"] % CHECKPOINT_EVERY == 0:
                _save_checkpoint(processed, results)
                _save_output(results)
                elapsed = time.time() - stats["t0"]
                rate = stats["done"] / elapsed if elapsed > 0 else 0
                eta_m = (stats["total"] - stats["done"]) / rate / 60 if rate > 0 else 0
                print(
                    f"  [PROGRESS] {stats['done']:>6}/{stats['total']:,} | "
                    f"found {stats['found']} | hits {stats['hits']} | "
                    f"{rate:.1f} req/s | ETA {eta_m:.0f}m"
                )

        queue.task_done()
        await asyncio.sleep(delay + random.uniform(0, 1.0))

        # Occasionally randomise the delay to break patterns
        if random.random() < 0.12:
            delay = random.uniform(DELAY_MIN, DELAY_MAX)


# ── Checkpoint I/O ────────────────────────────────────────────────────────────
def _load_checkpoint():
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            data = json.load(f)
        print(f"[RESUME] Processed: {len(data['processed'])} | Hits: {len(data['results'])}")
        return set(data["processed"]), data["results"]
    return set(), []

def _save_checkpoint(processed: set, results: list):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"processed": list(processed), "results": results}, f)

def _save_output(results: list):
    out = {}
    for r in results:
        out[r["name"]] = {
            "Price": r["price"],
            "Url": r["url"],
            "Id": str(r["id"]),
            "Image": r.get("image", "N/A"),
        }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=4)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main(start: int, end: int, resume: bool):
    print("=" * 60)
    print("BLINKIT SCRAPER — Scrapling")
    print("=" * 60)

    processed, results = _load_checkpoint() if resume else (set(), [])

    all_ids = [i for i in range(start, end + 1) if i not in processed]
    total   = len(all_ids)
    print(f"IDs to scan  : {total:,} ({start}→{end})")
    print(f"Workers      : {CONCURRENCY} (fast path)")
    print(f"Delay/worker : {DELAY_MIN}–{DELAY_MAX}s + jitter")
    print(f"Keyword      : '{KEYWORD}'")
    print(f"Output       : {OUTPUT_FILE}\n")

    queue: asyncio.Queue = asyncio.Queue()
    for pid in all_ids:
        await queue.put(pid)

    lock  = asyncio.Lock()
    stats = {"done": 0, "found": 0, "hits": 0, "blocks": 0, "total": total, "t0": time.time()}
    loop  = asyncio.get_event_loop()

    # Stagger worker launches to avoid synchronised burst at t=0
    tasks = []
    for i in range(CONCURRENCY):
        await asyncio.sleep(random.uniform(0.3, 1.5))
        tasks.append(asyncio.create_task(
            worker(i + 1, queue, results, processed, lock, stats, loop)
        ))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n[STOPPED] Saving...")

    _save_checkpoint(processed, results)
    _save_output(results)

    elapsed = time.time() - stats["t0"]
    print("\n" + "=" * 60)
    print(f"Finished in : {elapsed/60:.1f} min")
    print(f"Scanned     : {stats['done']:,}")
    print(f"Hits        : {stats['hits']} matching '{KEYWORD}'")
    print(f"Output      : {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",   type=int, default=START_ID)
    parser.add_argument("--end",     type=int, default=END_ID)
    parser.add_argument("--resume",  action="store_true")
    parser.add_argument("--workers", type=int, default=CONCURRENCY)
    args = parser.parse_args()

    CONCURRENCY = args.workers
    asyncio.run(main(start=args.start, end=args.end, resume=args.resume))