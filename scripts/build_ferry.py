"""Build data/ferry/{stops,routes,route_stops}.csv for Hooghly river ferries.

Ghat coordinates: OSM amenity=ferry_terminal nodes/ways and route=ferry way endpoints (S-MR-01, osm_ferry.json).
Service info: WBSTC route list (S-FE-01, last updated 2019), WBTC fare list via search snippet (S-FE-02),
community guides dated 2026 (S-FE-03 travelingcreature 2026-05-08, S-FE-04 kolkatadekho 2026-08-18).
Northern crossings exist only as OSM ferry ways: kept with no service data, confidence unverified.
Usage: venv/bin/python scripts/build_ferry.py
"""
import csv, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "ferry"
d = json.load(open(ROOT / "sources/raw/osm_ferry.json"))
EL = {(e["type"], e["id"]): e for e in d["elements"]}


def node(i):
    e = EL[("node", i)]; return round(e["lat"], 6), round(e["lon"], 6)


def way_end(i, end):
    g = EL[("way", i)]["geometry"][0 if end == 0 else -1]; return round(g["lat"], 6), round(g["lon"], 6)


hw = EL[("way", 384349701)]["center"]
STOPS = {  # stop_id: (name, (lat, lon) or None, note)
    "ferry_howrah": ("Howrah Station Ghat (Howrah Launch Ghat)", (round(hw["lat"], 6), round(hw["lon"], 6)), "OSM way 384349701 centre; next to Howrah station; ferry way 659390160/659390161 endpoints"),
    "ferry_fairlie": ("Fairlie Ghat (Fairlie Place)", node(6173584909), "OSM node 6173584909, endpoint of ferry way 659390160"),
    "ferry_millennium_park": ("Millennium Park (Shipping) Jetty", node(6173584907), "Unnamed OSM ferry terminal 6173584907 at the south end of ferry way 659390161 from Howrah, at Millennium Park; identification by location"),
    "ferry_babughat": ("Babughat / Chandpal Ghat", node(10559494009), "OSM node 10559494009 'Babughat Ferry to Howrah'"),
    "ferry_bagbazar": ("Bagbazar Launch Ghat", node(1208567622), "OSM node 1208567622"),
    "ferry_ahiritola": ("Ahiritola Ghat", node(1189457648), "OSM node 1189457648"),
    "ferry_shobhabazar": ("Shobhabazar Ghat", node(1189457885), "OSM node 1189457885"),
    "ferry_bandhaghat": ("Bandhaghat (Howrah side)", None, "Not mapped as a ferry terminal in OSM; coordinates left blank"),
    "ferry_golabari": ("Golabari Ghat (Howrah side)", None, "Intermediate stop on Howrah-Bagbazar per community guide; not mapped in OSM; coordinates left blank"),
    "ferry_belur_math": ("Belur Math Ghat", node(4523292408), "OSM node 4523292408"),
    "ferry_dakshineswar": ("Dakshineswar Ghat", node(4178734127), "OSM node 4178734127"),
    "ferry_rishra": ("Rishra Ferry Ghat", node(4467164697), "OSM node 4467164697"),
    "ferry_khardah": ("Khardah Ghat", way_end(449781829, 1), "East end of OSM ferry way 449781829 'Rishra - Khardah ferry service'; no terminal node"),
    "ferry_sheoraphuli": ("Sheoraphuli Ghat", node(1765885555), "OSM node 1765885555 'Sheoraphulli Ghaat'"),
    "ferry_dui_paisar_ghat": ("Dui Paisar Ghat (east bank, Barrackpore side)", node(1765885556), "OSM node 1765885556; town attribution by location"),
    "ferry_jugal_auddy_ghat": ("Jugal Auddy Ferry Ghat (west bank, Serampore side)", node(2178406075), "OSM node 2178406075; town attribution by location"),
    "ferry_dhobi_ghat": ("Dhobi Ghat (east bank, Barrackpore side)", node(2178405584), "OSM node 2178405584; town attribution by location"),
}

