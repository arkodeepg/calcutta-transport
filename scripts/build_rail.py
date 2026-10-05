"""Build data/rail/{stops,routes,route_stops,timetable,trip_days,fares}.csv for Kolkata suburban rail.

Input: sources/raw/rail/erail_trains_index.json (scripts/erail_suburban.py, source S-MR-06) for trains, stop
order and times; OSM station nodes matched on the Indian Railways station code in the `ref` tag
(sources/raw/osm_rail_stations_wide.json, S-MR-01) for coordinates, falling back to erail coordinates.
Trips are clipped to REGION (Kolkata suburban area incl. Bardhaman, Krishnanagar, Namkhana, Panskura;
Kharagpur and beyond are outside lon 87.7 and are clipped). Each trip is assigned to one linear corridor
route by the rules in ROUTES (first match wins).
Usage: venv/bin/python scripts/build_rail.py
"""
import csv, json, math, pathlib, re, statistics, sys
from collections import defaultdict, Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "rail"
REGION = (21.7, 23.5, 87.7, 89.0)
T = json.load(open(ROOT / "sources/raw/rail/erail_trains_index.json"))


def inreg(s):
    try:
        la, lo = float(s["lat"]), float(s["lon"])
    except ValueError:
        return False
    return REGION[0] <= la <= REGION[1] and REGION[2] <= lo <= REGION[3]


# ---- OSM coordinates by station code
osm = {}
osm_by_name = []
norm = lambda n: re.sub(r"[^a-z]", "", re.sub(r"\b(junction|jn|halt|h|road|rd)\b", "", n.lower()))
for f in ("osm_rail_stations_wide.json", "osm_geofabrik_transit_extract.json"):
    for e in json.load(open(ROOT / "sources/raw" / f))["elements"]:
        t = e.get("tags", {})
        if t.get("railway") in ("station", "halt") and t.get("ref") and not t.get("subway") and t.get("station") != "subway":
            c = e.get("center", e)
            for ref in t["ref"].split(";"):
                osm.setdefault(ref.strip(), (c["lat"], c["lon"], t.get("name", ""), f"{e['type']}/{e['id']}"))
        if t.get("railway") in ("station", "halt") and t.get("name") and not t.get("subway") and t.get("station") != "subway":
            c = e.get("center", e)
            osm_by_name.append((c["lat"], c["lon"], t["name"], f"{e['type']}/{e['id']}"))

