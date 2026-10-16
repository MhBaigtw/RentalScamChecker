import io
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image

from rentalscamchecker.contacts import contact_hash
from rentalscamchecker.main import create_app
from tests.helpers import TEST_PEPPER, UNREACHABLE_DB_URL, image_bytes, make_settings

RAW_PHONE = "(416) 555-0123"
RAW_EMAIL = "Landlord.Joe@Example.com"

FIELDS = {
    "listing_text": "Spacious 2 bedroom basement near Sheridan. Utilities included.",
    "monthly_rent": "1,850",
    "bedrooms": "2",
    "address_text": "1430 Trafalgar Rd",
    "city": "Oakville",
    "submitted_url": "https://www.kijiji.ca/v-apartments-condos/oakville/123",
    "contact_phone": RAW_PHONE,
    "contact_email": RAW_EMAIL,
}

Files = list[tuple[str, tuple[str, bytes, str]]]


def photos(*sizes: tuple[int, int]) -> Files:
    return [
        ("photos", (f"p{i}.jpg", image_bytes(size=size), "image/jpeg"))
        for i, size in enumerate(sizes)
    ]


def three_photos() -> Files:
    return photos((64, 48), (80, 60), (40, 40))


def assert_error_shape(body: dict[str, Any], code: str) -> None:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code


def field_errors(body: dict[str, Any]) -> dict[str, str]:
    return {f["field"]: f["message"] for f in body["error"]["details"]["fields"]}


# --- Validation: rejected before any DB access, so these run without Postgres. ---


@pytest.fixture
def offline_client(photo_dir: Path) -> TestClient:
    return TestClient(create_app(make_settings(UNREACHABLE_DB_URL, photo_dir)))


def test_empty_submission_lists_every_problem(offline_client: TestClient) -> None:
    response = offline_client.post("/checks", data={})

    assert response.status_code == 422
    body = response.json()
    assert_error_shape(body, "validation_error")
    assert set(field_errors(body)) == {
        "listing_text",
        "monthly_rent",
        "bedrooms",
        "address_text",
        "city",
        "photos",
    }


def test_more_than_ten_photos_is_rejected(offline_client: TestClient) -> None:
    response = offline_client.post("/checks", data=FIELDS, files=photos(*[(10, 10)] * 11))

    assert response.status_code == 422
    assert field_errors(response.json()) == {"photos": "Upload between 1 and 10 photos."}


def test_bad_photo_is_reported_by_index_and_nothing_is_stored(
    offline_client: TestClient, photo_dir: Path
) -> None:
    files = photos((10, 10))
    files.append(("photos", ("evil.jpg", b"<?php echo 1; ?>", "image/jpeg")))

    response = offline_client.post("/checks", data=FIELDS, files=files)

    assert response.status_code == 422
    assert field_errors(response.json()) == {
        "photos.1": "Photo 2: the file is not a readable JPEG, PNG or WebP image."
    }
    assert not photo_dir.exists()


def test_invalid_path_id_uses_the_error_shape(offline_client: TestClient) -> None:
    response = offline_client.get("/checks/not-a-uuid")

    assert response.status_code == 422
    body = response.json()
    assert_error_shape(body, "validation_error")
    assert "check_id" in field_errors(body)


def test_unknown_route_uses_the_error_shape(offline_client: TestClient) -> None:
    response = offline_client.get("/nope")

    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")


# --- Create and read back: real Postgres. ---


@pytest.fixture
def client(photo_dir: Path) -> TestClient:
    return TestClient(create_app(make_settings(photo_dir=photo_dir)))


@pytest.fixture
def engine() -> sa.Engine:
    return sa.create_engine(make_settings().database_url)


@pytest.mark.integration
def test_check_with_three_photos_is_created_and_read_back(
    client: TestClient, photo_dir: Path
) -> None:
    created = client.post("/checks", data=FIELDS, files=three_photos())

    assert created.status_code == 202
    check_id = created.json()["check_id"]

    response = client.get(f"/checks/{check_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == check_id
    assert body["status"] == "pending"
    assert body["risk_level"] is None
    assert body["listing"] == {
        "submitted_url": FIELDS["submitted_url"],
        "listing_text": FIELDS["listing_text"],
        "rent_cents": 185_000,
        "bedrooms": 2,
        "address_text": "1430 Trafalgar Rd",
        "city": "Oakville",
    }
    assert [(p["position"], p["width"], p["height"]) for p in body["photos"]] == [
        (0, 64, 48),
        (1, 80, 60),
        (2, 40, 40),
    ]
    assert sorted(c["kind"] for c in body["contacts"]) == ["email", "phone"]
    assert body["signals"] == []

    stored = sorted((photo_dir / "checks" / check_id).iterdir())
    assert len(stored) == 3
    for path in stored:
        with Image.open(io.BytesIO(path.read_bytes())) as img:
            assert img.format == "JPEG"
            assert len(img.getexif()) == 0


@pytest.mark.integration
def test_raw_contact_values_are_not_in_the_database(client: TestClient, engine: sa.Engine) -> None:
    created = client.post("/checks", data=FIELDS, files=three_photos())
    check_id = uuid.UUID(created.json()["check_id"])

    with engine.connect() as conn:
        # Every column of every row belonging to this check, as JSON text.
        rows = conn.execute(
            sa.text(
                """
                SELECT row_to_json(c)::text FROM checks c WHERE c.id = :id
                UNION ALL
                SELECT row_to_json(p)::text FROM check_photos p WHERE p.check_id = :id
                UNION ALL
                SELECT row_to_json(k)::text FROM check_contacts k WHERE k.check_id = :id
                """
            ),
            {"id": check_id},
        ).scalars()
        dump = "\n".join(rows).lower()
        hashes: dict[str, bytes] = dict(
            conn.execute(
                sa.text("SELECT kind, hmac_hash FROM check_contacts WHERE check_id = :id"),
                {"id": check_id},
            ).all()
        )

    assert dump.count(str(check_id)) >= 1  # sanity: the dump really has this check's rows
    for needle in ["4165550123", "416) 555", "555-0123", "landlord.joe", "example.com"]:
        assert needle not in dump

    pepper = TEST_PEPPER.encode()
    assert bytes(hashes["phone"]) == contact_hash("phone", "+14165550123", pepper)
    assert bytes(hashes["email"]) == contact_hash("email", "landlord.joe@example.com", pepper)


@pytest.mark.integration
def test_contacts_are_optional(client: TestClient) -> None:
    fields = {k: v for k, v in FIELDS.items() if not k.startswith("contact_")}

    created = client.post("/checks", data=fields, files=photos((20, 20)))

    assert created.status_code == 202
    body = client.get(f"/checks/{created.json()['check_id']}").json()
    assert body["contacts"] == []


@pytest.mark.integration
def test_unknown_check_returns_404_with_error_shape(client: TestClient) -> None:
    response = client.get(f"/checks/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "No check with this id.", "details": {}}
    }
