# Calcutta Transport

Personal-first open dataset of Kolkata transit routes (bus, minibus, metro, suburban rail, auto, ferry, tram), fares and passes, published as GTFS.

## Rules

- `data/` CSVs are the source of truth. `gtfs/` is generated, never hand-edited.
- Every dataset gets a row in `sources/SOURCES.md`: URL, date fetched, licence, what it covers.
- Keep it minimal: routes, stops, coordinates, frequencies, fares, passes. No app code here.
- Python: `venv/bin/python` only. Never install system-wide.
- No em dashes in any written content.
- Do not push to any remote without asking.
