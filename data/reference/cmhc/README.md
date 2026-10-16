# CMHC Rental Market Survey: average rents

Reference data for the `price_vs_market` signal (ED-021).

## Source

- Publisher: Canada Mortgage and Housing Corporation (CMHC).
- Product: Rental Market Survey (RMS), October 2025, "Rental Market Survey Data Tables" (Excel),
  one workbook per CMA, offered for download at cmhc.ca/rental-market-report-data:
  - Toronto CMA: https://assets.cmhc-schl.gc.ca/sites/cmhc/professional/housing-markets-data-and-research/housing-data-tables/rental-market/rental-market-report-data-tables/2025/rmr-toronto-2025-en.xlsx
  - Hamilton CMA: https://assets.cmhc-schl.gc.ca/sites/cmhc/professional/housing-markets-data-and-research/housing-data-tables/rental-market/rental-market-report-data-tables/2025/rmr-hamilton-2025-en.xlsx
- Tables used (both for private row/townhouse and apartment units, purpose-built rental market):
  - **3.1.9** "Average Rents ($) of Vacant and Occupied Units by Zone and Bedroom Type": the
    vacant-unit column (`rent_basis = vacant_units`). This is what landlords were asking for empty
    units, the closest CMHC figure to what a new tenant pays.
  - **3.1.2** "Average Rents ($), by Zone and Bedroom Type", October 2025 column (`rent_basis =
    all_units`): the overall average, mostly sitting tenants. Used only as a fallback. Its city
    rows match CMHC's Housing Market Information Portal figures exactly (checked for Oakville,
    Mississauga, Brampton and the Toronto CMA).
- Retrieved: 2026-10-16, with `fetch_rms.py` (this folder).

## Licence

CMHC Licence Agreement for the Use of Data:
https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/housing-data/cmhc-licence-agreement-use-of-data
(also linked from the HMIP terms page: https://www.cmhc-schl.gc.ca/about-us/terms-conditions/hmip-terms-conditions).
Reviewed and approved for this project by Humayun on 2026-10-16.

In short:
- **Allowed:** a revocable, worldwide, royalty-free, non-exclusive licence to "use, reproduce,
  publish, freely distribute, or sell the Information", and to sub-license on consistent terms.
- **Attribution:** reproductions must read "Source: Canada Mortgage and Housing Corporation (CMHC),
  name of product or information, reference date. This information is reproduced and
  distributed on an 'as is' basis with the permission of CMHC." Products that adapt the data
  must read "Adapted from Canada Mortgage and Housing Corporation, name of product or
  information, reference date. This does not constitute an endorsement by Canada Mortgage and
  Housing Corporation of this product."
- **Not allowed:** implying CMHC endorses us; misrepresenting the data or its source; using
  CMHC's name, logos or marks (beyond attribution) without written permission; linking the data
  with other databases to identify individuals.
- CMHC may change the agreement at any time, and may require that a use be limited or stopped.

How we comply: this CSV is a reproduction; each row's `source` and `source_url` columns are its
attribution. The `price_vs_market` evidence shown to users carries the "Adapted from ..." wording
with the survey date. The data contains no personal information.

## Files

| File | What |
|---|---|
| `fetch_rms.py` | Rebuilds the CSV from the workbooks (needs `openpyxl`, used only by this script). Run by hand about once a year, after CMHC publishes the October survey (December) |
| `rms_average_rent_2025_10.csv` | 15 geographies (Toronto and Hamilton CMAs; 13 municipalities around Sheridan's campuses) × 2 rent bases × 4 bedroom buckets = 120 rows; 32 suppressed by CMHC (`**`), kept with empty rent so the city → CMA link survives |

Municipalities that share a CMHC zone get the zone's value, recorded in `source_geography`:
Richmond Hill, Vaughan and King ("Zone 25 - Richmond Hill/Vaughan/King"); Milton and Halton
Hills ("Zone 29 - Milton/Halton Hills"). Hamilton uses "Hamilton City (Zones 1-6)", which is the
former city, not the whole amalgamated municipality.

Load into the database (from `apps/api`):

    .venv/Scripts/python.exe -m rentalscamchecker.reference_rents ../../data/reference/cmhc/rms_average_rent_2025_10.csv

## Caveats

- The RMS covers purpose-built rental buildings (row and apartment). Condos, basement apartments
  and rooms are not in these averages. The signal skips rooms and adds a caveat for basements.
- Reliability letters: a excellent, b very good, c good, d poor ("use with caution"); the signal
  ignores `d` and falls back.
- Where a municipality's vacant value is suppressed, the fallback is the CMA's vacant value, which
  can be far above that municipality's own all-units average (e.g. Milton 2-bedroom: CMA vacant
  $2,530 vs Milton all units $1,667). See ED-021.
