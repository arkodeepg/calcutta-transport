# Sources

Every dataset used, with URL, fetch date, stated licence and coverage. Each `source_id` in `data/` points at a row in one of these files.

| File | Modes |
|---|---|
| [SOURCES_bus.md](SOURCES_bus.md) | Bus, minibus |
| [SOURCES_metro_rail.md](SOURCES_metro_rail.md) | Metro, suburban rail, ferry, tram |
| [SOURCES_auto_fares.md](SOURCES_auto_fares.md) | Auto routes, fares and passes for all modes |

Raw downloads live in `sources/raw/` and are not committed. Rerun the fetch scripts in `scripts/` to recreate them.

## Trip planner place search

`docs/data/places.json`, the place list behind the planner's From and To search, is built by `scripts/build_web.py` from data already listed in [SOURCES_bus.md](SOURCES_bus.md), plus one more extract of the same Geofabrik file:

| source_id | name | URL | fetched | licence / terms as stated | coverage |
|---|---|---|---|---|---|
| osm (places) | OpenStreetMap, Geofabrik extract "asia/india/eastern-zone", `scripts/osm_pbf_extract.py --places`: named localities, landmarks, hospitals, schools, colleges, places of worship, parks, markets, malls, offices, housing, named buildings and crossings (nodes, and areas at their bbox centre), plus named streets merged by name, in bbox 21.5,87.4,23.6,89.1 | https://download.geofabrik.de/asia/india/eastern-zone-latest.osm.pbf | 2026-10-05 | ODbL 1.0, (c) OpenStreetMap contributors | 24,473 rows (3,193 merged streets) |
| wikidata, geonames | As in [SOURCES_bus.md](SOURCES_bus.md) | | 2026-10-05 | CC0 1.0; CC BY 4.0, credit "GeoNames (geonames.org)" | Wikidata: aliases for OSM places, plus notable items (Bengali label or alias); GeoNames: populated places and spots |
| photon (online) | Photon geocoder by komoot, queried live by the planner page only when the local list finds little (debounced, one request at a time, bounded to 22.3,88.0,23.1,88.7). Nothing from it is stored in this repository | https://photon.komoot.io/ | 2026-10-06 (terms read) | OpenStreetMap data, ODbL. Terms: "You can use the API for your project, but please be fair - extensive usage will be throttled." | Live fallback only |

Kept: everything inside 22.3,88.0,23.1,88.7, outside it only places within 4 km of a mapped stop. Same-spelling names close together are merged; names that match a nearby transit stop are left to the stop.

