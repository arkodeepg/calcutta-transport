"""Build the GTFS feed in gtfs/ from the data/ CSVs.

Inputs (read only): data/{bus,metro,rail,auto,ferry,tram}/{routes,stops,route_stops}.csv,
data/rail/{timetable,trip_days}.csv. Fares are taken from route-level fare_min_inr/fare_max_inr only.
Outputs: gtfs/*.txt, gtfs/BUILD_REPORT.md, gtfs/calcutta-transport-gtfs.zip.

Modelling decisions (also written into BUILD_REPORT.md):
- Rail: real trips from timetable.csv, one calendar service per days_mask_mon_to_sun (Mon..Sun order;
  confirmed by the Saturday-only "SAO" train being 0000010).
- Every other mode: one template trip per route and direction plus frequencies.txt rows (exact_times=0).
  Peak headway applies 07:00-10:00 and 17:00-20:00, off-peak headway elsewhere. Blank headway or
  first/last service falls back to DEFAULT_HEADWAY_MIN / DEFAULT_WINDOW and is listed in the report.
- Template stop times: travel_min_from_start where given (anchors), otherwise straight-line distance x
  DETOUR / SPEED_KMH per mode, interpolated between anchors.
- Stops without coordinates are dropped from the stop sequence; a route-direction left with < 2 located
  stops is dropped, and a route with no surviving direction is dropped.
- Auto (share auto) routes are route_type 3 under their own agency, not extended type 1501/715, for
  consumer compatibility. Data has direction 0 only; MIRROR_AUTO adds the reverse as direction 1.
- Fares v1 only for ferry routes with a single flat fare (fare_min == fare_max). Distance-slab fares (bus,
  metro, rail) need along-track km between stop pairs, which the data does not carry, so they are skipped.
- transfers.txt links metro/rail/ferry/tram stops that share a name and are within TRANSFER_MAX_M.

Usage: venv/bin/python scripts/build_gtfs.py [--publisher-url URL] [--date YYYYMMDD] [--days 365]
Re-runnable: clears gtfs/*.txt before writing.
"""
import argparse, csv, datetime as dt, math, pathlib, re, zipfile
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "gtfs"
MODES = ["metro", "rail", "bus", "tram", "ferry", "auto"]
TZ = "Asia/Kolkata"

ROUTE_TYPE = {"metro": 1, "rail": 2, "bus": 3, "minibus": 3, "ferry": 4, "tram": 0, "auto": 3}
DEFAULT_HEADWAY_MIN = {"bus": 15, "minibus": 15, "auto": 10, "tram": 20, "ferry": 20, "metro": 10}
DEFAULT_WINDOW = ("06:00", "21:00")
SPEED_KMH = {"bus": 15, "minibus": 15, "auto": 18, "tram": 10, "ferry": 10, "metro": 33}
DETOUR = 1.3  # straight-line to road/track distance factor
MIN_HOP_S = 30  # minimum estimated seconds between consecutive stops
PEAK = [("07:00", "10:00"), ("17:00", "20:00")]
MIRROR_AUTO = True
TRANSFER_MAX_M = 600
TRANSFER_MODES = {"metro", "rail", "ferry", "tram"}
SPIKE_MIN_M = 5000
LONG_HOP_M = 20000
FLAT_FARE_MODES = {"ferry"}  # share autos charge by stage; their single route fare is end to end only

METRO_COLORS = {"metro_blue": ("1E5AA8", "FFFFFF"), "metro_green": ("1B8A4A", "FFFFFF"),
                "metro_purple": ("6F2C91", "FFFFFF"), "metro_orange": ("F28C28", "000000"),
                "metro_yellow": ("F2C300", "000000")}

AGENCY_URLS = {
    "wbtc": ("West Bengal Transport Corporation (WBTC)", "https://wbtc.co.in/"),
    "private_bus": ("Private bus operators (Kolkata)", "https://transport.wb.gov.in/"),
    "minibus": ("Private minibus operators (Kolkata)", "https://transport.wb.gov.in/"),
    "share_auto": ("Share auto route operators (Kolkata)", "https://transport.wb.gov.in/"),
    "mrk": ("Metro Railway Kolkata", "https://mtp.indianrailways.gov.in/"),
    "er": ("Eastern Railway", "https://er.indianrailways.gov.in/"),
    "ser": ("South Eastern Railway", "https://ser.indianrailways.gov.in/"),
    "wbstc_ferry": ("West Bengal Surface Transport Corporation (WBTC ferry)", "https://wbtc.co.in/ferry-service/"),
    "other_ferry": ("Other Hooghly ferry operators", "https://transport.wb.gov.in/"),
}


