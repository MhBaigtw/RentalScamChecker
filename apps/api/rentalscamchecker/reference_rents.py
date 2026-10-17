"""CMHC reference rents: load the CSV written by data/reference/cmhc/fetch_rms.py, and look up the
reference for a city and bedroom count (ED-021). Deterministic; no network.

Load (from apps/api, with the venv):
    python -m rentalscamchecker.reference_rents path/to/rms_average_rent_2025_10.csv

The loader lives in the app package (not in data/reference/, as CLAUDE.md's map suggests) so it
is type-checked and tested with the rest of the code; data/reference/ holds the fetch script,
the dataset and its licence notes.
"""

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from rentalscamchecker.models import ReferenceRent

# Reliability letters we use. "d" (poor, CMHC says "use with caution") falls back to the CMA.
USABLE_RELIABILITY = frozenset({"a", "b", "c"})
# Former municipalities and common district names inside the City of Toronto.
CITY_ALIASES = {
    "north york": "toronto",
    "scarborough": "toronto",
    "etobicoke": "toronto",
    "east york": "toronto",
    "york": "toronto",
}
# rent_basis of an estimated value: municipality all-units x (CMA vacant / CMA all-units).
ESTIMATED_VACANT = "municipality_estimated_vacant"
_REQUIRED = (
    "geography_type",
    "geography_code",
    "geography_name",
    "cma_name",
    "source_geography",
    "rent_basis",
    "bedrooms",
    "avg_rent_cents",
    "reliability",
    "survey_year",
    "survey_month",
    "source",
    "source_url",
)


@dataclass(frozen=True)
class Estimate:
    """How an estimated municipal vacant rent was computed (ED-021)."""

    # CMA vacant / CMA all-units for the same bedroom bucket, rounded to 4 decimals.
    ratio: float
    municipality_all_units_cents: int
    cma_vacant_cents: int
    cma_all_units_cents: int


@dataclass(frozen=True)
class Reference:
    rent_cents: int
    reliability: str
    geography_name: str
    # "municipality" when the city's own figure was used, "cma" for the metropolitan fallback.
    level: str
    # "vacant_units" (asking rents of empty units), "municipality_estimated_vacant" (see Estimate)
    # or "all_units" (overall average).
    rent_basis: str
    # The CMHC table row the value was read from, e.g. "Zone 29 - Milton/Halton Hills".
    source_geography: str
    cma_name: str
    survey_year: int
    survey_month: int
    source: str
    source_url: str
    estimate: Estimate | None = None


def normalize_city(raw: str) -> str:
    """'City of Oakville, ON' -> 'oakville'; 'North York' -> 'toronto'."""
    city = raw.casefold().strip()
    # Province suffix only after a comma or space: "Caledon", "Milton" also end in "on".
    city = re.sub(r"(,\s*|\s+)(on|ont|ontario)\.?$", "", city)
    city = re.sub(r"^(city|town|township|municipality) of\s+", "", city)
    city = " ".join(re.sub(r"[^\w\s-]", " ", city).split())
    return CITY_ALIASES.get(city, city)


def bedroom_bucket(bedrooms: int) -> int:
    """CMHC buckets: studio (0), 1, 2, 3+ (3)."""
    return min(max(bedrooms, 0), 3)


def read_csv(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(missing)}")
        rows: list[dict[str, object]] = []
        for line, raw in enumerate(reader, start=2):
            try:
                rows.append(_parse_row(raw))
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path}:{line}: {exc}") from exc
    return rows


def _parse_row(raw: dict[str, str]) -> dict[str, object]:
    rent = raw["avg_rent_cents"].strip()
    reliability = raw["reliability"].strip() or None
    if bool(rent) != bool(reliability):
        raise ValueError("rent and reliability must both be present or both empty")
    return {
        "geography_type": raw["geography_type"],
        "geography_code": raw["geography_code"],
        "geography_name": raw["geography_name"],
        "cma_name": raw["cma_name"],
        "source_geography": raw["source_geography"],
        "rent_basis": raw["rent_basis"],
        "bedrooms": int(raw["bedrooms"]),
        "avg_rent_cents": int(rent) if rent else None,
        "reliability": reliability,
        "survey_year": int(raw["survey_year"]),
        "survey_month": int(raw["survey_month"]),
        "source": raw["source"],
        "source_url": raw["source_url"],
    }


def load_rows(session: Session, rows: list[dict[str, object]]) -> int:
    """Upsert rows (re-loading a file is safe). Returns the number of rows written."""
    for row in rows:
        stmt = insert(ReferenceRent).values(**row)
        session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_reference_rents_geography_basis_bedrooms_survey",
                set_={
                    k: stmt.excluded[k]
                    for k in (
                        "geography_name",
                        "cma_name",
                        "source_geography",
                        "avg_rent_cents",
                        "reliability",
                        "source",
                        "source_url",
                    )
                },
            )
        )
    return len(rows)


