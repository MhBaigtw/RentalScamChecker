"""The check graph (graph.py), contact_reuse, and the report's `related` section, against the real
test database. Photos are the CC0/public-domain fixtures; the CLIP model is the real int8 file."""

import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.config import get_settings
from rentalscamchecker.contacts import contact_hash
from rentalscamchecker.graph import (
    NewPhotoLink,
    cluster_size,
    public_ref,
    related_checks,
    store_photo_links,
)
from rentalscamchecker.jobs.worker import Worker, WorkerConfig
from rentalscamchecker.main import create_app
from rentalscamchecker.models import Check, CheckContact, CheckPhoto, PhotoLink
from rentalscamchecker.photos.embeddings import ClipEmbedder
from rentalscamchecker.photos.matching import Thresholds
from rentalscamchecker.photos.model_files import model_path
from rentalscamchecker.pipeline import build_handlers
from rentalscamchecker.signals.base import CheckInput, SignalContext
from rentalscamchecker.signals.contact_reuse import ContactReuseSignal
from rentalscamchecker.signals.photo_reuse_internal import PhotoReuseInternalSignal
from rentalscamchecker.signals.registry import enabled_signals
from rentalscamchecker.storage import LocalDiskStorage
from tests.helpers import TEST_PEPPER, UNREACHABLE_DB_URL, make_settings
from tests.test_photo_signals import add_check, fixture, jpeg

pytestmark = pytest.mark.integration

CTX = SignalContext(settings=make_settings(UNREACHABLE_DB_URL))
PEPPER = TEST_PEPPER.encode()


@pytest.fixture(scope="module")
def embedder() -> ClipEmbedder:
    return ClipEmbedder(model_path("clip-vit-b32-int8", get_settings().photo_model_path))


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "photos")


# --- through the API and the worker ---


class Harness:
    """POST /checks, run the worker until the queue is empty, GET the report."""

    def __init__(self, session_factory: sessionmaker[Session], photo_dir: Path) -> None:
        settings = make_settings(
            photo_dir=photo_dir,
            enabled_signals="photo_reuse_internal,contact_reuse",
            anthropic_api_key=SecretStr("unused"),
        )
        self.client = TestClient(create_app(settings))
        self.worker = Worker(
            session_factory,
            build_handlers(session_factory, enabled_signals(settings, session_factory), settings),
            WorkerConfig(concurrency=1, lease_seconds=30, job_timeout_seconds=20),
        )

    def submit(
        self,
        images: list[Image.Image],
        address: str,
        city: str,
        text: str = "2 bed near Sheridan, laundry in unit.",
        phone: str | None = None,
    ) -> str:
        data = {
            "listing_text": text,
            "monthly_rent": "1800",
            "bedrooms": "2",
            "address_text": address,
            "city": city,
        }
        if phone is not None:
            data["contact_phone"] = phone
        created = self.client.post(
            "/checks",
            data=data,
            files=[
                ("photos", (f"p{i}.jpg", jpeg(im), "image/jpeg")) for i, im in enumerate(images)
            ],
        )
        assert created.status_code == 202, created.text
        while self.worker.run_once():
            pass
        check_id: str = created.json()["check_id"]
        return check_id

    def report(self, check_id: str) -> dict[str, Any]:
        body: dict[str, Any] = self.client.get(f"/checks/{check_id}").json()
        return body


