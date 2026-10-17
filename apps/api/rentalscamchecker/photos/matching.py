"""When are two photos "the same photo"? DETERMINISTIC rules over hash distances and embedding
similarity, with thresholds calibrated on eval/photo_dupes.py (ED-025; numbers in
eval/reports/photo_dupes.md).

A pair is a duplicate if ANY of these holds:
- pHash distance <= 10 or dHash distance <= 10 (one hash alone, kept strict);
- pHash distance <= 18 AND dHash distance <= 18 (looser, but both hashes must agree);
- CLIP cosine similarity >= 0.95.

Why these and not looser ones: a query photo is compared with every stored photo, so the per-pair
false-match rate must stay tiny as the database grows. For two unrelated 64-bit hashes behaving
like random bits, P(distance <= 18) is about 3e-4 (2 false hits per query at 10,000 photos), while
P(distance <= 10) is about 1e-8. On the fixture set the closest non-duplicate pair had
max(pHash, dHash) = 22 and cosine 0.928 (a similar-looking but different room).
"""

from dataclasses import dataclass
from typing import Literal

MatchKind = Literal["hash", "embedding"]


@dataclass(frozen=True)
class Thresholds:
    single_hash: int = 10
    both_hashes: int = 18
    cosine: float = 0.95


DEFAULT = Thresholds()


def hash_duplicate(phash_distance: int, dhash_distance: int, t: Thresholds = DEFAULT) -> bool:
    return (
        phash_distance <= t.single_hash
        or dhash_distance <= t.single_hash
        or (phash_distance <= t.both_hashes and dhash_distance <= t.both_hashes)
    )


def classify(
    phash_distance: int, dhash_distance: int, cosine: float | None, t: Thresholds = DEFAULT
) -> MatchKind | None:
    """Returns "hash" when the hashes say duplicate (the stronger evidence), "embedding" when only
    the embedding does, and None otherwise."""
    if hash_duplicate(phash_distance, dhash_distance, t):
        return "hash"
    if cosine is not None and cosine >= t.cosine:
        return "embedding"
    return None