# ---- route rules: (route_id, name, operator, hub, predicate on set of codes in trip)
S = lambda *c: set(c)
ROUTES = [
    ("rail_circular", "Circular Railway (via BBD Bag) and through services", "Eastern Railway, Sealdah division", "MJT",
     lambda c: bool(c & S("BBDB", "PPGT", "EDG", "BZB", "SOLA", "BBR", "TALA", "KIRP", "RMTR", "KOAA"))),
    ("rail_north_south_through", "North-South through services via Kankurgachi link (bypassing Sealdah)", "Eastern Railway, Sealdah division", "BNXR",
     lambda c: bool(c & S("BNXR", "DDJ", "BP", "NH", "BT", "DDC", "MMG", "KYI", "RHA", "HNB", "BNJ")) and bool(c & S("BLN", "PQS", "SPR", "BRP", "MJT", "KBGB", "CG", "DH", "LKPR"))),
    ("rail_sdah_dankuni", "Sealdah - Dankuni (via Dakshineswar)", "Eastern Railway, Sealdah division", "SDAH", lambda c: "DKAE" in c and "SDAH" in c),
    ("rail_sdah_katwa", "Sealdah - Naihati - Bandel - Katwa line", "Eastern Railway", "SDAH",
     lambda c: "NH" in c and "BDC" in c and bool(c & S("MTFA", "PSAE", "KWAE", "BGRA", "DTAE", "SMAE", "TBAE", "KJU"))),
    ("rail_sdah_bardhaman", "Sealdah - Naihati - Bandel - Bardhaman", "Eastern Railway", "SDAH",
     lambda c: "NH" in c and "BDC" in c and bool(c & S("PDA", "MYM", "BWN", "MUG", "ADST", "TLO", "KHN"))),
    ("rail_bandel_naihati", "Naihati - Bandel", "Eastern Railway", "NH", lambda c: "NH" in c and "BDC" in c),
    ("rail_sdah_hasnabad", "Sealdah - Barasat - Hasnabad", "Eastern Railway, Sealdah division", "SDAH", lambda c: bool(c & S("HNB", "BSHT")) ),
    ("rail_ranaghat_bangaon", "Ranaghat - Bangaon", "Eastern Railway, Sealdah division", "RHA", lambda c: "RHA" in c and "BNJ" in c and not (c & S("SDAH", "STB"))),
    ("rail_sdah_bangaon", "Sealdah - Barasat - Bangaon", "Eastern Railway, Sealdah division", "SDAH",
     lambda c: bool(c & S("BNJ", "HB", "DTK", "GBG", "TKNR", "BT", "MMG", "DDC")) and not (c & S("HNB", "BSHT"))),
    ("rail_sdah_shantipur", "Sealdah - Ranaghat - Shantipur", "Eastern Railway, Sealdah division", "SDAH", lambda c: "STB" in c),
    ("rail_sdah_gede", "Sealdah - Ranaghat - Gede", "Eastern Railway, Sealdah division", "SDAH", lambda c: "GEDE" in c),
    ("rail_sdah_krishnanagar", "Sealdah - Ranaghat - Krishnanagar", "Eastern Railway, Sealdah division", "SDAH", lambda c: bool(c & S("KNJ", "DHU"))),
    ("rail_sdah_kalyani_simanta", "Sealdah - Kalyani Simanta", "Eastern Railway, Sealdah division", "SDAH", lambda c: "KLYM" in c),
    ("rail_sdah_ranaghat", "Sealdah - Naihati - Ranaghat (main line)", "Eastern Railway, Sealdah division", "SDAH",
     lambda c: bool(c & S("RHA", "NH", "BP", "KYI", "BNXR", "DDJ", "KGK"))),
    ("rail_sdah_budge_budge", "Sealdah - Majerhat - Budge Budge", "Eastern Railway, Sealdah division", "SDAH", lambda c: bool(c & S("KBGB", "BGB", "PJA"))),
    ("rail_sdah_canning", "Sealdah - Sonarpur - Canning", "Eastern Railway, Sealdah division", "SDAH", lambda c: bool(c & S("CG", "CHT"))),
    ("rail_sdah_namkhana", "Sealdah - Baruipur - Lakshmikantapur - Namkhana", "Eastern Railway, Sealdah division", "SDAH", lambda c: bool(c & S("LKPR", "NMKA", "KWDP"))),
    ("rail_sdah_diamond_harbour", "Sealdah - Baruipur - Diamond Harbour", "Eastern Railway, Sealdah division", "SDAH", lambda c: "DH" in c),
    ("rail_sdah_south_short", "Sealdah - Ballygunge - Sonarpur - Baruipur (short workings)", "Eastern Railway, Sealdah division", "SDAH", lambda c: bool(c & S("SPR", "BRP", "BLN"))),
    ("rail_hwh_katwa", "Howrah - Bandel - Katwa line", "Eastern Railway, Howrah division", "HWH", lambda c: bool(c & S("MTFA", "PSAE", "KWAE", "BGRA", "DTAE", "SMAE"))),
    ("rail_hwh_tarakeswar", "Howrah - Seoraphuli - Tarakeswar - Arambag - Goghat", "Eastern Railway, Howrah division", "HWH", lambda c: bool(c & S("TAK", "AMBG", "GOGT", "HPL", "DJW", "KMNC"))),
    ("rail_hwh_belur_math", "Howrah - Belur Math", "Eastern Railway, Howrah division", "HWH", lambda c: "BRMH" in c),
    ("rail_hwh_bardhaman_main", "Howrah - Bandel - Bardhaman (main line)", "Eastern Railway, Howrah division", "HWH", lambda c: bool(c & S("BDC", "SRP", "SHE", "PDA", "MYM", "CNS")) ),
    ("rail_hwh_bardhaman_chord", "Howrah - Dankuni - Bardhaman (chord line)", "Eastern Railway, Howrah division", "HWH", lambda c: bool(c & S("DKAE", "BWN", "MSAE", "CDAE", "GRAE", "BRPA"))),
    ("rail_hwh_amta", "Howrah - Santragachi - Amta", "South Eastern Railway, Kharagpur division", "HWH", lambda c: bool(c & S("AMTA", "AMZ", "BCK", "DSNR")) and not (c & S("ULB", "BZN", "PKU"))),
    ("rail_hwh_haldia", "Howrah - Panskura - Haldia", "South Eastern Railway, Kharagpur division", "HWH", lambda c: bool(c & S("HLZ", "MSDL", "DZK"))),
    ("rail_hwh_kharagpur", "Howrah - Santragachi - Panskura (Kharagpur line, clipped at Panskura)", "South Eastern Railway, Kharagpur division", "HWH",
     lambda c: bool(c & S("PKU", "MCA", "KIG", "BZN", "ULB", "SRC", "NSI", "BDPA", "SHM", "TPKR"))),
    ("rail_hwh_main_short", "Howrah - Liluah - Bally (short workings)", "Eastern Railway, Howrah division", "HWH", lambda c: bool(c & S("HWH", "LLH", "BLY"))),
]