def signal(report: dict[str, Any], name: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [s for s in report["signals"] if s["name"] == name]
    assert len(found) == 1
    return found[0]


def ref(check_id: str) -> str:
    return public_ref(uuid.UUID(check_id))


def test_three_checks_sharing_a_phone_and_a_photo_are_linked_in_each_others_reports(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    """The M6 'Done when'. The phone is typed in the form for the first two checks (formatted
    differently) and appears only inside the listing text of the third; the shared photo is
    resized / re-encoded in each. The first report is read last: links appear in it even though
    the other checks were submitted after it was complete."""
    h = Harness(session_factory, photo_dir)
    room = fixture("01")
    a = h.submit([room, fixture("15")], "1430 Trafalgar Rd", "Oakville", phone="(905) 555-0142")
    b = h.submit(
        [room.resize((room.width // 2, room.height // 2))],
        "3880 Duke of York Blvd",
        "Mississauga",
        phone="905-555-0142",
    )
    c = h.submit(
        [room, fixture("19")],
        "200 Queen St S",
        "Brampton",
        text="Furnished room, all inclusive. Text me at 905 555 0142 to book a viewing.",
    )

    reports = {x: h.report(x) for x in (a, b, c)}
    for me, report in reports.items():
        others = {x for x in (a, b, c) if x != me}
        related = report["related"]
        assert {r["check"] for r in related["checks"]} == {ref(x) for x in others}
        assert all(r["linked_by"] == ["phone", "photo"] for r in related["checks"])
        assert all(r["same_address"] is False for r in related["checks"])
        assert (related["total"], related["at_different_address"]) == (2, 2)
        assert (related["cluster_size"], related["cluster_size_capped"]) == (3, False)
        # No other check's id anywhere in the report: only public refs.
        assert not any(x in str(report) for x in others)

    # contact_reuse ran when each check was submitted, so it saw only the earlier ones.
    assert signal(reports[a], "contact_reuse")["severity"] == "none"
    c_contacts = signal(reports[c], "contact_reuse")
    assert c_contacts["evidence"]["contacts_checked"] == [
        {"kind": "phone", "source": "text", "other_checks": 2}
    ]
    assert c_contacts["evidence"]["other_cities"] == ["Mississauga", "Oakville"]
    # B reused A's photo at another address: B is high on its own evidence, so C's contact link
    # to B is high (M7, ED-027). A is low on its own evidence.
    assert c_contacts["severity"] == "high"
    assert c_contacts["evidence"]["linked_high_risk"] == [
        {"check": ref(b), "own_signals": ["photo_reuse_internal"]}
    ]
    assert signal(reports[c], "photo_reuse_internal")["severity"] == "high"
    assert [reports[x]["risk_level"] for x in (a, b, c)] == ["low", "high", "high"]


def test_two_checks_sharing_only_a_photo_at_the_same_address_is_informational(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    h = Harness(session_factory, photo_dir)
    room = fixture("15")
    first = h.submit([room], "1430 Trafalgar Rd", "Oakville")
    again = h.submit([room], "1430 Trafalgar Road, Unit 5", "oakville")

    report = h.report(again)
    photos = signal(report, "photo_reuse_internal")
    assert (photos["status"], photos["severity"]) == ("ok", "none")
    assert photos["evidence"]["same_address_matches"] == 1
    assert photos["evidence"]["other_checks_at_different_address"] == 0
    assert signal(report, "contact_reuse")["status"] == "skipped"  # no contacts given
    related = report["related"]
    assert related["checks"] == [
        {
            "check": ref(first),
            "created_on": related["checks"][0]["created_on"],
            "city": "Oakville",
            "risk_level": "low",
            "linked_by": ["photo"],
            "same_address": True,
        }
    ]
    assert (related["total"], related["at_different_address"], related["cluster_size"]) == (
        1,
        0,
        2,
    )
    # The earlier report shows the link too, from its side.
    assert h.report(first)["related"]["checks"][0]["check"] == ref(again)


def test_related_is_absent_until_the_check_is_complete(
    session_factory: sessionmaker[Session], photo_dir: Path
) -> None:
    h = Harness(session_factory, photo_dir)
    created = h.client.post(
        "/checks",
        data={
            "listing_text": "x",
            "monthly_rent": "1800",
            "bedrooms": "2",
            "address_text": "1 Main St",
            "city": "Milton",
        },
        files=[("photos", ("p.jpg", jpeg(fixture("01")), "image/jpeg"))],
    )
    assert h.report(created.json()["check_id"])["related"] is None


# --- graph queries on seeded rows ---


def seed_check(
    sf: sessionmaker[Session],
    address: str = "1 Main St",
    city: str = "Milton",
    phones: tuple[str, ...] = (),
    emails: tuple[str, ...] = (),
    risk_level: str | None = None,
    source: str = "field",
) -> uuid.UUID:
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
                risk_level=risk_level,
                independent_risk_level=risk_level,
            )
        )
        s.flush()
        for kind, values in (("phone", phones), ("email", emails)):
            for value in values:
                s.add(
                    CheckContact(
                        check_id=check_id,
                        kind=kind,
                        source=source,
                        hmac_hash=contact_hash(kind, value, PEPPER),  # type: ignore[arg-type]
                    )
                )
    return check_id


def seed_photo_link(sf: sessionmaker[Session], a: uuid.UUID, b: uuid.UUID) -> None:
    with sf.begin() as s:
        photos = []
        for check_id in (a, b):
            photo = CheckPhoto(
                check_id=check_id,
                position=0,
                storage_key=f"k/{uuid.uuid4()}",
                width=10,
                height=10,
            )
            s.add(photo)
            photos.append(photo)
        s.flush()
        store_photo_links(s, [NewPhotoLink(photos[0].id, a, photos[1].id, b, "hash", 3, 4, 0.97)])


def test_cluster_follows_chains_of_mixed_links(session_factory: sessionmaker[Session]) -> None:
    # a -phone- b -photo- c -email- d ; e is unrelated.
    a = seed_check(session_factory, phones=("+19055550142",))
    b = seed_check(session_factory, phones=("+19055550142",))
    c = seed_check(session_factory, emails=("x@example.com",))
    d = seed_check(session_factory, emails=("x@example.com",))
    e = seed_check(session_factory, phones=("+14165550199",))
    seed_photo_link(session_factory, b, c)

    with session_factory() as s:
        assert cluster_size(s, a).size == 4
        assert cluster_size(s, d).size == 4
        assert cluster_size(s, e).size == 1
        related, total = related_checks(s, a, "1 Main St", "Milton")
    assert (total, [r.check_id for r in related]) == (1, [b])
    assert related[0].linked_by == ("phone",)


def test_cluster_size_stops_at_the_cap(session_factory: sessionmaker[Session]) -> None:
    hub = seed_check(session_factory, phones=("+19055550142",))
    for _ in range(9):
        seed_check(session_factory, phones=("+19055550142",))

    with session_factory() as s:
        assert cluster_size(s, hub).size == 10
        capped = cluster_size(s, hub, max_checks=5)
    assert (capped.size, capped.capped) == (5, True)


def test_related_counts_all_but_lists_at_most_the_limit(
    session_factory: sessionmaker[Session],
) -> None:
    hub = seed_check(session_factory, phones=("+19055550142",))
    for _ in range(4):
        seed_check(session_factory, phones=("+19055550142",))

    with session_factory() as s:
        related, total = related_checks(s, hub, "1 Main St", "Milton", limit=2)
    assert (len(related), total) == (2, 4)


def test_photo_links_are_stored_both_ways_and_idempotently(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder)
    room = fixture("01")
    first = add_check(session_factory, storage, [room])
    second = add_check(
        session_factory, storage, [room.resize((400, 300))], "3880 Duke of York Blvd"
    )
    signal.run(first, CTX)
    signal.run(second, CTX)
    signal.run(second, CTX)  # a retried job

    with session_factory() as s:
        links = s.scalars(sa.select(PhotoLink)).all()
    assert sorted((ln.check_id, ln.other_check_id) for ln in links) == sorted(
        [(first.check_id, second.check_id), (second.check_id, first.check_id)]
    )
    assert {ln.match_type for ln in links} == {"hash"}


def test_embedding_only_matches_create_links_too(
    session_factory: sessionmaker[Session], storage: LocalDiskStorage, embedder: ClipEmbedder
) -> None:
    """The graph uses the full M5 rule, not exact hashes. Hash thresholds of -1 switch hash
    matching off, so this pair can only link through the embedding."""
    no_hashes = Thresholds(single_hash=-1, both_hashes=-1, cosine=0.9)
    signal = PhotoReuseInternalSignal(session_factory, storage, embedder, no_hashes)
    room = fixture("01")
    first = add_check(session_factory, storage, [room])
    second = add_check(session_factory, storage, [room.resize((400, 300))], "9 Other St")
    signal.run(first, CTX)
    result = signal.run(second, CTX)

    assert result.evidence["matches"][0]["match_type"] == "embedding"
    with session_factory() as s:
        types = s.scalars(sa.select(PhotoLink.match_type)).all()
        assert cluster_size(s, first.check_id).size == 2
    assert types == ["embedding", "embedding"]


def test_deleting_a_check_removes_its_links(session_factory: sessionmaker[Session]) -> None:
    a = seed_check(session_factory)
    b = seed_check(session_factory)
    seed_photo_link(session_factory, a, b)

    with session_factory.begin() as s:
        s.execute(sa.delete(Check).where(Check.id == a))
    with session_factory() as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(PhotoLink)) == 0
        assert cluster_size(s, b).size == 1


# --- contact_reuse severities ---


def run_contact_reuse(sf: sessionmaker[Session], check_id: uuid.UUID) -> Any:
    with sf() as s:
        check = s.get_one(Check, check_id)
        data = CheckInput(check.id, "x", 150000, 2, check.address_text, check.city, None)
    return ContactReuseSignal(sf).run(data, CTX)


PHONE = "+19055550142"


def test_contact_never_seen_is_none(session_factory: sessionmaker[Session]) -> None:
    me = seed_check(session_factory, phones=(PHONE,))
    result = run_contact_reuse(session_factory, me)
    assert (result.status, result.severity, result.evidence["other_checks"]) == ("ok", "none", 0)
    assert "We haven't seen it in any other check" in result.explanation


def test_contact_seen_only_at_the_same_address_is_none(
    session_factory: sessionmaker[Session],
) -> None:
    seed_check(session_factory, "12 King St W", "Toronto", phones=(PHONE,))
    me = seed_check(session_factory, "12 king street west, Apt 3", "toronto", phones=(PHONE,))
    result = run_contact_reuse(session_factory, me)
    assert result.severity == "none"
    assert result.evidence["other_checks"] == 1
    assert result.evidence["other_checks_at_different_address"] == 0
    assert "same address" in result.explanation


def test_contact_at_another_address_in_the_same_city_is_low(
    session_factory: sessionmaker[Session],
) -> None:
    seed_check(session_factory, "12 King St W", "Toronto", phones=(PHONE,))
    seed_check(session_factory, "40 Bay St", "Toronto", phones=(PHONE,))
    me = seed_check(session_factory, "1 Front St", "Toronto", phones=(PHONE,))
    result = run_contact_reuse(session_factory, me)
    assert result.severity == "low"
    assert (result.evidence["other_addresses"], result.evidence["other_cities"]) == (2, [])
    assert "2 other addresses in the same city" in result.explanation


def test_contact_in_another_city_is_medium(session_factory: sessionmaker[Session]) -> None:
    seed_check(session_factory, "12 King St W", "Toronto", emails=("a@example.com",))
    me = seed_check(
        session_factory, "1 Main St", "Milton", phones=(PHONE,), emails=("a@example.com",)
    )
    result = run_contact_reuse(session_factory, me)
    assert result.severity == "medium"
    assert result.evidence["other_cities"] == ["Toronto"]
    assert result.evidence["contacts_checked"] == [
        {"kind": "email", "source": "field", "other_checks": 1},
        {"kind": "phone", "source": "field", "other_checks": 0},
    ]
    assert result.explanation.startswith(
        "We compared the phone number and email in this listing with all earlier checks. "
        "The same email was also used for 1 other address, including in another city (Toronto)."
    )


def test_contact_linked_to_a_high_risk_check_is_high(
    session_factory: sessionmaker[Session],
) -> None:
    seed_check(session_factory, "1 Main St", "Milton", phones=(PHONE,), risk_level="high")
    me = seed_check(session_factory, "1 Main St", "Milton", phones=(PHONE,))
    result = run_contact_reuse(session_factory, me)
    assert result.severity == "high"
    assert result.evidence["linked_independent_levels"] == {"high": 1}
    assert result.evidence["linked_checks"][0]["check"] == public_ref(
        result_check_ids(session_factory, exclude=me)[0]
    )


def test_no_contacts_is_skipped(session_factory: sessionmaker[Session]) -> None:
    me = seed_check(session_factory)
    result = run_contact_reuse(session_factory, me)
    assert (result.status, result.severity, result.evidence) == (
        "skipped",
        None,
        {"reason": "no_contacts"},
    )


def test_contacts_from_listing_text_link_like_form_fields(
    session_factory: sessionmaker[Session],
) -> None:
    seed_check(session_factory, "12 King St W", "Toronto", phones=(PHONE,), source="field")
    me = seed_check(session_factory, "1 Main St", "Milton", phones=(PHONE,), source="text")
    result = run_contact_reuse(session_factory, me)
    assert result.severity == "medium"
    assert result.evidence["contacts_checked"] == [
        {"kind": "phone", "source": "text", "other_checks": 1}
    ]


def result_check_ids(sf: sessionmaker[Session], exclude: uuid.UUID) -> list[uuid.UUID]:
    with sf() as s:
        return list(s.scalars(sa.select(Check.id).where(Check.id != exclude)))
