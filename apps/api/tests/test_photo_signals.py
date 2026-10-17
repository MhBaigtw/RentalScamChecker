"""photo_reuse_internal and photo_reuse_web against the real test database. Fixture photos are the
CC0/public-domain set in eval/fixtures/photos; the CLIP model is the real int8 file. The web search
provider is always a fake: no TinEye request is ever made."""

import io
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image, ImageOps
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.config import get_settings
from rentalscamchecker.graph import public_ref
from rentalscamchecker.images import process_photo
from rentalscamchecker.jobs.worker import Worker, WorkerConfig
from rentalscamchecker.main import create_app
from rentalscamchecker.models import Check, CheckPhoto
from rentalscamchecker.photos.embeddings import ClipEmbedder
from rentalscamchecker.photos.model_files import model_path
from rentalscamchecker.photos.web_search import (
    CachedCappedSearch,
    TinEyeClient,
    WebMatch,
    WebSearchUnavailable,
    parse_matches,
)
from rentalscamchecker.pipeline import build_handlers
from rentalscamchecker.signals.base import CheckInput, SignalContext
from rentalscamchecker.signals.photo_reuse_internal import PhotoReuseInternalSignal
from rentalscamchecker.signals.photo_reuse_web import PhotoReuseWebSignal
from rentalscamchecker.signals.registry import enabled_signals
from rentalscamchecker.storage import LocalDiskStorage
from tests.helpers import UNREACHABLE_DB_URL, make_settings

FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "fixtures" / "photos" / "images"
CTX = SignalContext(settings=make_settings(UNREACHABLE_DB_URL))


def fixture(prefix: str) -> Image.Image:
    (path,) = FIXTURES.glob(f"{prefix}-*.jpg")
    return Image.open(path).convert("RGB")


