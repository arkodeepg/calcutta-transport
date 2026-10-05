# Sources

Every dataset used, with URL, fetch date, stated licence and coverage. Each `source_id` in `data/` points at a row in one of these files.

| File | Modes |
|---|---|
| [SOURCES_bus.md](SOURCES_bus.md) | Bus, minibus |
| [SOURCES_metro_rail.md](SOURCES_metro_rail.md) | Metro, suburban rail, ferry, tram |
| [SOURCES_auto_fares.md](SOURCES_auto_fares.md) | Auto routes, fares and passes for all modes |

Raw downloads live in `sources/raw/` and are not committed. Rerun the fetch scripts in `scripts/` to recreate them.
