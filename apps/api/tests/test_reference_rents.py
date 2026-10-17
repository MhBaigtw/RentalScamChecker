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

    assert len(rows) == 31
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
        assert s.execute(sa.text("SELECT count(*) FROM reference_rents")).scalar_one() == 31


@pytest.mark.integration
@pytest.mark.parametrize(
    ("city", "bedrooms", "expected"),
    [
        # Step 1: the municipality's vacant-unit figure; newest survey (2025 beats 2024).
        ("Oakville", 2, (260000, "municipality", "vacant_units", "Oakville", 2025, "b")),
        (
            "City of Oakville, ON",
            2,
            (260000, "municipality", "vacant_units", "Oakville", 2025, "b"),
        ),
        ("Mississauga", 1, (200000, "municipality", "vacant_units", "Mississauga", 2025, "a")),
        # Step 2: estimated municipal vacant = own all-units x CMA vacant / CMA all-units.
        # Oakville 3+: 2,500 x (2,800 / 2,300 = 1.2174) = 3,044; reliability = worst input (b).
        (
            "Oakville",
            4,
            (304400, "municipality", "municipality_estimated_vacant", "Oakville", 2025, "b"),
        ),
        # Toronto 1-bed: own vacant is "d"; 1,750 x (1,950 / 1,800 = 1.0833) = 1,896.
        (
            "North York",
            1,
            (189600, "municipality", "municipality_estimated_vacant", "Toronto", 2025, "a"),
        ),
        # Step 3: no usable own figure at all -> the CMA's vacant figure.
        ("Oakville", 1, (195000, "cma", "vacant_units", "Toronto", 2025, "a")),  # no own row
        ("Caledon", 2, (250000, "cma", "vacant_units", "Toronto", 2025, "a")),  # suppressed
        ("Milton", 2, (250000, "cma", "vacant_units", "Toronto", 2025, "a")),  # own is "d"
        # Step 4: no CMA vacant figure, so no estimate either -> the CMA's all-units figure.
        ("Hamilton", 2, (160000, "cma", "all_units", "Hamilton", 2025, "a")),
        ("Hamilton", 1, (140000, "cma", "all_units", "Hamilton", 2025, "a")),
    ],
)
def test_find_reference_fallback_order(
    loaded: sessionmaker[Session], city: str, bedrooms: int, expected: tuple[object, ...]
) -> None:
    with loaded() as s:
        ref = find_reference(s, city, bedrooms)

    assert ref is not None
    got = (
        ref.rent_cents,
        ref.level,
        ref.rent_basis,
        ref.geography_name,
        ref.survey_year,
        ref.reliability,
    )
    assert got == expected


@pytest.mark.integration
def test_estimate_records_its_ratio_and_inputs(loaded: sessionmaker[Session]) -> None:
    with loaded() as s:
        ref = find_reference(s, "Oakville", 3)

    assert ref is not None and ref.estimate is not None
    assert ref.estimate.ratio == 1.2174
    assert (
        ref.estimate.municipality_all_units_cents,
        ref.estimate.cma_vacant_cents,
        ref.estimate.cma_all_units_cents,
    ) == (250000, 280000, 230000)


@pytest.mark.integration
def test_reference_records_the_cmhc_row_it_came_from(loaded: sessionmaker[Session]) -> None:
    with loaded() as s:
        ref = find_reference(s, "Oakville", 2)

    assert ref is not None
    assert ref.source_geography == "Zone 23 - Oakville"
    assert ref.source_url == "https://example.invalid/oakville-vacant_units"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("city", "bedrooms"),
    [
        ("Burlington", 3),  # suppressed everywhere, municipality and CMA, both bases
        ("Kingston", 2),  # city not in the dataset
    ],
)
def test_no_reference_when_nothing_usable(
    loaded: sessionmaker[Session], city: str, bedrooms: int
) -> None:
    with loaded() as s:
        assert find_reference(s, city, bedrooms) is None