# ---- clip trips, assign routes
trips = []
unassigned = Counter()
for t in T:
    st = [s for s in t["stops"] if inreg(s)]
    if len(st) < 2:
        continue
    codes = {s["code"] for s in st}
    rid = next((r[0] for r in ROUTES if r[4](codes)), None)
    if not rid:
        unassigned[(st[0]["code"], st[-1]["code"])] += 1
        continue
    trips.append({"num": t["number"], "name": t["name"], "days": t["days"], "route": rid, "stops": st,
                  "clipped": len(st) < len(t["stops"]), "full_from": t["from"], "full_to": t["to"]})
print("trips kept", len(trips), "unassigned", sum(unassigned.values()), unassigned.most_common(10))

# ---- station positions along each route (km from hub), propagated through shared stations
def tomin(hhmm, day):
    if hhmm in ("First", "Last", "", "None"):
        return None
    h, m = map(int, re.split(r"[.:]", hhmm))
    return h * 60 + m  # erail field 5 is halt minutes, not a day offset; midnight rollover handled by unwrapping

route_pos = {}
viol = Counter()
for r in ROUTES:
    rid, hub = r[0], r[3]
    rt = [x for x in trips if x["route"] == rid]
    if not rt:
        continue
    pos = {hub: 0.0}
    for _ in range(6):
        for x in rt:
            km = {s["code"]: float(s["km"] or 0) for s in x["stops"]}
            known = [c for c in km if c in pos]
            if not known:
                continue
            # orientation: sign so positions increase away from hub
            if len(known) >= 2:
                a, b = known[0], known[-1]
                sign = 1 if (km[b] - km[a]) * (pos[b] - pos[a]) >= 0 else -1
            else:
                sign = 1 if x["stops"][0]["code"] == known[0] or km[known[0]] == min(km.values()) else -1
            off = pos[known[0]] - sign * km[known[0]]
            for c, k in km.items():
                pos.setdefault(c, off + sign * k)
    # orient each trip away from hub using km positions, then order stations topologically from
    # consecutive-stop precedence (km values in erail are occasionally inconsistent between trains)
    succ, indeg, nodes = defaultdict(set), Counter(), set()
    for x in rt:
        cs = [s["code"] for s in x["stops"]]
        if pos.get(cs[-1], 0) < pos.get(cs[0], 0):
            cs = cs[::-1]
        nodes.update(cs)
        for a, b in zip(cs, cs[1:]):
            if b not in succ[a]:
                succ[a].add(b); indeg[b] += 1
    order, ready = [], sorted([n for n in nodes if indeg[n] == 0], key=lambda n: pos.get(n, 0))
    while ready:
        n = ready.pop(0); order.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort(key=lambda n: pos.get(n, 0))
    if len(order) < len(nodes):
        viol[rid] += len(nodes) - len(order)
        order += sorted(nodes - set(order), key=lambda n: pos.get(n, 0))
    # a linear route has a single start node; more than one means branching
    starts = [n for n in nodes if not any(n in succ[m] for m in nodes)]
    if len(starts) > 1:
        viol[rid + ":branches"] = ",".join(sorted(starts))
    pos = {c: i for i, c in enumerate(order)}
    route_pos[rid] = pos
