"""Build data/tram/{stops,routes,route_stops}.csv for the tram routes running in 2026.

Running routes (S-TR-01 Wikipedia, S-TR-02 The Week 2026-07-01): 5 Shyambazar-Esplanade, 25 Gariahat-Esplanade.
Stops: OSM tram_stop nodes within 40 m of the OSM route=tram relation geometry (S-MR-01, osm_tram_5_25.json),
ordered by straight-line distance from the origin (both routes are near-monotonic). OSM maps only some
stops, so the stop lists are partial (major stops only).
Usage: venv/bin/python scripts/build_tram.py
NOTE: data/tram/routes.csv was edited by hand after this script last ran (2026-10-05: official
timetables and the October 2026 accuracy audit). The CSVs are the source of truth; rerunning this script
overwrites those edits, so diff the output before keeping it.
"""
import csv, json, math, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "tram"
d = json.load(open(ROOT / "sources/raw/osm_tram_5_25.json"))
REL = {e["id"]: e for e in d["elements"] if e["type"] == "relation"}
NODES = {e["id"]: e for e in d["elements"] if e["type"] == "node"}
m = lambda a, b: math.dist(a, b) * 111000

# Canonical stops: name -> OSM node used for coordinates (one per stop, the one on the route centreline)
CANON = {
    "Shyambazar": 1210706867, "Bidhan Sarani / Sri Aurobindo Sarani": 2723392043, "College Street": 2703679634,
    "College Street / Bepin Behari Ganguly Street": 2703686173, "Nirmal Chandra Dev Street": 2703690674,
    "Lenin Sarani / Nirmal Chandra Dev Street": 2703686631, "Esplanade": 1558109889,
    "Rafi Ahmad Kidwai Road / Elliot Road": 2721384860, "Acharya Jagadish Chandra Bose Road / Elliot Road": 2721388718,
    "Park Circus - Seven Point": 12076417108, "Park Circus": 2720702706, "Gariahat Depot": 2721264604,
}
ALIAS = {"Lenin Sarani/Nirmal Chandra Dev Street": "Lenin Sarani / Nirmal Chandra Dev Street",
         "Shyambazar (northbound)": "Shyambazar"}
slug = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
sid = lambda name: "tram_" + slug(name)


def route_stop_names(rel_id, origin):
    rel = REL[rel_id]
    geom = [(g["lat"], g["lon"]) for mm in rel["members"] if mm["type"] == "way" for g in mm.get("geometry", [])]
    names = set()
    for n in NODES.values():
        nm = ALIAS.get(n["tags"].get("name", ""), n["tags"].get("name", ""))
        if nm in CANON and min(m((n["lat"], n["lon"]), p) for p in geom) < 40:
            names.add(nm)
    o = NODES[CANON[origin]]
    return sorted(names, key=lambda nm: m((o["lat"], o["lon"]), (NODES[CANON[nm]]["lat"], NODES[CANON[nm]]["lon"])))


r5 = route_stop_names(2575608, "Shyambazar")
r25 = route_stop_names(2753154, "Esplanade")
r25 = r25 + ["Gariahat Depot"]  # terminus ~350 m beyond the end of the mapped OSM route geometry
r25 = list(reversed(r25))  # Gariahat -> Esplanade
used = sorted(set(r5) | set(r25))

with open(OUT / "stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["stop_id", "stop_name", "lat", "lon", "source_id", "notes"])
    for nm in used:
        n = NODES[CANON[nm]]
        note = f"OSM node {n['id']}"
        if nm == "Gariahat Depot":
            note += "; terminus, about 350 m beyond the end of the OSM route 25 geometry"
        if nm == "Esplanade":
            note += "; tram terminus near Esplanade metro (metro_esplanade_blue / metro_esplanade_green)"
        if nm == "Shyambazar":
            note += "; near Shyambazar metro (metro_shyambazar)"
        w.writerow([sid(nm), nm, round(n["lat"], 6), round(n["lon"], 6), "S-MR-01", note])

common = ("Only two routes ran as of mid-2026 (Wikipedia June 2026; The Week 2026-07-01). The Week: 8 to 10 trams across both routes "
          "between about 06:00 and 21:00 with no reliable schedule, so no headway is given. Stop list is partial: only stops mapped in OSM. "
          "Fare per community source: Rs 6 up to 4 km, Rs 7 beyond; AC tram on route 5 Rs 20 (S-TR-03). State government announced revival "
          "plans (70 routes) in 2026; recheck before use.")
ROUTES = [
    ["tram_5", "Tram 5 Shyambazar - Esplanade", "tram", "West Bengal Transport Corporation (WBTC)", "Shyambazar", "Esplanade",
     "", "", "06:00", "21:00", 6, 7, "S-TR-01;S-TR-02;S-TR-03", "community", "About 5 km via Bidhan Sarani, College Street, Nirmal Chandra Street. " + common],
    ["tram_25", "Tram 25 Gariahat - Esplanade", "tram", "West Bengal Transport Corporation (WBTC)", "Gariahat", "Esplanade",
     "", "", "06:00", "21:00", 6, 7, "S-TR-01;S-TR-02;S-TR-03", "community", "About 9 km via Park Circus, AJC Bose Road / Elliot Road, Rafi Ahmed Kidwai Road, Lenin Sarani. " + common],
]
with open(OUT / "routes.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_id", "route_name", "mode", "operator", "origin", "destination", "headway_min_peak", "headway_min_offpeak",
                "first_service", "last_service", "fare_min_inr", "fare_max_inr", "source_id", "confidence", "notes"])
    w.writerows(ROUTES)
with open(OUT / "route_stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_id", "direction", "seq", "stop_id", "travel_min_from_start"])
    for rid, seq in (("tram_5", r5), ("tram_25", r25)):
        for direction, s in ((0, seq), (1, list(reversed(seq)))):
            for i, nm in enumerate(s, 1):
                w.writerow([rid, direction, i, sid(nm), ""])
print("route 5:", r5); print("route 25:", r25)
