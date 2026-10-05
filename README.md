# Calcutta Transport

Open route data for getting around Kolkata: buses, minibuses, metro, local trains, autos, ferries and trams, plus fares and passes.

## Layout

| Path | What |
|---|---|
| `data/{mode}/` | Hand-editable CSVs, one folder per mode. The source of truth. |
| `gtfs/` | Built GTFS feed generated from `data/`. Load this into OpenTripPlanner or any GTFS tool. |
| `scripts/` | Build and validation scripts. |
| `sources/` | Raw downloads and `SOURCES.md`, where every dataset came from. |

## Coverage (October 2026)

| Mode | Routes in feed | Stops in feed | Timing data |
|---|---|---|---|
| Suburban rail | 27 lines | 355 | Real timetables, 1,637 trains |
| Metro | 5 lines | 57 | Official headways for Blue, Green, Yellow |
| Bus and minibus | 584 | 805 | Default 15 min headway, times estimated from distance |
| Auto | 25 | 56 | Default 10 min headway |
| Ferry | 9 | 15 | Published frequencies, flat fares |
| Tram | 2 | 12 | Default headway |

Fares and passes for every mode are in `data/fares/` (distance slabs are not yet encoded in GTFS).

## Known gaps, help welcome

- About 1,500 bus stops and 60 auto stops have no coordinates, so they are left out of the feed. Adding the missing names to OpenStreetMap is the best fix.
- Auto routes cover mostly south and central Kolkata. RTA Kolkata has 489 authorised routes; that list is not public.
- No source publishes bus or auto frequencies, so defaults are used. See `gtfs/BUILD_REPORT.md`.
- Rail timetables are community-sourced and may miss some trains.

## Build

```
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python scripts/build_gtfs.py --publisher-url https://github.com/arkodeepg/calcutta-transport
```

Mode data is rebuilt with the `scripts/build_*.py` and `scripts/bus_*.py` scripts.

## Licence

- Data (`data/`, `gtfs/`): [Open Database License (ODbL) 1.0](LICENSE-DATA), required because it includes OpenStreetMap data. Attribution: © OpenStreetMap contributors and the sources in [CREDITS.md](CREDITS.md).
- Code (`scripts/`): [MIT](LICENSE).

## Credits

See [CREDITS.md](CREDITS.md).
