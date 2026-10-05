"""Build data/metro/{stops,routes,route_stops,fares}.csv.

Station order and coordinates: OSM route=subway relations (sources/raw/osm_subway_full.json, S-MR-01).
Service figures: official Metro Railway Kolkata timetable PDFs parsed by metro_tt_parse.py (S-MR-03),
otherwise Wikipedia line articles (S-MR-04). Values are hand-entered below with their source ids.
Usage: venv/bin/python scripts/build_metro.py
NOTE: data/metro/routes.csv and fares.csv was edited by hand after this script last ran (2026-10-05: official
timetables and the October 2026 accuracy audit). The CSVs are the source of truth; rerunning this script
overwrites those edits, so diff the output before keeping it.
"""
import csv, json, re, pathlib, statistics

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "metro"
osm = json.load(open(ROOT / "sources/raw/osm_subway_full.json"))
N = {e["id"]: e for e in osm["elements"] if e["type"] == "node"}
R = {e["id"]: e for e in osm["elements"] if e["type"] == "relation"}

# OSM name -> official name (Metro Railway / Wikipedia spelling)
FIX = {"Dakshineshwar": "Dakshineswar", "Satyajit Roy": "Satyajit Ray", "Sakher bazar": "Sakher Bazar",
       "Sakherbazar": "Sakher Bazar", "City Centre": "City Centre", "Salt Lake Sector V": "Salt Lake Sector V"}
LINE_SUFFIX = re.compile(r"\s*\((Blue Line|Orange Line|Yellow Line|Line 1|Line 2)\)$")

# Stations with separate platforms per line get one stop per line (transfer between them).
SPLIT = {"Esplanade", "Noapara", "Kavi Subhash"}

LINES = [
    # route_id, name, relation dir0, relation dir1, colour key, timepoints (official code->station) per dir
    ("metro_blue", "Blue Line (North-South)", 8034180, 8033916, "blue"),
    ("metro_green", "Green Line (East-West)", 11720071, 11720072, "green"),
    ("metro_purple", "Purple Line (Joka-Esplanade, open Joka-Majerhat)", 15068962, 15068961, "purple"),
    ("metro_orange", "Orange Line (Kavi Subhash-Beleghata)", 17320238, 17320237, "orange"),
    ("metro_yellow", "Yellow Line (Noapara-Jai Hind)", 19508983, 19508978, "yellow"),
]
CLOSED = {("metro_blue", "Kavi Subhash")}  # Blue Line platforms suspended since 2025-07-28

slug = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def stops_of(rel_id):
    out = []
    for m in R[rel_id]["members"]:
        if m["type"] == "node" and m["role"].startswith("stop") and m["ref"] in N:
            n = N[m["ref"]]
            raw = n["tags"].get("name", "")
            name = FIX.get(LINE_SUFFIX.sub("", raw), LINE_SUFFIX.sub("", raw))
            out.append((name, n["lat"], n["lon"], n["id"]))
    return out


stops, route_stops = {}, []
line_stops = {}
for rid, rname, r0, r1, key in LINES:
    for direction, rel in ((0, r0), (1, r1)):
        seq = 0
        for name, lat, lon, nid in stops_of(rel):
            if (rid, name) in CLOSED:
                continue
            sid = f"metro_{slug(name)}" + (f"_{key}" if name in SPLIT else "")
            s = stops.setdefault(sid, {"name": name, "pts": [], "osm": set(), "lines": set()})
            s["pts"].append((lat, lon)); s["osm"].add(nid); s["lines"].add(key)
            seq += 1
            route_stops.append([rid, direction, seq, sid, ""])
        line_stops[(rid, direction)] = [r for r in route_stops if r[0] == rid and r[1] == direction]

