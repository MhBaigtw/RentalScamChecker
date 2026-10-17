"""Photo pieces without a database: hashing, the match rule, address comparison, and the real
int8 CLIP model on fixture images (the model file must be present: see photos/model_files.py)."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageOps

from rentalscamchecker.addresses import normalize_street, same_address
from rentalscamchecker.config import get_settings
from rentalscamchecker.photos.embeddings import ClipEmbedder, cosine_similarity, preprocess
from rentalscamchecker.photos.hashing import hamming, hash_image, to_signed64
from rentalscamchecker.photos.matching import Thresholds, classify, hash_duplicate
from rentalscamchecker.photos.model_files import model_path

FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "fixtures" / "photos" / "images"


def fixture(prefix: str) -> Image.Image:
    (path,) = FIXTURES.glob(f"{prefix}-*.jpg")
    return Image.open(path).convert("RGB")


# --- hashing ---


def test_signed_representation_round_trips_for_hamming() -> None:
    top_bit = 1 << 63
    assert to_signed64(top_bit) == -(1 << 63)
    assert to_signed64(5) == 5
    # Distance is the same whether hashes are signed or unsigned.
    assert hamming(to_signed64(top_bit | 1), 1) == hamming(top_bit | 1, 1) == 1
    assert hamming(-1, 0) == 64


def test_resized_and_recompressed_copies_hash_close_different_rooms_far() -> None:
    room = fixture("01")
    other = fixture("02")  # a different bedroom in the same style
    h = hash_image(room)
    small = hash_image(room.resize((room.width // 2, room.height // 2)))
    different = hash_image(other)

    assert hamming(h.phash, small.phash) <= 4
    assert hamming(h.dhash, small.dhash) <= 4
    assert hamming(h.phash, different.phash) > 18


# --- match rule ---


@pytest.mark.parametrize(
    ("phash", "dhash", "cosine", "kind"),
    [
        (10, 40, None, "hash"),  # one strict hash is enough
        (40, 10, None, "hash"),
        (18, 18, None, "hash"),  # both hashes agree within 18
        (18, 19, None, None),
        (19, 18, 0.949, None),
        (30, 30, 0.95, "embedding"),
        (5, 5, 0.99, "hash"),  # hash wins when both match
    ],
)
def test_match_rule(phash: int, dhash: int, cosine: float | None, kind: str | None) -> None:
    assert classify(phash, dhash, cosine) == kind


def test_thresholds_are_adjustable() -> None:
    strict = Thresholds(single_hash=4, both_hashes=8, cosine=0.99)
    assert not hash_duplicate(10, 40, strict)
    assert classify(30, 30, 0.97, strict) is None


# --- addresses ---


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("1430 Trafalgar Rd", "1430 trafalgar road"),
        ("1430 Trafalgar Rd., Unit 5", "1430 Trafalgar Road"),
        ("Apt 1203, 3880 Duke of York Blvd", "3880 Duke Of York Boulevard"),
        ("12 King St W", "12 king street west"),
        ("55 Elm Ave #B", "55 Elm Avenue"),
    ],
)
def test_same_street_written_differently(a: str, b: str) -> None:
    assert normalize_street(a) == normalize_street(b)


def test_same_address_needs_same_city_too() -> None:
    assert same_address("1430 Trafalgar Rd", "Oakville", "1430 Trafalgar Road", "City of Oakville")
    assert not same_address("1430 Trafalgar Rd", "Oakville", "1430 Trafalgar Rd", "Mississauga")
    assert not same_address("1430 Trafalgar Rd", "Oakville", "1431 Trafalgar Rd", "Oakville")


# --- the real model (pretrained CLIP ViT-B/32, int8 ONNX) ---


@pytest.fixture(scope="module")
def embedder() -> ClipEmbedder:
    settings = get_settings()
    return ClipEmbedder(model_path("clip-vit-b32-int8", settings.photo_model_path))


def test_preprocess_matches_clip_input_shape() -> None:
    pixels = preprocess(fixture("01"))
    assert pixels.shape == (3, 224, 224)
    assert pixels.dtype == np.float32


def test_embeddings_are_normalised_512_vectors(embedder: ClipEmbedder) -> None:
    (v,) = embedder.embed_images([fixture("01")])
    assert v.shape == (512,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_mirrored_copy_is_close_and_a_different_room_is_not(embedder: ClipEmbedder) -> None:
    room = fixture("01")
    original, mirrored, other = embedder.embed_images([room, ImageOps.mirror(room), fixture("02")])

    assert cosine_similarity(original, mirrored) >= 0.95
    assert cosine_similarity(original, other) < 0.95


def test_missing_model_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="model_files"):
        ClipEmbedder(tmp_path / "nope.onnx").embed_images([fixture("01")])