print("ordering problems per route (cycle nodes / branch starts)", dict(viol))

# ---- stops
names, coords = {}, {}
for x in trips:
    for s in x["stops"]:
        names.setdefault(s["code"], s["name"]); coords.setdefault(s["code"], (s["lat"], s["lon"]))
slug = lambda s: re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
sid, used, sname = {}, set(), {}
far = []
with open(OUT / "stops.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["stop_id", "stop_name", "lat", "lon", "source_id", "notes"])
    for code in sorted(names):
        el, eo = map(float, coords[code])
        if code in osm:
            la, lo, nm, ref = osm[code]
            dist = math.dist((la, lo), (el, eo)) * 111
            src, note = "S-MR-01", f"IR code {code}; OSM {ref}; erail coords {dist:.2f} km away"
            if dist > 1.0:
                far.append((code, round(dist, 1)))
                note += " (check)"
        elif (cand := [o for o in osm_by_name if norm(o[2]) and norm(o[2]) in (norm(names[code]), norm(names[code].replace("Komagata Maru ", "")))
                       and math.dist((o[0], o[1]), (el, eo)) * 111 < 3]):
            la, lo, nm, ref = min(cand, key=lambda o: math.dist((o[0], o[1]), (el, eo)))
            src, note = "S-MR-01", f"IR code {code}; OSM {ref} matched by name (ref tag differs or missing); erail coords {math.dist((la, lo), (el, eo)) * 111:.2f} km away"
        else:
            la, lo, nm = el, eo, names[code]
            src, note = "S-MR-06", f"IR code {code}; not matched in OSM by ref, coordinates from erail (community)"
        nm = nm or names[code]
        base = "rail_" + slug(re.sub(r"\b(Junction|Jn)\b\.?", "", nm))
        s_id = base if base not in used else f"{base}_{code.lower()}"
        used.add(s_id); sid[code] = s_id; sname[code] = nm
        w.writerow([s_id, nm, round(la, 6), round(lo, 6), src, note])
print("stops", len(sid), "osm matched", sum(1 for c in sid if c in osm), "far>1km", far)

# ---- timetable + trip days
def fmt(m):
    return f"{m // 60:02d}:{m % 60:02d}:00"

rows, days_rows, dir_of = [], [], {}
for x in trips:
    pos = route_pos[x["route"]]
    p0, p1 = pos.get(x["stops"][0]["code"], 0), pos.get(x["stops"][-1]["code"], 0)
    d = 0 if p1 >= p0 else 1
    dir_of[x["num"]] = d
    last = None
    for i, s in enumerate(x["stops"], 1):
        a, dp = tomin(s["arr"], None), tomin(s["dep"], None)
        a = a if a is not None else dp
        dp = dp if dp is not None else a
        if last is not None:
            while a < last:
                a += 1440
        while dp < a:
            dp += 1440
        last = dp
        x.setdefault("times", []).append((a, dp))
        rows.append([x["route"], x["num"], d, i, sid[s["code"]], fmt(a), fmt(dp)])
    days_rows.append([x["num"], x["route"], x["days"], x["name"], x["full_from"], x["full_to"], "yes" if x["clipped"] else "no"])
with open(OUT / "timetable.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["route_id", "trip_id", "direction", "seq", "stop_id", "arrival", "departure"]); w.writerows(rows)
with open(OUT / "trip_days.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["trip_id", "route_id", "days_mask_mon_to_sun", "train_name", "full_origin_code", "full_destination_code", "clipped_to_region"])
    w.writerows(days_rows)

# ---- route_stops (stations ordered by position) with median running minutes from first station
rs_rows, route_rows = [], []
for r in ROUTES:
    rid, name, op, hub = r[0], r[1], r[2], r[3]
    rt = [x for x in trips if x["route"] == rid]
    if not rt:
        continue
    pos = route_pos[rid]
    order = sorted(pos, key=pos.get)
    order = [c for c in order if c in sid]
    branching = isinstance(viol.get(rid + ":branches"), str)
    if branching:
        # not a single line: template = stop list of the trip with most stops (oriented away from hub)
        rep = max(rt, key=lambda x: len(x["stops"]))
        order = [s_["code"] for s_ in rep["stops"]]
        if dir_of[rep["num"]] == 1:
            order = order[::-1]
    for d, seq in ((0, order), (1, list(reversed(order)))):
        # travel time: median over trips in this direction that serve both seq[0] and the station
        tt = defaultdict(list)
        for x in rt:
            if dir_of[x["num"]] != d:
                continue
            codes = [s["code"] for s in x["stops"]]
            if seq[0] not in codes:
                continue
            t0 = x["times"][codes.index(seq[0])][1]
            for c, (a, dp) in zip(codes, x["times"]):
                tt[c].append(a - t0)
        for i, c in enumerate(seq, 1):
            v = tt.get(c)
            rs_rows.append([rid, d, i, sid[c], int(statistics.median(v)) if v else ""])
    # service figures on weekdays (mask position 1-5 assumed Mon-Fri)
    wk = [x for x in rt if x["days"][:5] == "11111"]
    deps = [x["times"][0][1] % 1440 for x in wk]
    busiest = Counter(s["code"] for x in wk for s in x["stops"]).most_common(1)[0][0]
    hws = {}
    for lab, wins in (("peak", [(420, 600), (1020, 1200)]), ("off", [(660, 960)])):
        gaps = []
        for d in (0, 1):
            ts = sorted(x["times"][[s["code"] for s in x["stops"]].index(busiest)][1] for x in wk
                        if dir_of[x["num"]] == d and busiest in [s["code"] for s in x["stops"]])
            ts = [t % 1440 for t in ts]
            ts.sort()
            gaps += [b - a for a, b in zip(ts, ts[1:]) if any(w0 <= a and b <= w1 for w0, w1 in wins)]
        hws[lab] = round(statistics.median(gaps)) if gaps else ""
    o, dname = sname[order[0]], sname[order[-1]]
    clipped = sum(x["clipped"] for x in rt)
    extra = (" Route has branches/through patterns, so route_stops shows only the longest trip pattern (train " + rep["num"] + "); see timetable.csv for every trip.") if branching else ""
    route_rows.append([rid, name, "rail", op, o, dname, hws["peak"], hws["off"], fmt(min(deps) % 1440)[:5] if deps else "",
                       fmt(max(deps) % 1440)[:5] if deps else "", 5, "", "S-MR-06;S-MR-01", "community",
                       f"{len(rt)} trips in erail ({len(wk)} on weekdays); headways are medians of weekday departure gaps at {sname[busiest]} ({busiest}), both directions, peak 07-10 and 17-20, off-peak 11-16. first/last = earliest/latest weekday trip departure from its origin. {clipped} trips continue beyond the region and are clipped. Fare by distance, see fares.csv." + extra])

# fare max per route from the slab table, using route length in km
SLABS = [(20, 5), (45, 10), (70, 15), (100, 20), (125, 25)]
for row in route_rows:
    span = max(float(x["stops"][-1]["km"] or 0) - float(x["stops"][0]["km"] or 0) for x in trips if x["route"] == row[0])
    row[11] = next((fare for lim, fare in SLABS if span <= lim), "")
    row[14] += f" Route length about {span:.0f} km."

with open(OUT / "routes.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["route_id", "route_name", "mode", "operator", "origin", "destination", "headway_min_peak", "headway_min_offpeak",
                "first_service", "last_service", "fare_min_inr", "fare_max_inr", "source_id", "confidence", "notes"])
    w.writerows(route_rows)
with open(OUT / "route_stops.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["route_id", "direction", "seq", "stop_id", "travel_min_from_start"]); w.writerows(rs_rows)
with open(OUT / "fares.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["applies_to", "km_from", "km_to", "fare_inr", "source_id", "confidence", "notes"])
    note = "Second class single journey, suburban EMU. Derived from erail fare fields for all queried station pairs: observed 0-20 km Rs 5, 21-41 km Rs 10, 50-68 km Rs 15, 77-78 km Rs 20, 112 km Rs 25. Exact slab edges between observations follow the standard IR suburban table (assumed)."
    for a, b, p in [(1, 20, 5), (21, 45, 10), (46, 70, 15), (71, 100, 20), (101, 125, 25)]:
        w.writerow(["rail_all", a, b, p, "S-MR-06", "community", note])
print("routes", len(route_rows), "route_stops", len(rs_rows), "timetable rows", len(rows), "trips", len(trips))