def jpeg(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def crop(image: Image.Image, fraction: float) -> Image.Image:
    w, h = image.size
    cw, ch = int(w * fraction), int(h * fraction)
    return image.crop(((w - cw) // 2, (h - ch) // 2, (w + cw) // 2, (h + ch) // 2))


@pytest.fixture(scope="module")
def embedder() -> ClipEmbedder:
    return ClipEmbedder(model_path("clip-vit-b32-int8", get_settings().photo_model_path))


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "photos")


def add_check(
    sf: sessionmaker[Session],
    storage: LocalDiskStorage,
    images: list[Image.Image],
    address: str = "1430 Trafalgar Rd",
    city: str = "Oakville",
) -> CheckInput:
    """Store photos the way POST /checks does (re-encoded JPEG) and return the signal input."""
    check_id = uuid.uuid4()
    with sf.begin() as s:
        s.add(
            Check(
                id=check_id,
                listing_text="x",
                rent_cents=150000,
                bedrooms=2,
                address_text=address,
                city=city,
            )
        )
        s.flush()
        for position, image in enumerate(images):
            processed = process_photo(jpeg(image))
            key = f"checks/{check_id}/{uuid.uuid4()}.jpg"
            storage.put(key, processed.jpeg_bytes)
            s.add(
                CheckPhoto(
                    check_id=check_id,
                    position=position,
                    storage_key=key,
                    width=processed.width,
                    height=processed.height,
                )
            )
    return CheckInput(check_id, "x", 150000, 2, address, city, None)


pytestmark = pytest.mark.integration


# --- photo_reuse_internal ---


def test_first_check_stores_fingerprints_and_finds_nothing(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    check = add_check(session_factory, storage, [fixture("01"), fixture("15")])

    result = signal.run(check, CTX)

    assert (result.status, result.severity) == ("ok", "none")
    assert result.evidence["photos_checked"] == 2
    assert result.evidence["matches"] == []
    with session_factory() as s:
        rows = s.execute(
            sa.text(
                "SELECT phash IS NOT NULL, dhash IS NOT NULL, vector_dims(embedding) "
                "FROM check_photos WHERE check_id = :id"
            ),
            {"id": check.check_id},
        ).all()
    assert [tuple(r) for r in rows] == [(True, True, 512), (True, True, 512)]


def test_resized_copy_at_a_different_address_is_high(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    first = add_check(session_factory, storage, [fixture("01"), fixture("15")])
    signal.run(first, CTX)
    room = fixture("15")
    reposted = add_check(
        session_factory,
        storage,
        [fixture("19"), room.resize((room.width // 2, room.height // 2))],
        address="3880 Duke of York Blvd",
        city="Mississauga",
    )

    result = signal.run(reposted, CTX)

    assert result.severity == "high"
    (match,) = result.evidence["matches"]
    assert match["photo"] == 2
    assert match["matched_check"] == public_ref(first.check_id)
    assert match["matched_city"] == "Oakville"
    assert match["same_address"] is False
    assert match["match_type"] == "hash"
    assert result.evidence["other_cities"] == ["Oakville"]
    assert result.explanation is not None and "different address (Oakville)" in result.explanation
    assert "scam" not in f"{result.explanation} {result.next_step}".lower()


def test_mirrored_copy_is_found_by_the_embedding_only(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    signal.run(add_check(session_factory, storage, [fixture("01")]), CTX)
    mirrored = add_check(
        session_factory, storage, [ImageOps.mirror(fixture("01"))], city="Brampton"
    )

    result = signal.run(mirrored, CTX)

    assert result.severity == "medium"
    (match,) = result.evidence["matches"]
    assert match["match_type"] == "embedding"
    assert match["cosine_similarity"] >= 0.95


def test_same_address_repost_is_information_not_a_warning(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    signal.run(add_check(session_factory, storage, [fixture("01")]), CTX)
    again = add_check(
        session_factory, storage, [fixture("01")], address="1430 Trafalgar Road, Unit 2"
    )

    result = signal.run(again, CTX)

    assert result.severity == "none"
    assert result.evidence["same_address_matches"] == 1
    assert result.evidence["depends_on_other_checks"] is False
    assert result.explanation is not None and "same address" in result.explanation


def test_same_address_match_to_an_independently_high_check_depends_on_other_checks(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    """v3 (M7, ED-027): the earlier check at this address is high risk on its own evidence, so the
    match is concerning, but only because of another check's risk: marked
    depends_on_other_checks, which the scorer caps and leaves out of the independent level."""
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    earlier = add_check(session_factory, storage, [fixture("01")])
    signal.run(earlier, CTX)
    with session_factory.begin() as s:
        s.execute(
            sa.update(Check)
            .where(Check.id == earlier.check_id)
            .values(risk_level="high", independent_risk_level="high")
        )
    again = add_check(
        session_factory, storage, [fixture("01")], address="1430 Trafalgar Road, Unit 2"
    )

    result = signal.run(again, CTX)

    assert result.severity == "high"
    assert result.evidence["depends_on_other_checks"] is True
    assert result.evidence["other_checks_at_different_address"] == 0
    assert result.explanation is not None
    assert "same address" in result.explanation
    assert "different address" not in result.explanation


def test_only_the_independent_level_of_an_earlier_check_counts(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    """An earlier check that is "high" only through graph links (independent "some") doesn't
    make a same-address repost concerning: no second hop."""
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    earlier = add_check(session_factory, storage, [fixture("01")])
    signal.run(earlier, CTX)
    with session_factory.begin() as s:
        s.execute(
            sa.update(Check)
            .where(Check.id == earlier.check_id)
            .values(risk_level="high", independent_risk_level="some")
        )
    again = add_check(session_factory, storage, [fixture("01")], address="1430 Trafalgar Rd")

    result = signal.run(again, CTX)

    assert result.severity == "none"
    assert result.evidence["depends_on_other_checks"] is False


def test_similar_but_different_rooms_do_not_match(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    # Hard negatives from the fixture set: similar bedrooms, similar hotel rooms.
    signal.run(add_check(session_factory, storage, [fixture("01"), fixture("15")]), CTX)
    others = add_check(
        session_factory, storage, [fixture("02"), fixture("03"), fixture("16")], city="Brampton"
    )

    result = signal.run(others, CTX)

    assert (result.severity, result.evidence["matches"]) == ("none", [])


def test_rerun_reuses_stored_fingerprints(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage
) -> None:
    class CountingEmbedder(ClipEmbedder):
        calls = 0

        def embed_images(self, images: list[Image.Image]) -> list:  # type: ignore[type-arg]
            CountingEmbedder.calls += len(images)
            return super().embed_images(images)

    counting = CountingEmbedder(model_path("clip-vit-b32-int8", get_settings().photo_model_path))
    signal = PhotoReuseInternalSignal(session_factory, storage, counting)
    check = add_check(session_factory, storage, [fixture("01"), fixture("02")])

    signal.run(check, CTX)
    signal.run(check, CTX)

    assert CountingEmbedder.calls == 2  # embedded once each, not again on the re-run


# --- photo_reuse_web (fakes only) ---


class FakeSearch:
    def __init__(self, matches: list[WebMatch] | None = None, error: str | None = None) -> None:
        self.matches = matches or []
        self.error = error
        self.calls = 0

    def search(self, image_bytes: bytes) -> list[WebMatch]:
        self.calls += 1
        if self.error:
            raise WebSearchUnavailable(self.error)
        return self.matches


def web_signal(
    sf: sessionmaker[Session], storage: LocalDiskStorage, fake: FakeSearch | None, cap: int = 20
) -> PhotoReuseWebSignal:
    search = CachedCappedSearch(sf, fake, daily_cap=cap, cap_lock_wait_seconds=5) if fake else None
    return PhotoReuseWebSignal(sf, storage, search, max_photos=2)


def test_web_search_off_is_skipped_without_any_call(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage
) -> None:
    check = add_check(session_factory, storage, [fixture("01")])

    result = web_signal(session_factory, storage, None).run(check, CTX)

    assert (result.status, result.evidence["reason"]) == ("skipped", "disabled")
    assert result.next_step is not None and "Google Lens" in result.next_step


def test_web_matches_are_reported_cached_and_limited_per_check(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage
) -> None:
    fake = FakeSearch(
        [WebMatch("https://x.example/a.jpg", "x.example", 92.5, ["https://x.example/l"])]
    )
    check = add_check(session_factory, storage, [fixture("01"), fixture("02"), fixture("03")])
    signal = web_signal(session_factory, storage, fake)

    first = signal.run(check, CTX)
    second = signal.run(check, CTX)

    assert (first.status, first.severity) == ("ok", "medium")
    assert (first.evidence["photos_searched"], first.evidence["photos_not_searched"]) == (2, 1)
    assert fake.calls == 2  # third photo skipped by the per-check limit; re-run served from cache
    results = second.evidence["results"]
    assert isinstance(results, list) and all(r["cached"] for r in results)


def test_web_daily_cap_turns_into_skipped(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage
) -> None:
    fake = FakeSearch([])
    check = add_check(session_factory, storage, [fixture("01")])

    result = web_signal(session_factory, storage, fake, cap=0).run(check, CTX)

    assert (result.status, result.evidence["reason"]) == ("skipped", "daily_cap_reached")
    assert fake.calls == 0


def test_tineye_request_shape_and_response_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": {
                    "matches": [
                        {
                            "image_url": "https://img.example/1.jpg",
                            "domain": "rentals.example",
                            "score": 87.1,
                            "backlinks": [{"url": "u", "backlink": "https://rentals.example/ad/1"}],
                        }
                    ]
                }
            },
        )

    client = TinEyeClient("k-test", "https://api.example/rest/", 5, httpx.MockTransport(handler))

    (match,) = client.search(b"jpegbytes")

    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/rest/search/")
    assert request.headers["x-api-key"] == "k-test"
    assert b'name="image_upload"' in request.read()
    assert match == WebMatch(
        "https://img.example/1.jpg", "rentals.example", 87.1, ["https://rentals.example/ad/1"]
    )
    assert parse_matches({}) == []


# --- startup checks and the end-to-end journey ---


def test_web_signal_enabled_without_key_fails_at_startup() -> None:
    settings = make_settings(
        UNREACHABLE_DB_URL, enabled_signals="photo_reuse_web", tineye_enabled=True
    )

    with pytest.raises(ValueError, match="TINEYE_API_KEY"):
        enabled_signals(settings, sessionmaker())


def test_internal_signal_without_model_file_fails_at_startup(tmp_path: Path) -> None:
    settings = make_settings(
        UNREACHABLE_DB_URL, enabled_signals="photo_reuse_internal", photo_model_dir=tmp_path
    )

    with pytest.raises(ValueError, match="embedding model is missing"):
        enabled_signals(settings, sessionmaker())


def test_tineye_budget_must_fit_the_job_timeout() -> None:
    with pytest.raises(ValueError, match="TinEye worst case"):
        make_settings(UNREACHABLE_DB_URL, tineye_enabled=True, tineye_max_photos_per_check=3)


def test_resubmitted_cropped_and_resized_photos_are_flagged_end_to_end(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    """The M5 'Done when': a second listing with cropped/resized versions of earlier photos."""
    settings = make_settings(
        photo_dir=photo_dir,
        enabled_signals="photo_reuse_internal",
        anthropic_api_key=SecretStr("unused"),
    )
    client = TestClient(create_app(settings))
    worker = Worker(
        session_factory,
        build_handlers(session_factory, enabled_signals(settings, session_factory), settings),
        WorkerConfig(concurrency=1, lease_seconds=30, job_timeout_seconds=20),
    )

    def submit(images: list[Image.Image], address: str, city: str) -> dict[str, Any]:
        created = client.post(
            "/checks",
            data={
                "listing_text": "2 bed near Sheridan",
                "monthly_rent": "1800",
                "bedrooms": "2",
                "address_text": address,
                "city": city,
            },
            files=[
                ("photos", (f"p{i}.jpg", jpeg(im), "image/jpeg")) for i, im in enumerate(images)
            ],
        )
        check_id = created.json()["check_id"]
        while worker.run_once():
            pass
        body: dict[str, Any] = client.get(f"/checks/{check_id}").json()
        return body

    originals = [fixture("01"), fixture("15"), fixture("19")]
    first = submit(originals, "1430 Trafalgar Rd", "Oakville")
    edited = [
        crop(originals[0], 0.8),  # centre crop 80%
        originals[1].resize((originals[1].width // 2, originals[1].height // 2)),
        crop(originals[2], 0.9).resize((400, 270)),
    ]
    second = submit(edited, "3880 Duke of York Blvd", "Mississauga")

    (s1,) = first["signals"]
    (s2,) = second["signals"]
    assert s1["severity"] == "none"
    assert s2["severity"] == "high"
    matched = {m["photo"] for m in s2["evidence"]["matches"]}
    assert matched >= {2, 3}
    assert all(
        m["matched_check"] == public_ref(uuid.UUID(first["id"])) for m in s2["evidence"]["matches"]
    )


def test_explanation_grammar_for_several_photos(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    rooms = [fixture("01"), fixture("15")]
    signal.run(add_check(session_factory, storage, rooms), CTX)
    copies = [r.resize((r.width // 2, r.height // 2)) for r in rooms]

    result = signal.run(add_check(session_factory, storage, copies, city="Brampton"), CTX)

    assert result.explanation is not None
    assert "Photos 1 and 2 match earlier photos almost exactly from 1 other check" in (
        result.explanation
    )