def agency_for(mode, operator):
    op = (operator or "").lower()
    if mode == "metro":
        return "mrk"
    if mode == "rail":
        return "ser" if "south eastern" in op else "er"
    if mode == "tram":
        return "wbtc"
    if mode == "ferry":
        return "wbstc_ferry" if "wbstc" in op or "wbtc" in op else "other_ferry"
    if mode == "auto":
        return "share_auto"
    if mode == "minibus":
        return "minibus"
    return "wbtc" if "wbtc" in op else "private_bus"


def read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def hms_to_s(t):
    p = [int(x) for x in t.strip().split(":")]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def s_to_hms(s):
    s = int(round(s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def norm_name(n):
    n = n.lower()
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"\b(junction|jn|station|terminal|halt|ghat|launch|metro|road|rd)\b", " ", n)
    return re.sub(r"[^a-z]+", " ", n).strip()


def names_match(a, b):
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return False
    return a == b or a.split()[0] == b.split()[0]


def write(name, header, rows):
    with open(OUT / name, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


class Report:
    def __init__(self):
        self.mode = defaultdict(Counter)
        self.defaults = defaultdict(list)
        self.warn = []
        self.dropped_routes = defaultdict(list)

    def w(self, msg):
        self.warn.append(msg)


def freq_windows(r, mode, rep):
    """Return list of (start_s, end_s, headway_s) for a frequency route, recording defaults used."""
    rid = r["route_id"]
    first, last = r.get("first_service", "").strip(), r.get("last_service", "").strip()
    if not first:
        first = DEFAULT_WINDOW[0]
        rep.defaults["first_service"].append(rid)
    if not last:
        last = DEFAULT_WINDOW[1]
        rep.defaults["last_service"].append(rid)
    a, b = hms_to_s(first), hms_to_s(last)
    if b <= a:
        b += 86400  # after-midnight last service
    hp, ho = r.get("headway_min_peak", "").strip(), r.get("headway_min_offpeak", "").strip()
    if not hp and not ho:
        hp = ho = str(DEFAULT_HEADWAY_MIN[mode])
        rep.defaults["headway"].append(rid)
    elif not hp or not ho:
        hp = hp or ho
        ho = ho or hp
        rep.defaults["headway_one_side_copied"].append(rid)
    hp_s, ho_s = int(round(float(hp) * 60)), int(round(float(ho) * 60))
    cuts = sorted({a, b} | {hms_to_s(x) for p in PEAK for x in p if a < hms_to_s(x) < b})
    out = []
    for s, e in zip(cuts, cuts[1:]):
        peak = any(hms_to_s(p0) <= s < hms_to_s(p1) for p0, p1 in PEAK)
        h = hp_s if peak else ho_s
        if out and out[-1][2] == h and out[-1][1] == s:
            out[-1] = (out[-1][0], e, h)
        else:
            out.append((s, e, h))
    return out


def template_times(seq, coords, mode, rep, key):
    """seq: list of (stop_id, travel_min or None). Return list of (seconds, is_anchor)."""
    est = [0.0]
    sp = SPEED_KMH[mode] / 3.6
    for i in range(1, len(seq)):
        d = haversine_m(coords[seq[i - 1][0]], coords[seq[i][0]]) * DETOUR
        est.append(est[-1] + max(d / sp, MIN_HOP_S))
    anchors = [(i, float(t) * 60) for i, (_, t) in enumerate(seq) if t not in (None, "")]
    if anchors and any(b[1] < a[1] for a, b in zip(anchors, anchors[1:])):
        rep.w(f"{key}: travel_min_from_start decreases along the sequence; ignored, used distance estimate")
        anchors = []
    if not anchors:
        return [(t, False) for t in est]
    res = [None] * len(seq)
    for i, t in anchors:
        res[i] = t
    a0, t0 = anchors[0]
    for i in range(a0):
        res[i] = t0 - (est[a0] - est[i])
    for (ia, ta), (ib, tb) in zip(anchors, anchors[1:]):
        span = est[ib] - est[ia]
        for i in range(ia + 1, ib):
            res[i] = ta + (tb - ta) * ((est[i] - est[ia]) / span if span else 0)
    al, tl = anchors[-1]
    for i in range(al + 1, len(seq)):
        res[i] = tl + est[i] - est[al]
    base = res[0]
    aset = {i for i, _ in anchors}
    out = []
    for i, t in enumerate(res):
        t = t - base
        if out and t < out[-1][0]:
            t = out[-1][0]
        out.append((t, i in aset))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publisher-url", default="https://www.openstreetmap.org/copyright",
                    help="feed_publisher_url (replace with the project URL once one exists)")
    ap.add_argument("--date", default=dt.date.today().strftime("%Y%m%d"), help="build date YYYYMMDD")
    ap.add_argument("--days", type=int, default=365, help="calendar validity from build date")
    args = ap.parse_args()
    build = dt.datetime.strptime(args.date, "%Y%m%d").date()
    start, end = build.strftime("%Y%m%d"), (build + dt.timedelta(days=args.days)).strftime("%Y%m%d")
    OUT.mkdir(exist_ok=True)
    for f in OUT.glob("*.txt"):
        f.unlink()
    rep = Report()

    stops_all, coords, stop_mode = {}, {}, {}
    routes_out, trips_out, st_out, freq_out, fares_attr, fares_rules = [], [], [], [], [], []
    used_stops, agencies_used = set(), set()
    services = {}

    for mode in MODES:
        d = DATA / mode
        routes = read(d / "routes.csv")
        stops = read(d / "stops.csv")
        rstops = read(d / "route_stops.csv")
        c = rep.mode[mode]
        c["routes_in"] = len(routes)
        c["stops_in"] = len(stops)
        for s in stops:
            sid = s["stop_id"]
            if sid in stops_all:
                rep.w(f"duplicate stop_id across modes: {sid}")
                continue
            stops_all[sid] = s
            stop_mode[sid] = mode
            try:
                lat, lon = float(s["lat"]), float(s["lon"])
                if not (21.0 < lat < 24.0 and 87.0 < lon < 90.0):
                    rep.w(f"{sid}: coordinate outside the region box, treated as unlocated")
                    raise ValueError
                coords[sid] = (lat, lon)
            except (ValueError, TypeError):
                c["stops_unlocated"] += 1
        by_rd = defaultdict(list)
        for x in rstops:
            if x["stop_id"] not in stops_all:
                rep.w(f"{mode} route_stops references unknown stop {x['stop_id']} ({x['route_id']})")
                continue
            by_rd[(x["route_id"], x["direction"])].append(x)

        if mode == "rail":
            build_rail(routes, rep, c, coords, routes_out, trips_out, st_out, used_stops,
                       agencies_used, services, stops_all)
            continue

        for r in routes:
            rid = r["route_id"]
            rmode = (r.get("mode") or mode).strip() or mode
            if rmode not in ROUTE_TYPE:
                rep.w(f"{rid}: unknown mode '{rmode}', treated as {mode}")
                rmode = mode
            speed_mode = rmode if rmode in SPEED_KMH else mode
            dirs = sorted(d_ for (r_, d_) in by_rd if r_ == rid)
            pats = {}
            for di in dirs:
                rows = sorted(by_rd[(rid, di)], key=lambda x: int(x["seq"]))
                kept, dropped = [], 0
                for x in rows:
                    if x["stop_id"] not in coords:
                        dropped += 1
                        continue
                    if kept and kept[-1][0] == x["stop_id"]:
                        continue
                    kept.append((x["stop_id"], x.get("travel_min_from_start", "").strip() or None))
                c["route_stop_rows_in"] += len(rows)
                c["route_stop_rows_dropped_unlocated"] += dropped
                if dropped and any(t is not None for _, t in kept):
                    # anchors stay relative to the original origin; template_times re-bases on the first kept stop
                    pass
                if len(kept) < 2:
                    c["directions_dropped_lt2"] += 1
                    continue
                if di == "0":
                    check_geometry(kept, coords, stops_all, rid, rep, c)
                pats[di] = kept
            if MIRROR_AUTO and mode == "auto" and "0" in pats and "1" not in pats:
                pats["1"] = [(s, None) for s, _ in reversed(pats["0"])]
                c["directions_mirrored"] += 1
            if not pats:
                c["routes_dropped"] += 1
                rep.dropped_routes[mode].append(rid)
                continue
            ag = agency_for(rmode, r.get("operator"))
            agencies_used.add(ag)
            short, long_ = route_names(mode, r)
            col, tcol = METRO_COLORS.get(rid, ("", ""))
            routes_out.append([rid, ag, short, long_, ROUTE_TYPE[rmode], col, tcol])
            c["routes_out"] += 1
            if rmode == "minibus":
                c["of_which_minibus"] += 1
            wins = freq_windows(r, mode, rep)
            services["daily"] = "1111111"
            for di, kept in sorted(pats.items()):
                tid = f"{rid}_d{di}"
                times = template_times(kept, coords, speed_mode, rep, f"{rid} dir {di}")
                trips_out.append([rid, "daily", tid, stops_all[kept[-1][0]]["stop_name"], int(di)])
                t0 = wins[0][0]
                for i, ((sid, _), (t, anchor)) in enumerate(zip(kept, times)):
                    hms = s_to_hms(t0 + t)
                    st_out.append([tid, hms, hms, sid, i + 1, 1 if anchor else 0])
                    used_stops.add(sid)
                for s, e, h in wins:
                    freq_out.append([tid, s_to_hms(s), s_to_hms(e), h, 0])
                c["trips_out"] += 1
                if any(a for _, a in times):
                    c["directions_with_given_times"] += 1
                else:
                    c["directions_estimated_times"] += 1
            add_fare(r, rid, fares_attr, fares_rules, rep, mode, ag)

    # stops.txt (only stops used by a trip)
    stops_rows = []
    for sid in sorted(used_stops):
        s = stops_all[sid]
        lat, lon = coords[sid]
        stops_rows.append([sid, s["stop_name"].strip(), f"{lat:.6f}", f"{lon:.6f}", 0])
        rep.mode[stop_mode[sid]]["stops_out"] += 1

    # transfers.txt between named-alike stops of the rail-like modes
    transfers, pairs = [], []
    tl = [s for s in used_stops if stop_mode[s] in TRANSFER_MODES]
    for i, a in enumerate(tl):
        for b in tl[i + 1:]:
            if stop_mode[a] == stop_mode[b] == "rail":
                continue
            dist = haversine_m(coords[a], coords[b])
            if dist <= TRANSFER_MAX_M and names_match(stops_all[a]["stop_name"], stops_all[b]["stop_name"]):
                mtt = int(math.ceil((120 + dist / 1.0) / 60) * 60)  # 2 min + walk at 1 m/s
                transfers += [[a, b, 2, mtt], [b, a, 2, mtt]]
                pairs.append((a, b, int(dist), mtt))

    write("agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"],
          [[a, AGENCY_URLS[a][0], AGENCY_URLS[a][1], TZ, "en"] for a in sorted(agencies_used)])
    write("stops.txt", ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type"], stops_rows)
    write("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type",
                         "route_color", "route_text_color"], routes_out)
    write("trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"], trips_out)
    write("stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
                             "timepoint"], st_out)
    write("frequencies.txt", ["trip_id", "start_time", "end_time", "headway_secs", "exact_times"], freq_out)
    write("calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                           "sunday", "start_date", "end_date"],
          [[sid] + list(m) + [start, end] for sid, m in sorted(services.items())])
    write("feed_info.txt", ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date",
                            "feed_end_date", "feed_version"],
          [["Calcutta Transport contributors", args.publisher_url, "en", start, end, start]])
    if transfers:
        write("transfers.txt", ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"], transfers)
    if fares_attr:
        write("fare_attributes.txt", ["fare_id", "price", "currency_type", "payment_method", "transfers", "agency_id"],
              fares_attr)
        write("fare_rules.txt", ["fare_id", "route_id"], fares_rules)

    files = sorted(OUT.glob("*.txt"))
    zp = OUT / "calcutta-transport-gtfs.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)

    write_report(rep, start, end, routes_out, trips_out, st_out, freq_out, stops_rows, transfers, pairs,
                 fares_attr, services, args.publisher_url, stops_all)
    print(f"built {len(routes_out)} routes, {len(trips_out)} trips, {len(st_out)} stop_times, "
          f"{len(stops_rows)} stops, {len(freq_out)} frequencies, {len(transfers)} transfers -> {zp}")


def check_geometry(kept, coords, stops_all, rid, rep, c):
    """Flag likely mis-located stops (out-and-back spikes) and very long hops; data is left unchanged."""
    pts = [coords[s] for s, _ in kept]
    for i in range(1, len(pts) - 1):
        a, b = haversine_m(pts[i - 1], pts[i]), haversine_m(pts[i], pts[i + 1])
        direct = haversine_m(pts[i - 1], pts[i + 1])
        if min(a, b) > SPIKE_MIN_M and a + b > 3 * direct:
            c["suspect_stop_spikes"] += 1
            rep.w(f"{rid}: stop {kept[i][0]} ({stops_all[kept[i][0]]['stop_name']}) is {a / 1000:.0f} km off the "
                  f"line between its neighbours (out-and-back spike, likely wrong coordinate)")
    for i in range(1, len(pts)):
        if haversine_m(pts[i - 1], pts[i]) > LONG_HOP_M:
            c["hops_over_20km"] += 1


def route_names(mode, r):
    name, o, de = r["route_name"].strip(), r.get("origin", "").strip(), r.get("destination", "").strip()
    od = f"{o} - {de}" if o and de else name
    if mode == "bus":
        if " - " in name or name.lower() == od.lower():
            return "", od  # named, unnumbered routes (e.g. STA): long name only
        m = re.match(r"^([^()]+?)\s*\((.+)\)$", name)
        if m and len(name) > 12:
            return m.group(1).strip(), f"{od} ({m.group(2).strip()})"
        return name, od
    if mode == "tram":
        m = re.match(r"tram_(\w+)", r["route_id"])
        return (m.group(1) if m else ""), od
    if mode == "auto":
        m = re.match(r"auto_r(\d+)$", r["route_id"])
        return (m.group(1) if m else ""), name
    return "", name


def add_fare(r, rid, attr, rules, rep, mode, agency):
    lo, hi = r.get("fare_min_inr", "").strip(), r.get("fare_max_inr", "").strip()
    if not lo or not hi:
        return
    try:
        lo_f, hi_f = float(lo), float(hi)
    except ValueError:
        rep.w(f"{rid}: non-numeric fare '{lo}'/'{hi}'")
        return
    if lo_f != hi_f or mode not in FLAT_FARE_MODES:
        return
    fid = f"fare_{rid}"
    attr.append([fid, f"{lo_f:.2f}", "INR", 0, 0, agency])
    rules.append([fid, rid])


def build_rail(routes, rep, c, coords, routes_out, trips_out, st_out, used_stops, agencies_used, services,
               stops_all):
    tt = read(DATA / "rail" / "timetable.csv")
    td = {t["trip_id"]: t for t in read(DATA / "rail" / "trip_days.csv")}
    rids = {r["route_id"]: r for r in routes}
    by_trip = defaultdict(list)
    for x in tt:
        by_trip[x["trip_id"]].append(x)
    routes_with_trips = set()
    masks = Counter()
    for trip, rows in by_trip.items():
        rows.sort(key=lambda x: int(x["seq"]))
        rid = rows[0]["route_id"]
        if rid not in rids:
            rep.w(f"rail trip {trip}: unknown route {rid}, skipped")
            c["trips_skipped"] += 1
            continue
        if trip not in td:
            rep.w(f"rail trip {trip}: no trip_days row, skipped")
            c["trips_skipped"] += 1
            continue
        mask = td[trip]["days_mask_mon_to_sun"].strip()
        if not re.fullmatch(r"[01]{7}", mask) or mask == "0000000":
            rep.w(f"rail trip {trip}: bad days mask '{mask}', skipped")
            c["trips_skipped"] += 1
            continue
        kept = [x for x in rows if x["stop_id"] in coords]
        if len(kept) < 2:
            c["trips_skipped"] += 1
            continue
        prev, fixed = -1, False
        times = []
        for x in kept:
            a, d = hms_to_s(x["arrival"]), hms_to_s(x["departure"])
            while a < prev:
                a += 86400
                fixed = True
            while d < a:
                d += 86400 if a - d > 43200 else 0
                if d < a:
                    d = a
                    fixed = True
            times.append((a, d))
            prev = d
        if fixed:
            c["trips_time_order_fixed"] += 1
            rep.w(f"rail trip {trip}: times went backwards, rolled past midnight or clamped")
        sid_ = f"rail_{mask}"
        services[sid_] = mask
        masks[mask] += 1
        tid = f"rail_{trip}"
        dirn = rows[0].get("direction", "0") or "0"
        name = td[trip].get("train_name", "").strip()
        trips_out.append([rid, sid_, tid, stops_all[kept[-1]["stop_id"]]["stop_name"], int(dirn)])
        for i, (x, (a, d)) in enumerate(zip(kept, times)):
            st_out.append([tid, s_to_hms(a), s_to_hms(d), x["stop_id"], i + 1, 1])
            used_stops.add(x["stop_id"])
        routes_with_trips.add(rid)
        c["trips_out"] += 1
        c["stop_time_rows_dropped_unlocated"] += len(rows) - len(kept)
    for rid, r in rids.items():
        if rid not in routes_with_trips:
            c["routes_dropped"] += 1
            rep.dropped_routes["rail"].append(rid)
            continue
        ag = agency_for("rail", r.get("operator"))
        agencies_used.add(ag)
        routes_out.append([rid, ag, "", r["route_name"].strip(), ROUTE_TYPE["rail"], "", ""])
        c["routes_out"] += 1
    rep.rail_masks = masks


def write_report(rep, start, end, routes_out, trips_out, st_out, freq_out, stops_rows, transfers, pairs,
                 fares_attr, services, pub_url, stops_all):
    L = [f"# GTFS build report", "",
         f"Built {start} by `scripts/build_gtfs.py`. Calendar valid {start} to {end}. Generated file, do not edit.", "",
         "## Feed totals", "",
         f"| file | rows |", "|---|---|"]
    for n, rows in [("routes.txt", routes_out), ("trips.txt", trips_out), ("stop_times.txt", st_out),
                    ("frequencies.txt", freq_out), ("stops.txt", stops_rows), ("transfers.txt", transfers),
                    ("fare_attributes.txt", fares_attr), ("calendar.txt", list(services))]:
        L.append(f"| {n} | {len(rows)} |")
    L += ["", "## Per mode", "",
          "| mode | routes in | routes out | routes dropped | stops in | stops unlocated | stops in feed | trips | notes |",
          "|---|---|---|---|---|---|---|---|---|"]
    for m in MODES:
        c = rep.mode[m]
        notes = []
        for k in ["of_which_minibus", "route_stop_rows_in", "route_stop_rows_dropped_unlocated",
                  "directions_dropped_lt2", "directions_mirrored", "directions_with_given_times",
                  "directions_estimated_times", "stop_time_rows_dropped_unlocated", "trips_skipped",
                  "trips_time_order_fixed", "suspect_stop_spikes", "hops_over_20km"]:
            if c.get(k):
                notes.append(f"{k}={c[k]}")
        L.append(f"| {m} | {c['routes_in']} | {c['routes_out']} | {c['routes_dropped']} | {c['stops_in']} | "
                 f"{c['stops_unlocated']} | {c['stops_out']} | {c['trips_out']} | {', '.join(notes)} |")
    L += ["", "Stops in feed counts only located stops that at least one trip uses.", "",
          "## Modelling and defaults", "",
          "- route_type: metro 1, rail 2, bus 3, minibus 3 (agency: private minibus operators), tram 0, ferry 4, "
          "share auto 3 (agency: share auto route operators). Extended types (1501 communal taxi, 715 demand "
          "responsive) were not used: share autos run fixed routes without booking, and basic type 3 is understood "
          "by every consumer, while extended types are not.",
          "- Rail: real trips from data/rail/timetable.csv; one service per days mask (Mon..Sun order, confirmed "
          "by the Saturday-only train HWH-BWN LOCAL SAO having mask 0000010). Masks: " +
          ", ".join(f"{k} x{v}" for k, v in sorted(getattr(rep, 'rail_masks', {}).items())) + ".",
          "- Other modes: one template trip per route direction, frequencies.txt with exact_times=0. Peak headway "
          "07:00-10:00 and 17:00-20:00, off-peak elsewhere. The same calendar (daily) is used for all days; "
          "weekend differences (e.g. metro Sunday 09:00 start) are not modelled.",
          f"- Default headways when both headway columns are blank: " +
          ", ".join(f"{k} {v} min" for k, v in DEFAULT_HEADWAY_MIN.items()) +
          f". Default service window when first/last is blank: {DEFAULT_WINDOW[0]} to {DEFAULT_WINDOW[1]}.",
          f"- Estimated stop times: straight-line distance x {DETOUR} at " +
          ", ".join(f"{k} {v} km/h" for k, v in SPEED_KMH.items()) +
          f", at least {MIN_HOP_S} s per hop; travel_min_from_start values are used as anchors where present "
          "and the estimate is interpolated between them. Estimated times have timepoint=0.",
          "- Stops without coordinates are removed from each stop sequence; a direction with fewer than 2 "
          "located stops is dropped, and a route with no direction left is dropped.",
          "- Share auto data has direction 0 only; the builder adds the reverse sequence as direction 1 "
          "(MIRROR_AUTO), since route autos shuttle both ways." if MIRROR_AUTO else "",
          "- shapes.txt is not produced (no cheap, reliable geometry for all modes).", ""]
    L += ["## Routes using defaults", ""]
    labels = {"headway": "headway default (both columns blank)",
              "headway_one_side_copied": "one headway column blank, other copied",
              "first_service": f"first_service default {DEFAULT_WINDOW[0]}",
              "last_service": f"last_service default {DEFAULT_WINDOW[1]}"}
    for k, lab in labels.items():
        v = rep.defaults.get(k, [])
        by = Counter(x.split("_")[0] for x in v)
        L.append(f"- {lab}: {len(v)} routes ({', '.join(f'{m} {n}' for m, n in sorted(by.items())) or 'none'})"
                 + (f": {', '.join(v)}" if 0 < len(v) <= 40 else ""))
    L += ["", "## Dropped routes", ""]
    for m in MODES:
        v = rep.dropped_routes.get(m, [])
        L.append(f"- {m}: {len(v)}" + (f": {', '.join(v)}" if 0 < len(v) <= 60 else ""))
    L += ["", "## Transfers", "",
          f"{len(pairs)} stop pairs (both directions written, transfer_type 2) among metro, rail, ferry and tram "
          f"stops with matching names within {TRANSFER_MAX_M} m:", ""]
    for a, b, dist, mtt in sorted(pairs):
        L.append(f"- {stops_all[a]['stop_name']} ({a}) and {stops_all[b]['stop_name']} ({b}): {dist} m, "
                 f"min {mtt} s")
    L += ["", "## Fares", "",
          f"GTFS fares v1 written only for ferry routes whose fare_min_inr equals fare_max_inr ({len(fares_attr)} "
          "routes, one flat fare per crossing, no transfers). Share auto routes with one fare value are not "
          "encoded: that value is the end-to-end fare and autos charge less for shorter stages. Bus and minibus slabs, metro slabs and rail slabs are "
          "distance based and need along-track km between every stop pair, which the data does not carry; they "
          "are documented in data/fares/ and data/{metro,rail}/fares.csv but not encoded in the feed.", "",
          "## Warnings", ""]
    L += [f"- {w}" for w in rep.warn] or ["- none"]
    L += ["", "## Notes", "",
          f"- feed_publisher_url is set to {pub_url}; replace it with the project URL once one is published "
          "(`--publisher-url`).", ""]
    (OUT / "BUILD_REPORT.md").write_text("\n".join(x for x in L if x is not None) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
