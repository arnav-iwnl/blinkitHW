#!/usr/bin/env python3
"""Filter Blinkit sitemap URLs by product id.

By default this keeps only <url> entries whose trailing /prid/<number>
value is greater than 770000.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


PRID_RE = re.compile(r"/prid/(\d+)(?:/|$)")


def extract_prid(loc_text: str | None) -> int | None:
    if not loc_text:
        return None
    match = PRID_RE.search(loc_text.strip())
    if not match:
        return None
    return int(match.group(1))


IMAGE_NS = "http://www.google.com/schemas/sitemap-image/1.1"
LOC_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def filter_sitemap(input_path: Path, output_path: Path, min_prid: int, keyword: str) -> tuple[int, int]:
    raw_text = input_path.read_text(encoding="utf-8-sig")
    root_start = raw_text.find("<urlset")
    if root_start >= 0:
        raw_text = raw_text[root_start:]

    root = ET.fromstring(raw_text)
    tree = ET.ElementTree(root)

    kept = 0
    removed = 0
    entries: list[dict[str, object]] = []

    for url_element in list(root):
        loc_element = url_element.find(f"{{{LOC_NS}}}loc")
        loc_text = loc_element.text if loc_element is not None else None
        prid = extract_prid(loc_text)
        loc_text_normalized = (loc_text or "").lower()
        matches_keyword = keyword.lower() in loc_text_normalized

        if prid is not None and prid > min_prid and matches_keyword:
            kept += 1
            image_element = url_element.find(f"{{{IMAGE_NS}}}image")
            image_loc_element = image_element.find(f"{{{IMAGE_NS}}}loc") if image_element is not None else None
            lastmod_element = url_element.find(f"{{{LOC_NS}}}lastmod")

            entries.append(
                {
                    "loc": loc_element.text.strip() if loc_element is not None and loc_element.text else None,
                    "prid": prid,
                    "image": image_loc_element.text.strip() if image_loc_element is not None and image_loc_element.text else None,
                    "lastmod": lastmod_element.text.strip() if lastmod_element is not None and lastmod_element.text else None,
                }
            )
            continue

        root.remove(url_element)
        removed += 1

    entries.sort(key=lambda entry: int(entry["prid"]), reverse=True)

    payload = {
        "min_prid": min_prid,
        "kept": kept,
        "removed": removed,
        "entries": entries,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return kept, removed


def build_output_path(input_path: Path, min_prid: int, keyword: str) -> Path:
    safe_keyword = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_") or "filtered"
    return input_path.with_name(f"{input_path.stem}_{safe_keyword}.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter Blinkit sitemap entries by product id.")
    parser.add_argument("--input", default="product.xml", help="Input sitemap file")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--min-prid", type=int, default=770000, help="Keep entries with prid greater than this value")
    parser.add_argument("--keyword", default="hot-wheels", help="Keep entries whose URL contains this keyword")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else build_output_path(input_path, args.min_prid, args.keyword)

    kept, removed = filter_sitemap(input_path, output_path, args.min_prid, args.keyword)
    print(f"Wrote {output_path} ({kept} kept, {removed} removed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())