# Official timetable timepoints (minutes from first stop), median of weekday trips, S-MR-03
TP = {
    ("metro_blue", 0): {"metro_dakshineswar": 0, "metro_noapara_blue": 8, "metro_dum_dum": 12, "metro_mahanayak_uttam_kumar": 45, "metro_shahid_khudiram": 60},
    ("metro_blue", 1): {"metro_shahid_khudiram": 0, "metro_mahanayak_uttam_kumar": 13, "metro_dum_dum": 46, "metro_noapara_blue": 50, "metro_dakshineswar": 60},
    ("metro_green", 0): {"metro_howrah_maidan": 0, "metro_salt_lake_sector_v": 32},
    ("metro_green", 1): {"metro_salt_lake_sector_v": 0, "metro_howrah_maidan": 32},
    ("metro_yellow", 0): {"metro_noapara_yellow": 0, "metro_dum_dum_cantonment": 5, "metro_jessore_road": 10, "metro_jai_hind": 12},
    ("metro_yellow", 1): {"metro_jai_hind": 0, "metro_jessore_road": 3, "metro_dum_dum_cantonment": 7, "metro_noapara_yellow": 12},
}
for r in route_stops:
    tp = TP.get((r[0], r[1]), {})
    if r[3] in tp:
        r[4] = tp[r[3]]
missing = [(k, s) for k, v in TP.items() for s in v if s not in {r[3] for r in route_stops if (r[0], r[1]) == k}]
assert not missing, missing

INTERCHANGE = {
    "metro_esplanade_blue": "Interchange with Green Line (metro_esplanade_green)",
    "metro_esplanade_green": "Interchange with Blue Line (metro_esplanade_blue)",
    "metro_noapara_blue": "Interchange with Yellow Line (metro_noapara_yellow)",
    "metro_noapara_yellow": "Interchange with Blue Line (metro_noapara_blue)",
    "metro_kavi_subhash_orange": "Blue Line Kavi Subhash platforms closed since 2025-07-28 for reconstruction; Blue Line terminates at Shahid Khudiram. Interchange with suburban rail at New Garia",
    "metro_dum_dum": "Interchange with suburban rail (Dum Dum Jn)",
    "metro_dum_dum_cantonment": "Interchange with suburban rail (Dum Dum Cantonment)",
    "metro_howrah": "Interchange with suburban rail (Howrah)",
    "metro_sealdah": "Interchange with suburban rail (Sealdah)",
    "metro_majerhat": "Interchange with suburban rail (Majerhat)",
    "metro_jai_hind": "Serves Netaji Subhas Chandra Bose International Airport",
    "metro_kalighat": "",
}
with open(OUT / "stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["stop_id", "stop_name", "lat", "lon", "source_id", "notes"])
    for sid, s in sorted(stops.items()):
        lat = round(statistics.mean(p[0] for p in s["pts"]), 6)
        lon = round(statistics.mean(p[1] for p in s["pts"]), 6)
        note = "; ".join(x for x in [INTERCHANGE.get(sid, ""), "OSM node " + ",".join(map(str, sorted(s["osm"])))] if x)
        w.writerow([sid, s["name"], lat, lon, "S-MR-01", note])

ROUTES = [
    # route_id, name, operator, origin, dest, hw_peak, hw_off, first, last, fare_min, fare_max, src, conf, notes
    ["metro_blue", "Blue Line (North-South)", "Metro Railway Kolkata", "Dakshineswar", "Shahid Khudiram", 6, 7, "06:00", "22:30", 5, 25,
     "S-MR-03;S-MR-04;S-MR-05", "verified",
     "Weekday timetable w.e.f. 2026-07-27 (official PDF): median headway 6 min 08-11 and 17-20, 7 min 11-16; first departures 06:00 from Dakshineswar and Shahid Khudiram, last 22:18 from Dakshineswar and 22:30 from Shahid Khudiram. Some trips short-turn at Mahanayak Uttam Kumar, Dum Dum, Noapara. Saturday: 7/8 min. Sunday from 09:00, about 10 min. Kavi Subhash suspended since 2025-07-28. Fare: Blue Line distance slabs Rs 5 to 25 (see fares.csv); Rs 10 surcharge on special night services (S-MR-08)."],
    ["metro_green", "Green Line (East-West)", "Metro Railway Kolkata (KMRC infrastructure)", "Howrah Maidan", "Salt Lake Sector V", 6, 10, "06:00", "22:30", 5, 30,
     "S-MR-03;S-MR-04", "verified",
     "Weekday timetable w.e.f. 2026-07-27 (official PDF): 15 min early morning, median 6 min in peaks, 10 min midday; end to end 32 min. Some down trips start at City Centre. Sunday from 09:00. Through running since Esplanade-Sealdah opened 2025-08-22 (includes Hooghly underwater section Howrah Maidan-Mahakaran). Fare slabs for non-Blue lines Rs 5 to 30 (see fares.csv)."],
    ["metro_purple", "Purple Line (Joka-Majerhat)", "Metro Railway Kolkata", "Joka", "Majerhat", 21, 21, "06:40", "21:26", 5, 20,
     "S-MR-04", "community",
     "Wikipedia: weekdays 84 pairs, first from Joka 06:40 and Majerhat 07:03, last services quoted as 21:05 (Joka) and 21:26 (Majerhat), mostly 21 min interval; Saturday 40 services at 21 min (afternoon only per news); no Sunday service stated. Official MTP Purple timetable page empty when fetched. Fare max assumes 5-10 km slab of non-Blue chart for the 7.75 km line, unverified."],
    ["metro_orange", "Orange Line (Kavi Subhash-Beleghata)", "Metro Railway Kolkata", "Kavi Subhash", "Beleghata", 25, 25, "08:00", "", 5, 20,
     "S-MR-04", "unverified",
     "Sources conflict: Wikipedia says 08:00 to 20:05, 60 pairs, 25 min, weekdays only (text predates 2025-08-22 Beleghata extension); a community site says 20 min 08:00-21:00 and last from Beleghata 21:44. No Saturday or Sunday service reported. Official MTP Orange timetable page empty when fetched. Fare max (Rs 20, 5-10 km slab) for ~9.9 km is unverified."],
    ["metro_yellow", "Yellow Line (Noapara-Jai Hind)", "Metro Railway Kolkata", "Noapara", "Jai Hind (Biman Bandar)", 12, 12, "07:18", "21:20", 5, 20,
     "S-MR-03", "verified",
     "Weekday timetable w.e.f. 2026-05-18 (official PDF): 60 trips each way, 12 min headway (20 min first trips), 12 min end to end; Sunday timetable w.e.f. 2026-08-02 from 09:18, 18 min. Last departures 21:00 Noapara, 21:20 Jai Hind. Fare max (Rs 20, 5-10 km slab, ~7 km) inferred from non-Blue chart, unverified."],
]
with open(OUT / "routes.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_id", "route_name", "mode", "operator", "origin", "destination", "headway_min_peak", "headway_min_offpeak",
                "first_service", "last_service", "fare_min_inr", "fare_max_inr", "source_id", "confidence", "notes"])
    for r in ROUTES:
        w.writerow(r[:2] + ["metro"] + r[2:])
