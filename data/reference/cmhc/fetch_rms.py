"""Build the CMHC reference-rent CSV from CMHC's official Rental Market Survey data tables
(Excel workbooks, one per CMA), with provenance on every row.

Run (from the repo root, with the API venv plus openpyxl, which the app itself doesn't need):
    apps/api/.venv/Scripts/python.exe -m pip install openpyxl
    apps/api/.venv/Scripts/python.exe data/reference/cmhc/fetch_rms.py --year 2025

Licence: CMHC Licence Agreement for the Use of Data (README.md in this folder). The workbooks are
the "Rental Market Survey Data Tables" CMHC offers for download (cmhc.ca/rental-market-report-data).
Run by hand about once a year, after CMHC publishes the October survey (December).

Two tables per workbook, both for private row (townhouse) and apartment units:
- 3.1.9 "Average Rents ($) of Vacant and Occupied Units by Zone and Bedroom Type": we keep the
  VACANT-unit averages (rent_basis = vacant_units): what landlords were asking for empty units,
  closer to what a new tenant pays than the overall average (ED-021).
- 3.1.2 "Average Rents ($), by Zone and Bedroom Type", October column: the overall average of all
  units, mostly occupied ones (rent_basis = all_units). Used as the fallback.
Suppressed values (**) are written with an empty rent and reliability. Each published value keeps
its reliability letter (a excellent, b very good, c good, d poor).
"""

import argparse
import csv
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import openpyxl

BASE_URL = (
    "https://assets.cmhc-schl.gc.ca/sites/cmhc/professional/housing-markets-data-and-research/"
    "housing-data-tables/rental-market/rental-market-report-data-tables/{year}/rmr-{cma}-{year}-en.xlsx"
)
USER_AGENT = "RentalScamChecker reference-data fetch (https://github.com/MhBaigtw/RentalScamChecker)"
BEDROOMS = {"Studio": 0, "1 Bedroom": 1, "2 Bedroom": 2, "3 Bedroom +": 3}
TABLES = {"vacant_units": "Table 3.1.9", "all_units": "Table 3.1.2"}


@dataclass(frozen=True)
class Target:
    """One of our geographies, read from one row of a CMHC workbook."""

    workbook: str  # file slug, e.g. "toronto"
    row_label: str  # the zone column text in the workbook
    geography_type: str  # "cma" or "csd"
    geography_code: str  # StatCan CMA or CSD code
    geography_name: str
    cma_name: str


# Sheridan College's campus cities (Oakville, Brampton, Mississauga) and their commuting area.
# Several municipalities share one CMHC zone; the CSV row records which zone a value came from.
TARGETS = [
    Target("toronto", "Toronto CMA", "cma", "535", "Toronto", "Toronto"),
    Target("toronto", "City of Toronto (Zones 1-17)", "csd", "3520005", "Toronto", "Toronto"),
    Target("toronto", "Mississauga City (Zones 18-20)", "csd", "3521005", "Mississauga", "Toronto"),
    Target("toronto", "Brampton City (Zones 21-22)", "csd", "3521010", "Brampton", "Toronto"),
    Target("toronto", "Zone 23 - Oakville", "csd", "3524001", "Oakville", "Toronto"),
    Target("toronto", "Zone 24 - Caledon", "csd", "3521024", "Caledon", "Toronto"),
    Target("toronto", "Zone 25 - Richmond Hill/Vaughan/King", "csd", "3519038", "Richmond Hill", "Toronto"),
    Target("toronto", "Zone 25 - Richmond Hill/Vaughan/King", "csd", "3519028", "Vaughan", "Toronto"),
    Target("toronto", "Zone 25 - Richmond Hill/Vaughan/King", "csd", "3519049", "King", "Toronto"),
    Target("toronto", "Zone 27 - Markham", "csd", "3519036", "Markham", "Toronto"),
    Target("toronto", "Zone 29 - Milton/Halton Hills", "csd", "3524009", "Milton", "Toronto"),
    Target("toronto", "Zone 29 - Milton/Halton Hills", "csd", "3524015", "Halton Hills", "Toronto"),
    Target("hamilton", "Hamilton CMA", "cma", "537", "Hamilton", "Hamilton"),
    Target("hamilton", "Hamilton City (Zones 1-6)", "csd", "3525005", "Hamilton", "Hamilton"),
    Target("hamilton", "Zone 8 - Burlington", "csd", "3524002", "Burlington", "Hamilton"),
]

