"""Comparing two typed street addresses (DETERMINISTIC). Used by photo_reuse_internal to tell
"the same listing posted again" from "photos reused for another place" (ED-025).

"1430 Trafalgar Rd., Unit 5" and "1430 trafalgar road" are the same street address: case,
punctuation, common abbreviations and unit/apartment designators are normalised away (two units of
one building can share photos of the building legitimately). This is a heuristic, not
geocoding: "1430 Trafalgar" vs "1430 Trafalgar Rd" still differ.
"""

import re

from rentalscamchecker.reference_rents import normalize_city

_ABBREVIATIONS = {
    "rd": "road",
    "st": "street",
    "ave": "avenue",
    "av": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "cres": "crescent",
    "cr": "crescent",
    "ct": "court",
    "crt": "court",
    "pl": "place",
    "ln": "lane",
    "hwy": "highway",
    "pkwy": "parkway",
    "cir": "circle",
    "terr": "terrace",
    "sq": "square",
    "e": "east",
    "w": "west",
    "n": "north",
    "s": "south",
}
# "#" needs its own branch: \b never matches between a space and "#".
_UNIT = re.compile(r"(\b(unit|apt|apartment|suite|ste|basement|bsmt)\b|#)\s*[\w-]*", re.IGNORECASE)


def normalize_street(address: str) -> str:
    text = _UNIT.sub(" ", address.casefold())
    words = re.sub(r"[^\w\s]", " ", text).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def same_address(street_a: str, city_a: str, street_b: str, city_b: str) -> bool:
    return normalize_street(street_a) == normalize_street(street_b) and normalize_city(
        city_a
    ) == normalize_city(city_b)