def find_reference(session: Session, city: str, bedrooms: int) -> Reference | None:
    """The newest usable reference rent for this city and bedroom count (ED-021).

    Tried in this order; a figure counts only if published with reliability a, b or c:
    1. the municipality's vacant-unit average;
    2. an estimated municipal vacant rent: the municipality's all-units average scaled by the
       CMA's vacant/all-units ratio for the same bedroom bucket (needs all three figures);
    3. the CMA's vacant-unit average;
    4. the CMA's all-units average.
    None if the city isn't in the dataset or nothing usable is published.

    Step 2 replaced "CMA vacant before municipal all-units": the CMA figure overstated the
    baseline for cheaper municipalities (Milton 2-bed: $2,530 vs its own $1,667 all-units).
    """
    bucket = bedroom_bucket(bedrooms)
    name = normalize_city(city)
    cma_name = _cma_of(session, name)
    if cma_name is None:
        return None
    cma = cma_name.casefold()
    own_vacant = _usable(_newest(session, "csd", name, "vacant_units", bucket))
    if own_vacant is not None:
        return _reference(own_vacant, "municipality")
    own_all = _usable(_newest(session, "csd", name, "all_units", bucket))
    cma_vacant = _usable(_newest(session, "cma", cma, "vacant_units", bucket))
    cma_all = _usable(_newest(session, "cma", cma, "all_units", bucket))
    if own_all is not None and cma_vacant is not None and cma_all is not None:
        return _estimated(own_all, cma_vacant, cma_all)
    if cma_vacant is not None:
        return _reference(cma_vacant, "cma")
    if cma_all is not None:
        return _reference(cma_all, "cma")
    return None


def _usable(row: ReferenceRent | None) -> ReferenceRent | None:
    if row is None or row.avg_rent_cents is None or row.reliability not in USABLE_RELIABILITY:
        return None
    return row


def _estimated(
    own_all: ReferenceRent, cma_vacant: ReferenceRent, cma_all: ReferenceRent
) -> Reference:
    assert own_all.avg_rent_cents and cma_vacant.avg_rent_cents and cma_all.avg_rent_cents  # noqa: S101
    ratio = round(cma_vacant.avg_rent_cents / cma_all.avg_rent_cents, 4)
    # Whole dollars, like every other CMHC figure.
    cents = round(own_all.avg_rent_cents * ratio / 100) * 100
    worst = max(r.reliability or "d" for r in (own_all, cma_vacant, cma_all))
    base = _reference(own_all, "municipality")
    return Reference(
        **{
            **base.__dict__,
            "rent_cents": cents,
            "reliability": worst,
            "rent_basis": ESTIMATED_VACANT,
            "estimate": Estimate(
                ratio=ratio,
                municipality_all_units_cents=own_all.avg_rent_cents,
                cma_vacant_cents=cma_vacant.avg_rent_cents,
                cma_all_units_cents=cma_all.avg_rent_cents,
            ),
        }
    )


def _cma_of(session: Session, city: str) -> str | None:
    """The CMA a municipality belongs to, from any of its rows (suppressed rows count)."""
    return session.scalars(
        select(ReferenceRent.cma_name)
        .where(
            ReferenceRent.geography_type == "csd", func.lower(ReferenceRent.geography_name) == city
        )
        .limit(1)
    ).first()


def _newest(
    session: Session, geography_type: str, name: str, basis: str, bucket: int
) -> ReferenceRent | None:
    return session.scalars(
        select(ReferenceRent)
        .where(
            ReferenceRent.geography_type == geography_type,
            func.lower(ReferenceRent.geography_name) == name,
            ReferenceRent.rent_basis == basis,
            ReferenceRent.bedrooms == bucket,
        )
        .order_by(ReferenceRent.survey_year.desc(), ReferenceRent.survey_month.desc())
        .limit(1)
    ).first()


def _reference(row: ReferenceRent, level: str) -> Reference:
    assert row.avg_rent_cents is not None and row.reliability is not None  # noqa: S101
    return Reference(
        rent_cents=row.avg_rent_cents,
        reliability=row.reliability,
        geography_name=row.geography_name,
        level=level,
        rent_basis=row.rent_basis,
        source_geography=row.source_geography,
        cma_name=row.cma_name,
        survey_year=row.survey_year,
        survey_month=row.survey_month,
        source=row.source,
        source_url=row.source_url,
    )


def main() -> None:
    from rentalscamchecker.config import get_settings
    from rentalscamchecker.db import make_engine

    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m rentalscamchecker.reference_rents <csv>")
    settings = get_settings()
    engine = make_engine(settings.database_url, settings.database_connect_timeout)
    rows = read_csv(Path(sys.argv[1]))
    with sessionmaker(engine).begin() as session:
        written = load_rows(session, rows)
    print(f"loaded {written} reference rent rows from {sys.argv[1]}")


if __name__ == "__main__":
    main()