FIELDS = [
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
]


def _cell(value: object) -> str:
    return "" if value is None else str(value).strip()


def _sheet_rows(wb: openpyxl.Workbook, sheet: str) -> list[list[str]]:
    return [[_cell(c) for c in row] for row in wb[sheet].iter_rows(values_only=True)]


def _value_columns(rows: list[list[str]], basis: str, year: int) -> dict[int, int]:
    """bedroom bucket -> column index of the value we want (its reliability is the next column)."""
    bedroom_header = next(r for r in rows if "Studio" in r)
    second_header = rows[rows.index(bedroom_header) + 1]
    wanted = "Vacant Units" if basis == "vacant_units" else f"Oct-{year % 100:02d}"
    columns: dict[int, int] = {}
    for label, bucket in BEDROOMS.items():
        start = bedroom_header.index(label)
        # Within this bedroom's group of columns, find the sub-header we want.
        end = next(
            (i for i in range(start + 1, len(bedroom_header)) if bedroom_header[i]),
            len(bedroom_header),
        )
        hits = [i for i in range(start, end) if second_header[i] == wanted]
        if len(hits) != 1:
            raise ValueError(f"expected one {wanted!r} column under {label!r}, found {hits}")
        columns[bucket] = hits[0]
    return columns


def parse_workbook(data: bytes, slug: str, year: int, url: str) -> list[dict[str, object]]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[dict[str, object]] = []
    for basis, sheet in TABLES.items():
        rows = _sheet_rows(wb, sheet)
        title = next(c for r in rows for c in r if c.startswith(sheet.removeprefix("Table ")))
        if basis == "vacant_units" and f"(October {year})" not in title:
            raise ValueError(f"{sheet} is not for October {year}: {title!r}")
        columns = _value_columns(rows, basis, year)
        by_label = {r[0]: r for r in rows if r and r[0]}
        for target in (t for t in TARGETS if t.workbook == slug):
            row = by_label.get(target.row_label)
            if row is None:
                raise ValueError(f"{slug} {sheet}: no row {target.row_label!r}")
            for bucket, col in columns.items():
                value, reliability = row[col], row[col + 1]
                suppressed = value in ("", "**")
                if not suppressed and reliability not in ("a", "b", "c", "d"):
                    raise ValueError(f"{target.row_label} {sheet}: odd reliability {reliability!r}")
                out.append(
                    {
                        "geography_type": target.geography_type,
                        "geography_code": target.geography_code,
                        "geography_name": target.geography_name,
                        "cma_name": target.cma_name,
                        "source_geography": target.row_label,
                        "rent_basis": basis,
                        "bedrooms": bucket,
                        "avg_rent_cents": "" if suppressed else int(value.replace(",", "")) * 100,
                        "reliability": "" if suppressed else reliability,
                        "survey_year": year,
                        "survey_month": 10,
                        "source": f"CMHC Rental Market Survey, {slug.title()} CMA data tables "
                        f"{year}, {sheet}",
                        "source_url": url,
                    }
                )
    return out


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args.add_argument("--year", type=int, required=True)
    args.add_argument("--out", type=Path, default=None)
    opts = args.parse_args()
    out = opts.out or Path(__file__).with_name(f"rms_average_rent_{opts.year}_10.csv")

    rows: list[dict[str, object]] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True) as c:
        for slug in sorted({t.workbook for t in TARGETS}):
            url = BASE_URL.format(year=opts.year, cma=slug)
            response = c.get(url)
            response.raise_for_status()
            found = parse_workbook(response.content, slug, opts.year, url)
            print(json.dumps({"workbook": slug, "rows": len(found), "bytes": len(response.content)}))
            rows += found
            time.sleep(1.0)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
