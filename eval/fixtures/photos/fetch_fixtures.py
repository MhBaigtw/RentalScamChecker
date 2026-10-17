"""Fetch the photo-duplicate fixture images from Wikimedia Commons and write manifest.json.

Only files whose Commons licence metadata says CC0 or public domain are accepted; the licence is
re-checked through the Commons API at fetch time and recorded per image. No images come from
rental listings. Commons serves an 800-px-wide thumbnail of each file; that is what we keep.

Run from apps/api:
    .venv/Scripts/python.exe ../../eval/fixtures/photos/fetch_fixtures.py
"""

import hashlib
import html
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "images"
MANIFEST = HERE / "manifest.json"
API = "https://commons.wikimedia.org/w/api.php"
UA = "RentalScamChecker/0.1 (https://github.com/MhBaigtw/RentalScamChecker; eval fixtures)"
WIDTH = 800
ACCEPTED = ("cc0", "pd")

# (group, title). Group names similar-looking photos that are NOT copies of each other: they are
# the hard negatives (different rooms, or the same room photographed separately).
# Removed after visual review: "Bathroom in Tallinn 2.jpg" (looks like the same photo as
# "Bathroom in Tallinn.JPG", so "not a duplicate" would be an unreliable label), the second Glenmont
# file (a blank wall) and "Kitchen area with open door..." (mostly a blurred wall).
TITLES: list[tuple[str, str]] = [
    ("gfp-bedrooms", "File:Gfp-bed.jpg"),
    ("gfp-bedrooms", "File:Gfp-bedroom-2.jpg"),
    ("gfp-bedrooms", "File:Gfp-bedroom1.jpg"),
    ("gfp-bedrooms", "File:Gfp-bathroom.jpg"),
    ("dormitorio", "File:Dormitorio de Mitre.JPG"),
    ("dormitorio", "File:Dormitorio en San José.JPG"),
    (
        "glenmont",
        "File:Images of North Bedroom at Glenmont at TENHP in 2014 (ad79040b-d0a8-484d-9715-6f10beb48443).jpg",
    ),
    ("tallinn-bathrooms", "File:Bathroom in Tallinn.JPG"),
    (
        "dpla-residences",
        "File:Casablanca - Standard Level Position Residence - 1972 - DPLA - 39a6039c3168bb0b58a88b809bc1f236.jpg",
    ),
    (
        "dpla-residences",
        "File:Casablanca - Standard Level Position Residence - 1972 - DPLA - a4c0b902633353499bfaa9a306ee4121.jpg",
    ),
    (
        "dpla-residences",
        "File:Casablanca - Standard Level Position Residence - 1972 - DPLA - e12597e33ea3be1279a25028e49c43ec.jpg",
    ),
    (
        "dpla-residences",
        "File:Kuala Lumpur - Mid-Level Position Residence - 1976 - DPLA - e73691be620b1be8c52af1b22248f89d.jpg",
    ),
    ("hotel-rooms", "File:Hotel Room (32259665218).jpg"),
    ("hotel-rooms", "File:Centennial Inn Master Suite.jpg"),
    ("hotel-rooms", "File:GranaryHotel-apart.JPG"),
    ("hotel-rooms", "File:Hotel-suite-living-room.jpg"),
    ("kitchens", "File:Hostel-garant-kitchen.jpg"),
    ("kitchens", "File:Mini kitchen.jpg"),
    ("bathrooms", "File:Badezimmer.jpg"),
    ("bathrooms", "File:Ban-Bathroom-Nus.JPG"),
    ("other-interiors", "File:Bedroom window (Unsplash).jpg"),
    ("other-interiors", "File:Museo civiltà contadina Friuli imperiale - La camera da letto.jpg"),
    ("other-interiors", "File:Antesala.jpg"),
    ("other-interiors", "File:Drawing Room - Camondo Apartment - 82 Avenue des Champs-Elysees.jpg"),
]


def _text(meta: dict[str, dict[str, str]], key: str) -> str:
    raw = (meta.get(key) or {}).get("value", "")
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _slug(title: str) -> str:
    stem = title.removeprefix("File:").rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:60]


def main() -> None:
    IMAGES.mkdir(parents=True, exist_ok=True)
    entries = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as client:
        for index, (group, title) in enumerate(TITLES, start=1):
            info = client.get(
                API,
                params={
                    "action": "query",
                    "format": "json",
                    "titles": title,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|mime|sha1",
                    "iiurlwidth": str(WIDTH),
                },
            ).json()
            (page,) = info["query"]["pages"].values()
            if "imageinfo" not in page:
                raise SystemExit(f"{title}: not found on Commons")
            ii = page["imageinfo"][0]
            meta = ii["extmetadata"]
            code = _text(meta, "License").lower()
            if code not in ACCEPTED:
                raise SystemExit(f"{title}: licence {code!r} is not CC0/public domain; refusing")
            data = client.get(ii["thumburl"]).content
            name = f"{index:02d}-{_slug(title)}.jpg"
            (IMAGES / name).write_bytes(data)
            entries.append(
                {
                    "file": f"images/{name}",
                    "group": group,
                    "title": title,
                    "source_page": ii["descriptionurl"],
                    "downloaded_from": ii["thumburl"],
                    "original_sha1": ii.get("sha1"),
                    "licence": _text(meta, "LicenseShortName"),
                    "licence_code": code,
                    "licence_url": _text(meta, "LicenseUrl") or None,
                    "artist": _text(meta, "Artist") or None,
                    "credit": _text(meta, "Credit") or None,
                    "width": ii.get("thumbwidth"),
                    "height": ii.get("thumbheight"),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            print(f"{name}: {code}, {len(data) / 1e3:.0f} kB", file=sys.stderr)
            time.sleep(0.5)  # be gentle with Commons
    manifest = {
        "description": (
            "Base images for eval/photo_dupes.py. All CC0 or public domain on Wikimedia "
            "Commons (licence checked via the Commons API at fetch time). 800-px-wide Commons "
            "thumbnails. Not from rental listings."
        ),
        "retrieved": datetime.now(UTC).isoformat(timespec="seconds"),
        "total_bytes": sum(e["bytes"] for e in entries),
        "images": entries,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(entries)} images, {manifest['total_bytes'] / 1e6:.2f} MB -> {MANIFEST}")


if __name__ == "__main__":
    main()
