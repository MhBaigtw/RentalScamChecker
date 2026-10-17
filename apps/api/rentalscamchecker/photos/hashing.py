"""Perceptual hashes (DETERMINISTIC, no model): pHash and dHash from the imagehash library.

Both are 64-bit fingerprints where similar-looking images get hashes that differ in few bits.
- pHash: DCT of a 32x32 greyscale image, keep the 8x8 low frequencies, 1 bit = above/below the
  median. Robust to resizing, recompression and mild colour changes.
- dHash: 9x8 greyscale, 1 bit = is this pixel brighter than its right neighbour. Robust to the same,
  very cheap.
Neither survives large crops, rotations or mirroring well; the CLIP embedding covers those (ED-025).

Postgres has no unsigned 64-bit type, so hashes are stored as signed BIGINT (two's complement).
Hamming distance is computed on the bit pattern either way.
"""

import io
from dataclasses import dataclass

import imagehash
from PIL import Image

_MASK = (1 << 64) - 1


@dataclass(frozen=True)
class PhotoHashes:
    phash: int  # signed 64-bit
    dhash: int  # signed 64-bit


def to_signed64(unsigned: int) -> int:
    return unsigned - (1 << 64) if unsigned >= 1 << 63 else unsigned


def hamming(a: int, b: int) -> int:
    """Differing bits between two 64-bit hashes (signed or unsigned representation)."""
    return ((a ^ b) & _MASK).bit_count()


def _as_int(h: imagehash.ImageHash) -> int:
    return to_signed64(int(str(h), 16))


def hash_image(image: Image.Image) -> PhotoHashes:
    return PhotoHashes(
        phash=_as_int(imagehash.phash(image, hash_size=8)),
        dhash=_as_int(imagehash.dhash(image, hash_size=8)),
    )


def hash_bytes(data: bytes) -> PhotoHashes:
    with Image.open(io.BytesIO(data)) as image:
        return hash_image(image.convert("RGB"))