with open(OUT / "route_stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_id", "direction", "seq", "stop_id", "travel_min_from_start"])
    w.writerows(route_stops)
with open(OUT / "fares.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["applies_to", "km_from", "km_to", "fare_inr", "source_id", "confidence", "notes"])
    for a, b, p in [(0, 2, 5), (2, 5, 10), (5, 10, 15), (10, 20, 20), (20, "", 25)]:
        w.writerow(["metro_blue", a, b, p, "S-MR-04", "community", "Wikipedia fare table citing MTP fare page (archived 2019); not reconfirmed on live MTP site (fare page empty 2026-10-05)"])
    for a, b, p in [(0, 2, 5), (2, 5, 10), (5, 10, 20), (10, "", 30)]:
        w.writerow(["metro_green;metro_purple;metro_orange;metro_yellow", a, b, p, "S-MR-04", "community", "Wikipedia 'Other lines' fare table; not reconfirmed on live MTP site"])
    w.writerow(["smart_card", "", "", "", "AF-THEWEEK-METRO;S-MR-04", "community", "Smart card terms from 2025-09-25 (AF-THEWEEK-METRO, AF-MPOST-METRO): refundable security deposit Rs 50 (was Rs 80 from Nov 2021, Rs 60 before), minimum issue price Rs 100 (Rs 50 deposit plus Rs 52 ride value including a Rs 2 bonus), 5 percent bonus on recharge value continues, validity 10 years counted from the first gate swipe (existing cards extended to 10 years at their next recharge). Tourist cards Rs 250 (1 day) and Rs 550 (3 days) per Wikipedia (deposit not reconfirmed). Paper QR and mobile QR (Aamar Kolkata Metro app) tickets on all lines; tokens withdrawn from Jan 2025."])
print("stops", len(stops), "route_stops", len(route_stops))
