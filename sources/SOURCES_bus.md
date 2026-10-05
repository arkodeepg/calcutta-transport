# Sources: bus and minibus

Build: `venv/bin/python scripts/bus_fetch_upstream.py`, `scripts/bus_fetch_osm.py`, then `scripts/bus_build.py`, then `scripts/bus_geocode_nominatim.py`, then `scripts/bus_build.py` again. Raw inputs live in `sources/raw/bus/` (git-ignored): the upstream pages are cached under `sources/raw/bus/upstream/` with `fetch_log.json` (URL, UTC time, HTTP status). The build log with merge, rejection and split decisions is `sources/raw/bus/build_report.txt`.

Fetch policy: at most 1 request per 2 seconds to the route-list sites and 1 per second to Nominatim, a descriptive User-Agent with no personal details, everything cached so a rebuild is offline.

| source_id | mode | name | URL | fetched | licence / terms as stated | coverage |
|---|---|---|---|---|---|---|
| wbtc_routes | bus | West Bengal Transport Corporation, "WBTC City Bus Routes" table (same table as the site's "Download pdf", INTRA_CITY_RTS_Latest-small.pdf) | https://wbtconline.in/wbtc-city-bus-routes | 2026-10-05 | Government undertaking web page; footer "Copyright (c) 2026. All Rights Reserved". The site's Terms & Conditions cover e-ticketing only and say nothing about reuse of the route list. Used for facts only: route numbers, termini, stoppages | 131 WBTC routes. 84 are also on Bus-O-Pedia's government page (merged, both credited), 47 only here. No fares, headways or timings |
| kolbusopedia | bus, minibus | Kolkata Bus-O-Pedia route catalogue, private and government pages | https://www.kolbusopedia.com/bus-routes, https://www.kolbusopedia.com/bus-routes-government | 2026-10-05 | Community site run by Kolkata Bus-O-Pedia Foundation (registered society); footer "(c)2026 by Kolkata bus-o-pedia Foundation". No licence or terms page found; robots.txt allows crawling. Used for facts only: route numbers, termini, stop sequences, section (operator class) | 543 route lines parsed (359 private page, 184 government page), 2 lines left unparsed (DN21, C24: no clear origin/destination). Sections: blue-yellow private, 200 series, minibus, SD, DN, K/KB, M/MM/MN, E (Howrah), STA, WBTC CSTC/WBSTC/CTC C series, D and E series. No fares, headways or timings |
| osm | bus | OpenStreetMap via Overpass API: route=bus relations (bbox 22.3,88.0,23.1,88.7), bus stops/platforms and place/station nodes (bbox 21.5,87.7,23.4,89.1), named landmarks (hospitals, colleges, cinemas, malls, parks, temples, junctions, bridges, housing estates; bbox 22.3,88.0,23.1,88.7) | https://overpass-api.de/api/interpreter | 2026-10-05 | ODbL 1.0, (c) OpenStreetMap contributors | 449 bus relations (249 carry a route number in the lists above and are used as route-line checks; 15 OSM-only numbers added as routes), 721 named bus stops, 3368 named places/stations, 4380 named landmarks. All stop coordinates come from OSM data |
| nominatim | bus | Nominatim search (OSM data), bounded to the region | https://nominatim.openstreetmap.org/ | 2026-10-05 | ODbL 1.0, (c) OpenStreetMap contributors; used under the Nominatim usage policy (1 request/s, cached) | Fallback coordinates for stops not matched in the OSM extracts; accepted only when they fit the route |

BusBondhu (https://github.com/rounak-adhikary-github/bus-bondhu, "All rights reserved") was consulted only to find which upstream pages it compiled from. It is not a data source: no route, stop name, alias, coordinate or derived value from it is used, and none of its files are read by the build.

## Counts (build of 2026-10-05)

- Routes: 605. bus/WBTC 232, bus/private 274, minibus/private 85, bus/operator unknown 14 (OSM-only).
- By source: kolbusopedia 459, wbtc_routes;kolbusopedia 84, wbtc_routes 47, osm 15.
- Stops: 2304, of which 805 have coordinates (osm_place 305, nominatim 251, osm_stop 154, osm_landmark 67, osm_route 28). Route-weighted coverage (share of direction-0 route stops with a coordinate): 58.0%.

## Notes and gaps

- No published source carries headways, first/last service or route-level fares for these routes, so those columns are blank. Fare slabs (WBTC/private/minibus) belong to the fares dataset.
- `confidence`: verified = in WBTC's own published list; community = Bus-O-Pedia or OSM only.
- When a government route number is published by both WBTC and Bus-O-Pedia, the list that names more stops is kept and the other list's termini are recorded in `notes`. A private route that shares a number with a WBTC route is kept as a separate route (suffix `_2`).
- Stop identity: spellings are grouped by a normalised key (case, punctuation, common Bengali-English transliteration variants, abbreviations such as Stn, Xing, Rd). Other spellings are listed in each stop's `notes`. One name used for places far apart is split into separate ids.
- Direction 1 in `route_stops.csv` is direction 0 reversed unless OSM supplied a second-direction relation. Kolkata routes often use one-way streets on the return, so the reverse order is approximate. Where a source printed the stoppages from the far end, the list is reordered and the route `notes` say so.
- `travel_min_from_start` is blank: neither source publishes run times.
- Neither upstream list publishes coordinates. Every coordinate comes from OSM (via Overpass or Nominatim) and passed a route-geometry check: near the nearest located neighbours on at least half of the routes that can test it and, where OSM has the same route number, within 1 km of the OSM route line. Stops no neighbour could test yet were seeded only when their exact name matches a single OSM feature; all points were then re-checked leave-one-out (worst offender first) and rejected if off-route. 75 seeded stops still had too few located neighbours to test and say "not route-checked" in `notes`. Rejected points are listed in each stop's `notes`. No coordinate is invented: unmatched stops stay blank.
- Many central stops (Dalhousie, Moulali, Wellington, PTS, Chandni Market, Maidan Metro, Khanna Cinema and others) have no matching name in OSM or Nominatim as of this fetch, so they are blank. Adding those names to OpenStreetMap is the open way to fill them.
