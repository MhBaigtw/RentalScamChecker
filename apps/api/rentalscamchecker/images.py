"""Untrusted photo handling: validate, decode safely, re-encode as JPEG without metadata.

Re-encoding (rather than storing the uploaded bytes) does three jobs at once: it drops EXIF
(which can contain GPS coordinates and device details), it proves the file really decodes as
an image, and it gives every stored photo the same format for the hashing in M5.
"""

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps

ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_SIDE_PX = 8000
# Checked from the header before any pixel data is decoded. 40 MP covers phone cameras.
MAX_PIXELS = 40_000_000
JPEG_QUALITY = 90

# Pillow's own decompression-bomb guard: warns above this many pixels, raises above twice it.
# We lower it to our limit and turn the warning into an error below, as a second line of defence
# behind the explicit header check.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class PhotoRejected(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedPhoto:
    jpeg_bytes: bytes
    width: int
    height: int


def process_photo(data: bytes) -> ProcessedPhoto:
    """Validate and re-encode one uploaded photo. Raises PhotoRejected with a user-safe reason."""
    if len(data) == 0:
        raise PhotoRejected("the file is empty")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoRejected(f"the file is larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # open() only reads the header; nothing is decoded yet.
            with Image.open(io.BytesIO(data)) as img:
                _check_header(img)
                img.load()
                return _reencode(img)
    except PhotoRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PhotoRejected("the image has too many pixels") from exc
    # Pillow reports corrupt, truncated or unknown files with these exception types.
    except (OSError, ValueError, SyntaxError) as exc:
        raise PhotoRejected("the file is not a readable JPEG, PNG or WebP image") from exc


def _check_header(img: Image.Image) -> None:
    if img.format not in ALLOWED_FORMATS:
        raise PhotoRejected("only JPEG, PNG and WebP images are accepted")
    width, height = img.size
    if width > MAX_SIDE_PX or height > MAX_SIDE_PX:
        raise PhotoRejected(f"the image is wider or taller than {MAX_SIDE_PX} pixels")
    if width * height > MAX_PIXELS:
        raise PhotoRejected("the image has too many pixels")


def _reencode(img: Image.Image) -> ProcessedPhoto:
    # Apply the EXIF orientation to the pixels first, since the EXIF tag is about to be dropped.
    upright = ImageOps.exif_transpose(img)
    rgb = upright.convert("RGB")
    out = io.BytesIO()
    # No exif=/icc_profile= arguments, so Pillow writes no metadata.
    rgb.save(out, format="JPEG", quality=JPEG_QUALITY)
    return ProcessedPhoto(jpeg_bytes=out.getvalue(), width=rgb.width, height=rgb.height)
