# Validating the GTFS feed

The feed is checked with the [MobilityData GTFS Validator](https://github.com/MobilityData/gtfs-validator) (command-line jar, Java 17 or newer). The jar is not committed; download it to `sources/raw/tools/` (git-ignored) from the project's [releases page](https://github.com/MobilityData/gtfs-validator/releases).

## How to run

```
venv/bin/python scripts/build_gtfs.py --publisher-url https://github.com/arkodeepg/calcutta-transport
java -Xmx1500m -jar sources/raw/tools/gtfs-validator-8.0.1-cli.jar -i gtfs/calcutta-transport-gtfs.zip -o validator-output
```

Open `validator-output/report.html` for the full report. Any ERROR is a builder bug: fix `scripts/build_gtfs.py` or the data, never the generated files.

## Latest result

Run on 2026-10-06 with validator 8.0.1, on the feed built the same day (calendar 2026-10-06 to 2027-10-06), after the auto stand coordinate reuse pass (156 auto routes in the feed).

| severity | count | notices |
|---|---|---|
| ERROR | 0 | none |
| WARNING | 23 | missing_bike_allowance 16 (ferry trips), mixed_case_recommended_field 4 (bus short names such as 12AD), duplicate_route_name 2 (two auto routes from different RTA serials share the same termini), route_short_name_too_long 1 |
| INFO | 6 | trip_headsign_matches_intermediate_stop 4, unknown_file 2 (`LICENSE-DATA.txt` and `ATTRIBUTION.txt`, shipped on purpose for the ODbL notice) |
| System errors | 0 | none |

Update this section after each release build.