with open(OUT / "stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["stop_id", "stop_name", "lat", "lon", "source_id", "notes"])
    for sid, (nm, ll, note) in STOPS.items():
        w.writerow([sid, nm, ll[0] if ll else "", ll[1] if ll else "", "S-MR-01" if ll else "S-FE-03", note])

H = ["route_id", "route_name", "mode", "operator", "origin", "destination", "headway_min_peak", "headway_min_offpeak",
     "first_service", "last_service", "fare_min_inr", "fare_max_inr", "source_id", "confidence", "notes"]
WB = "West Bengal Surface Transport Corporation (WBSTC, now part of WBTC)"
ROUTES = [
    (["ferry_howrah_fairlie", "Howrah - Fairlie Ghat", "ferry", WB, "Howrah", "Fairlie Ghat", 10, 10, "08:00", "19:50", 6, 6,
      "S-FE-01;S-FE-02;S-FE-03;S-FE-04", "community",
      "WBSTC route list. Guide (2026-05-08): every 10 min, 08:00-19:50, about 6 min crossing; another guide (2026-08-18) says roughly 07:40-21:00. Fare Rs 6 (WBTC fare list)."],
     [("ferry_howrah", 0), ("ferry_fairlie", 6)]),
    (["ferry_howrah_millennium_park", "Howrah - Millennium Park (Shipping)", "ferry", WB, "Howrah", "Millennium Park", 10, 10, "08:00", "19:50", 6, 6,
      "S-FE-01;S-FE-02;S-FE-03", "community", "WBSTC route list. Guide: every 10 min, 08:00-19:50, about 8 min crossing. Fare Rs 6 (WBTC fare list, 'Shipping - Howrah')."],
     [("ferry_howrah", 0), ("ferry_millennium_park", 8)]),
    (["ferry_howrah_babughat", "Howrah - Babughat / Chandpal", "ferry", "", "Howrah", "Babughat", 15, 15, "08:00", "19:45", 6, 6,
      "S-FE-03", "community", "Guide only: every 15 min, 08:00-19:45, about 10 min, Rs 6. Operator not stated; not in WBSTC route list (may be a private or Inland Water Transport service)."],
     [("ferry_howrah", 0), ("ferry_babughat", 10)]),
    (["ferry_howrah_bagbazar", "Howrah - Bagbazar", "ferry", WB, "Howrah", "Bagbazar", 30, 60, "09:00", "19:30", 7, 7,
      "S-FE-02;S-FE-03", "community", "Guide: every 30 min 09:00-12:00 and 15:00-19:30, hourly 12:00-15:00; calls at Golabari, Ahiritola, Shobhabazar. Fare Rs 7 per WBTC fare list (guide says Rs 6-7). Intermediate stop order inferred from geography (north along the river)."],
     [("ferry_howrah", 0), ("ferry_golabari", ""), ("ferry_ahiritola", ""), ("ferry_shobhabazar", ""), ("ferry_bagbazar", "")]),
    (["ferry_ahiritola_bandhaghat", "Ahiritola - Bandhaghat", "ferry", "", "Ahiritola", "Bandhaghat", 15, 15, "05:30", "21:30", 9, 9,
      "S-FE-03;S-FE-04", "community", "Guide (2026-05-08): every 15 min 05:30-21:30, 6-7 min, Rs 9; other guide says about Rs 5. Operator not stated. Bandhaghat coordinates unknown."],
     [("ferry_ahiritola", 0), ("ferry_bandhaghat", 7)]),
    (["ferry_belur_dakshineswar", "Belur Math - Dakshineswar", "ferry", WB, "Belur Math", "Dakshineswar", 30, 30, "07:30", "19:30", 11, 11,
      "S-FE-01;S-FE-02;S-FE-03", "community", "WBSTC route list. Guide: every 30 min, 07:30-19:30, Rs 11 (matches WBTC fare list); guide's 40 min journey time looks long for a 2.7 km OSM ferry way, so travel time left blank."],
     [("ferry_belur_math", 0), ("ferry_dakshineswar", "")]),
    (["ferry_rsv_circuit", "Ramakrishna-Sarada-Vivekananda Circuit (Fairlie - Ariadaha)", "ferry", WB, "Fairlie Ghat", "Ariadaha", "", "", "", "", 11, 16,
      "S-FE-01;S-FE-02", "unverified", "WBSTC lists 'Fairlie - Ariyadaha via Howrah - Baghbazar - Belur - Kutighat'. Kutighat and Ariadaha ghats not mapped in OSM, so only the mapped ghats are listed; fares in WBTC list: Bagbazar-Belur Rs 11, Bagbazar-Dakshineswar Rs 16. Frequency unknown (a tourist circuit, likely few sailings)."],
     [("ferry_fairlie", ""), ("ferry_howrah", ""), ("ferry_bagbazar", ""), ("ferry_belur_math", "")]),
    (["ferry_rishra_khardah", "Rishra - Khardah", "ferry", "", "Rishra", "Khardah", "", "", "", "", "", "",
      "S-MR-01", "unverified", "Crossing exists as named OSM ferry way 449781829; no timetable, frequency or fare found."],
     [("ferry_rishra", 0), ("ferry_khardah", "")]),
    (["ferry_sheoraphuli_barrackpore", "Sheoraphuli - Dui Paisar Ghat (Barrackpore)", "ferry", "", "Sheoraphuli", "Barrackpore", "", "", "", "", "", "",
      "S-MR-01", "unverified", "OSM ferry way 164999568 between the two named terminals; no service data found."],
     [("ferry_sheoraphuli", 0), ("ferry_dui_paisar_ghat", "")]),
    (["ferry_serampore_barrackpore", "Jugal Auddy Ghat (Serampore) - Dhobi Ghat (Barrackpore)", "ferry", "", "Serampore", "Barrackpore", "", "", "", "", "", "",
      "S-MR-01", "unverified", "OSM ferry way 207589982 between the two named terminals; no service data found."],
     [("ferry_jugal_auddy_ghat", 0), ("ferry_dhobi_ghat", "")]),
]
with open(OUT / "routes.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(H)
    for r, _ in ROUTES:
        w.writerow(r)
with open(OUT / "route_stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_id", "direction", "seq", "stop_id", "travel_min_from_start"])
    for r, seq in ROUTES:
        w.writerows([r[0], 0, i, s, t] for i, (s, t) in enumerate(seq, 1))
        # reverse direction: offsets only when both ends known
        tot = seq[-1][1]
        rev = [(s, (tot - t) if isinstance(t, int) and isinstance(tot, int) else "") for s, t in reversed(seq)]
        w.writerows([r[0], 1, i, s, t] for i, (s, t) in enumerate(rev, 1))
print("ferry stops", len(STOPS), "routes", len(ROUTES))
