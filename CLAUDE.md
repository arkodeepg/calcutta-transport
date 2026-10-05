# Calcutta Transport

Personal-first open dataset of Kolkata transit routes (bus, minibus, metro, suburban rail, auto, ferry, tram), fares and passes, published as GTFS.

## Rules

- `data/` CSVs are the source of truth. `gtfs/` is generated, never hand-edited.
- Every dataset gets a row in `sources/SOURCES.md`: URL, date fetched, licence, what it covers.
- Keep the data minimal: routes, stops, coordinates, frequencies, fares, passes. The only app code is the static trip planner POC in `docs/` (GitHub Pages), built from `scripts/build_web.py`.
- Python: `venv/bin/python` only. Never install system-wide.
- No em dashes in any written content.
- Do not push to any remote without asking.
