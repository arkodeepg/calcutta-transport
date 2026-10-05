# Calcutta Transport

Open route data for getting around Kolkata: buses, minibuses, metro, local trains, autos, ferries and trams, plus fares and passes.

## Layout

| Path | What |
|---|---|
| `data/{mode}/` | Hand-editable CSVs, one folder per mode. The source of truth. |
| `gtfs/` | Built GTFS feed generated from `data/`. Load this into OpenTripPlanner or any GTFS tool. |
| `scripts/` | Build scripts. Validation is run with the MobilityData validator, see [docs/VALIDATION.md](docs/VALIDATION.md). |
| `sources/` | `SOURCES.md` and per-mode source tables, where every dataset came from. Raw downloads go in `sources/raw/` and are not committed. |

## Coverage (October 2026)

| Mode | Routes in feed | Stops in feed | Timing data |
|---|---|---|---|
| Suburban rail | 27 lines | 355 | Real timetables, 1,637 trains |
| Metro | 5 lines | 57 | Official timetables for all 5 lines, per weekday, Saturday and Sunday (`data/metro/service_periods.csv`): Orange runs Monday to Friday only, Purple Monday to Friday plus Saturday afternoon |
| Bus and minibus | 603 | 1,177 | Default 15 min headway, times estimated from distance |
| Auto | 156 of 514 | 184 | Default 10 min headway. 492 routes from RTA Kolkata's official 2018 list |
| Ferry | 8 | 15 | Community-sourced frequencies and hours (no official timetable found); flat fares where one value is known. Routes with no service data are kept in `data/` but left out of the feed |
| Tram | 2 | 12 | 30 min headway (Wikipedia, undated); service is irregular, times are indicative |

Fares and passes for every mode are in `data/fares/` (distance slabs are not yet encoded in GTFS).

## Known gaps, help welcome

- About 1,185 bus stops and 419 auto stops have no coordinates in any open source, so they are left out of the feed (bus stops located: 74% weighted by how often routes use them). Adding the missing names to OpenStreetMap is the best fix; well-known examples are Moulali, Chandni Market, Khanna Cinema and Tollygunge Phari.
- RTA Kolkata's notification 5673-WT of December 2018 lists 489 authorised auto routes (a 2018 count, not a current one). They are transcribed in `data/auto/`, but many of their stops have no coordinates yet, so most are not in the feed.
- No source publishes bus or auto frequencies, so defaults are used for those two modes only; every default is listed in `gtfs/BUILD_REPORT.md`. No other mode gets invented service.
- Rail timetables are community-sourced and may miss some trains.

## Trip planner (POC)

A proof-of-concept trip planner runs on GitHub Pages at https://arkodeepg.github.io/calcutta-transport/ (served from `docs/`). Pick a start and destination, choose any mix of modes, and it routes in your browser over `docs/data/network.json`, on an OpenStreetMap-based map. No server of its own and no API keys; place search uses OpenStreetMap Nominatim on demand. Bus, minibus and auto times are estimates, see Known gaps.

Regenerate the network after rebuilding the GTFS feed. Metro and train lines are drawn along OpenStreetMap track, so extract the track and the water barriers for walking (Hooghly, canals, road bridges) from the Geofabrik extract first (once per OSM refresh):

```
venv/bin/python scripts/osm_pbf_extract.py --rail-ways sources/raw/bus/eastern-zone-latest.osm.pbf sources/raw/osm_rail_ways.json
venv/bin/python scripts/osm_pbf_extract.py --water sources/raw/bus/eastern-zone-latest.osm.pbf sources/raw/osm_water.json
venv/bin/python scripts/build_web.py
```

Walks, buses, minibuses, autos and trams are drawn on streets at view time, for the selected option only, by the FOSSGIS OSRM service at routing.openstreetmap.de (one request at a time, at least 1.1 s apart, cached; straight lines if it cannot be reached). The base map is OpenFreeMap (Positron, Dark in dark mode, Liberty as "Detailed") through MapLibre GL, with OpenStreetMap Standard tiles in the layer switcher and as the fallback without WebGL.

To try it locally, serve `docs/` with `venv/bin/python -m http.server` from inside that folder.

## Build

```
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python scripts/build_gtfs.py --publisher-url https://github.com/arkodeepg/calcutta-transport
```

Mode data is rebuilt with the `scripts/build_*.py` and `scripts/bus_*.py` scripts. Two cross-mode tables feed the GTFS build: `data/{mode}/service_periods.csv` (per-day and per-direction service windows) and `data/interchanges.csv` (reviewed transfer pairs whose names differ, such as Majerhat / Majherhat).

## Licence

- Data (`data/`, `gtfs/`): [Open Database License (ODbL) 1.0](LICENSE-DATA), required because it includes OpenStreetMap data. Attribution: © OpenStreetMap contributors and the sources in [CREDITS.md](CREDITS.md). The GTFS zip carries the same notice in `attributions.txt`, `ATTRIBUTION.txt` and `LICENSE-DATA.txt`.
- Facts taken from sources that state no open licence (timetables, route lists, news reports) are republished as facts only, with credit; see the licence notes in `sources/`.
- Code (`scripts/`): [MIT](LICENSE).

## Credits

See [CREDITS.md](CREDITS.md).
