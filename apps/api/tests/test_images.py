import io

import pytest
from PIL import Image

from rentalscamchecker.images import MAX_PHOTO_BYTES, PhotoRejected, process_photo
from tests.helpers import image_bytes

GPS_IFD = 0x8825
ORIENTATION = 0x0112
MAKE = 0x010F


def exif_with_gps_and_orientation(orientation: int = 1) -> Image.Exif:
    exif = Image.Exif()
    exif[MAKE] = "TestPhone"
    exif[ORIENTATION] = orientation
    # GPSLatitudeRef / GPSLatitude: somewhere in Oakville.
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (43.0, 27.0, 0.0)})
    return exif


def test_input_fixture_really_contains_exif() -> None:
    data = image_bytes(exif=exif_with_gps_and_orientation())

    with Image.open(io.BytesIO(data)) as img:
        assert img.getexif()[MAKE] == "TestPhone"


def test_output_is_jpeg_without_exif() -> None:
    result = process_photo(image_bytes(exif=exif_with_gps_and_orientation()))

    with Image.open(io.BytesIO(result.jpeg_bytes)) as out:
        assert out.format == "JPEG"
        assert len(out.getexif()) == 0
        assert "exif" not in out.info
        assert "icc_profile" not in out.info


def test_exif_orientation_is_applied_before_it_is_dropped() -> None:
    # Orientation 6 = "rotate 90° clockwise to display": a 64x48 file displays as 48x64.
    result = process_photo(image_bytes(size=(64, 48), exif=exif_with_gps_and_orientation(6)))

    assert (result.width, result.height) == (48, 64)


@pytest.mark.parametrize(("fmt", "mode"), [("PNG", "RGBA"), ("PNG", "P"), ("WEBP", "RGB")])
def test_png_and_webp_are_converted_to_jpeg(fmt: str, mode: str) -> None:
    result = process_photo(image_bytes(size=(30, 20), fmt=fmt, mode=mode))

    assert result.jpeg_bytes[:3] == b"\xff\xd8\xff"
    assert (result.width, result.height) == (30, 20)


def test_gif_is_rejected() -> None:
    with pytest.raises(PhotoRejected, match="only JPEG, PNG and WebP"):
        process_photo(image_bytes(fmt="GIF", mode="P"))


@pytest.mark.parametrize("data", [b"not an image at all", b"\xff\xd8\xff\xe0truncated"])
def test_non_images_are_rejected(data: bytes) -> None:
    with pytest.raises(PhotoRejected, match="not a readable"):
        process_photo(data)


def test_empty_file_is_rejected() -> None:
    with pytest.raises(PhotoRejected, match="empty"):
        process_photo(b"")


def test_oversized_file_is_rejected_before_decoding() -> None:
    with pytest.raises(PhotoRejected, match="larger than 10 MB"):
        process_photo(b"\0" * (MAX_PHOTO_BYTES + 1))


def test_image_wider_than_limit_is_rejected() -> None:
    with pytest.raises(PhotoRejected, match="wider or taller"):
        process_photo(image_bytes(size=(8001, 10), fmt="PNG", mode="L"))


def test_decompression_bomb_is_rejected() -> None:
    # 7000 x 7000 = 49 MP of a single colour: a PNG of a few kilobytes that would need
    # ~150 MB of RAM decoded as RGB. Each side is under the 8000 px limit; the pixel count isn't.
    bomb = image_bytes(size=(7000, 7000), fmt="PNG", mode="1")
    assert len(bomb) < 100_000

    with pytest.raises(PhotoRejected, match="too many pixels"):
        process_photo(bomb)
