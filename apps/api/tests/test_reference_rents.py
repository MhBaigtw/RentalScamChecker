"""Reference rents: CSV loading and the municipality -> CMA lookup (fixture data is synthetic)."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.reference_rents import (
    bedroom_bucket,
    find_reference,
    load_rows,
    normalize_city,
    read_csv,
)

FIXTURE = Path(__file__).parent / "fixtures" / "rms_synthetic.csv"


@pytest.mark.parametrize(
    ("typed", "normalized"),
    [
        ("Oakville", "oakville"),
        ("  OAKVILLE ", "oakville"),
        ("City of Oakville, ON", "oakville"),
        ("Town of Oakville", "oakville"),
        ("Mississauga, Ontario", "mississauga"),
        ("North York", "toronto"),
        ("Scarborough", "toronto"),
        ("Richmond Hill", "richmond hill"),
        # Names that end in "on": the province suffix must not eat them.
        ("Caledon", "caledon"),
        ("Milton", "milton"),
        ("Burlington", "burlington"),
        ("Kingston, ON", "kingston"),
        ("Milton Ont.", "milton"),
    ],
)
def test_normalize_city(typed: str, normalized: str) -> None:
    assert normalize_city(typed) == normalized


@pytest.mark.parametrize(("bedrooms", "bucket"), [(0, 0), (1, 1), (2, 2), (3, 3), (5, 3)])
def test_bedroom_bucket_follows_cmhc(bedrooms: int, bucket: int) -> None:
    assert bedroom_bucket(bedrooms) == bucket


def test_read_csv_parses_published_and_suppressed_rows() -> None:
    rows = read_csv(FIXTURE)

    assert len(rows) == 12
    caledon = next(r for r in rows if r["geography_name"] == "Caledon")
    assert (caledon["avg_rent_cents"], caledon["reliability"]) == (None, None)


def test_read_csv_rejects_a_rent_without_reliability(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    bad.write_text("\n".join([lines[0], lines[1].replace(",a,2025", ",,2025")]), encoding="utf-8")

    with pytest.raises(ValueError, match="bad.csv:2: rent and reliability"):
        read_csv(bad)


def test_read_csv_rejects_missing_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("geography_type,bedrooms\ncma,1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing columns"):
        read_csv(bad)


@pytest.fixture
def loaded(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with session_factory.begin() as s:
        load_rows(s, read_csv(FIXTURE))
    return session_factory


@pytest.mark.integration
def test_loading_twice_does_not_duplicate(loaded: sessionmaker[Session]) -> None:
    with loaded.begin() as s:
        load_rows(s, read_csv(FIXTURE))
    with loaded() as s:
        assert s.execute(sa.text("SELECT count(*) FROM reference_rents")).scalar_one() == 12


@pytest.mark.integration
@pytest.mark.parametrize(
    ("city", "bedrooms", "expected"),
    [
        # The municipality's own, newest figure (2025 beats 2024).
        ("Oakville", 2, (240000, "municipality", "Oakville", 2025, "b")),
        ("City of Oakville, ON", 2, (240000, "municipality", "Oakville", 2025, "b")),
        ("Oakville", 4, (250000, "municipality", "Oakville", 2025, "b")),  # 3+ bucket
        ("Mississauga", 1, (170000, "municipality", "Mississauga", 2025, "a")),
        ("North York", 1, (190000, "municipality", "Toronto", 2025, "a")),
        # Suppressed (**) municipal value -> the CMA's figure.
        ("Caledon", 2, (200000, "cma", "Toronto", 2025, "a")),
        # Reliability d ("use with caution") -> the CMA's figure.
        ("Milton", 2, (200000, "cma", "Toronto", 2025, "a")),
    ],
)
def test_find_reference(
    loaded: sessionmaker[Session], city: str, bedrooms: int, expected: tuple[object, ...]
) -> None:
    with loaded() as s:
        ref = find_reference(s, city, bedrooms)

    assert ref is not None
    assert (ref.rent_cents, ref.level, ref.geography_name, ref.survey_year, ref.reliability) == (
        expected
    )
    assert ref.cma_name == "Toronto"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("city", "bedrooms"),
    [
        ("Burlington", 3),  # suppressed, and its CMA (Hamilton) isn't in the data
        ("Oakville", 1),  # no row for this bucket at all
        ("Kingston", 2),  # city not in the dataset
    ],
)
def test_no_reference_when_nothing_usable(
    loaded: sessionmaker[Session], city: str, bedrooms: int
) -> None:
    with loaded() as s:
        assert find_reference(s, city, bedrooms) is None
