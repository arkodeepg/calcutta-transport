"""Build data/bus/{stops,routes,route_stops}.csv from the published upstream route lists + OpenStreetMap.

Route sources (fetched first-hand, see bus_fetch_upstream.py; parsed by bus_common.load_upstream):
  wbtc_routes   WBTC city bus route table (wbtconline.in)
  kolbusopedia  Kolkata Bus-O-Pedia private and government route catalogue (kolbusopedia.com)
  osm           OpenStreetMap route=bus relations (route numbers not in either list)

Pipeline (re-runnable, offline once the raw files exist):
  1. venv/bin/python scripts/bus_fetch_upstream.py     # WBTC + Bus-O-Pedia -> sources/raw/bus/upstream/
  2. venv/bin/python scripts/bus_fetch_osm.py          # Overpass -> sources/raw/bus/osm_*.json
  3. venv/bin/python scripts/bus_build.py              # writes CSVs + unresolved_stops.json
  4. venv/bin/python scripts/bus_geocode_nominatim.py  # 1 req/s, cached, for unresolved stops
  5. venv/bin/python scripts/bus_build.py              # again, now using the Nominatim cache

Coordinate tiers, best first. Neither upstream list publishes coordinates; all come from OSM data:
  1. osm_route      a named platform in the OSM relation carrying the same route number
  2. osm_stop       a named OSM bus stop / platform, route-checked
  3. osm_place      an OSM place or railway station node of the same name, route-checked
  4. nominatim      Nominatim search bounded to the region, route-checked
Route-checked = close to the nearest already-located neighbours on at least half of the routes that
can test it, and within 1 km of the OSM route line where OSM has the same route number. Stops no
neighbour can test yet are seeded only when their exact name matches a single OSM feature; every
coordinate is then re-checked leave-one-out and rejected if off-route. No coordinate is ever
invented: a stop that fails every tier stays blank.

Also adds OSM-only bus routes (refs neither list carries) with >= 3 named stops.
Stdlib only.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict

from bus_common import (NOMINATIM_CACHE, OSM_ROUTES, OSM_STOPS, OUT, RAW, UNRESOLVED,
                        ascii_fold, haversine_km, in_wide_bbox, load_upstream, loose_key, norm_key, slug)

OSM_PLACES = RAW / "osm_places.json"
OSM_LANDMARKS = RAW / "osm_landmarks.json"
REPORT = RAW / "build_report.txt"
DONT_MERGE = {frozenset({"Batanagar", "Bhattanagar"})}

report = []


def log(msg):
    report.append(msg)


# ----------------------------------------------------------------- name cleaning
def clean_name(raw, known_keys):
    """Return (clean_name or None, note)."""
    s = raw.replace("\u2013", "-").replace("\u2014", "-").strip()
    note = ""
    m = re.split(r"\.?\s*\(?\s*On Return Goes Via|\(\s*Return via", s, flags=re.I)
    if len(m) > 1:
        s, note = m[0], "return-via text stripped from source name"
    s = re.sub(r"^[-\s]+", "", s)
    s = s.strip(" .")
    if s.count("(") != s.count(")"):
        s = s.replace("(", "").replace(")", "").strip(" .")
    s = re.sub(r"\s+", " ", s)
    m2 = re.fullmatch(r"(.{4,})\1", s)
    if m2:
        s, note = m2.group(1), "doubled name in source"
    if not s:
        return None, "empty after cleaning"
    if len(s) > 60:
        hits = sum(1 for w in re.split(r"\s+", s) if norm_key(w) in known_keys)
        if hits >= 4:
            return None, "glued list of stops in source, dropped"
    return s, note


# ----------------------------------------------------------------- OSM loading
RAIL_HINTS = ("railway", "train", "subway", "light_rail", "tram", "ferry")


def osm_name_variants(tags):
    out = []
    for k in ("name:en", "name", "alt_name", "old_name", "official_name", "short_name"):
        v = tags.get(k)
        if v:
            out.extend(x.strip() for x in v.split(";") if x.strip())
    # drop pure Bengali script names
    return [v for v in out if re.search(r"[A-Za-z]", v)]


def build_index(points):
    """points: list of (names, lat, lon, ref). Returns {key: [(lat, lon, ref, name)]} for norm + loose keys."""
    idx = defaultdict(list)
    for names, lat, lon, ref in points:
        seen = set()
        for n in names:
            for k in (norm_key(n), "L:" + loose_key(n)):
                if k and k not in seen:
                    seen.add(k)
                    idx[k].append((lat, lon, ref, n))
    return idx


def load_osm():
    stops, places, rels, nodes = [], [], [], {}
    if OSM_STOPS.exists():
        for e in json.loads(OSM_STOPS.read_text(encoding="utf-8"))["elements"]:
            t = e.get("tags", {})
            if any(t.get(h) in ("yes", "station", "platform", "halt", "tram_stop") for h in RAIL_HINTS) \
                    and t.get("highway") != "bus_stop" and t.get("bus") != "yes" and t.get("amenity") != "bus_station":
                continue
            names = osm_name_variants(t)
            if names and "lat" in e:
                stops.append((names, e["lat"], e["lon"], f"node/{e['id']}"))
    if OSM_PLACES.exists():
        for e in json.loads(OSM_PLACES.read_text(encoding="utf-8"))["elements"]:
            names = osm_name_variants(e.get("tags", {}))
            if names and "lat" in e:
                places.append((names, e["lat"], e["lon"], f"node/{e['id']}"))
    landmarks = []
    if OSM_LANDMARKS.exists():
        for e in json.loads(OSM_LANDMARKS.read_text(encoding="utf-8"))["elements"]:
            ll = (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"]) if "center" in e else None
            names = osm_name_variants(e.get("tags", {}))
            if names and ll:
                landmarks.append((names, ll[0], ll[1], f"{e['type']}/{e['id']}"))
    stop_tags = {}
    if OSM_STOPS.exists():
        stop_tags = {e["id"]: e for e in json.loads(OSM_STOPS.read_text(encoding="utf-8"))["elements"]}
    if OSM_ROUTES.exists():
        for e in json.loads(OSM_ROUTES.read_text(encoding="utf-8"))["elements"]:
            if e["type"] == "relation":
                rels.append(e)
            elif e["type"] == "node":
                nodes[e["id"]] = e if e.get("tags") or e["id"] not in stop_tags else stop_tags[e["id"]]
    return stops, places, landmarks, rels, nodes


def ref_key(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper().replace("SPECIAL", "SPL"))


def rel_platforms(rel, nodes):
    """Ordered named stop nodes of a relation: [(name_variants, lat, lon, ref)]."""
    out, seen = [], set()
    for m in rel.get("members", []):
        role = m.get("role", "")
        if m["type"] != "node" or not (role == "" or role.startswith("platform") or role.startswith("stop")):
            continue
        n = nodes.get(m["ref"])
        if not n or "lat" not in n:
            continue
        t = n.get("tags", {})
        if role == "" and not (t.get("highway") == "bus_stop" or t.get("public_transport") in ("platform", "stop_position")):
            continue
        names = osm_name_variants(n.get("tags", {}))
        if not names:
            continue
        k = norm_key(names[0])
        if k in seen:
            continue
        seen.add(k)
        out.append((names, n["lat"], n["lon"], f"node/{n['id']}"))
    return out


# ----------------------------------------------------------------- geometry check
GEOM = {}  # route_id -> [(lat, lon)] from the OSM relation's ways, when one carries the same number
GEOM_TOL_KM = 1.0
REJECTED = defaultdict(list)  # stop_id -> coordinates already rejected as off-route (never re-picked)


def near_geom(cand, pts):
    return any(abs(la - cand[0]) < 0.012 and abs(lo - cand[1]) < 0.012 and haversine_km(cand, (la, lo)) <= GEOM_TOL_KM
               for la, lo in pts)


def neighbour_tests(sid, cand, route_seqs, stop_routes, coords):
    """Return (n_tests, n_pass). Tests the candidate against the nearest located neighbours on each
    route, and against the OSM route line where the same route number exists in OSM."""
    tests = passes = 0
    for rid in stop_routes[sid]:
        if GEOM.get(rid):
            tests += 1
            passes += near_geom(cand, GEOM[rid])
        seq = route_seqs[rid]
        for i, s in enumerate(seq):
            if s != sid:
                continue
            prev = nxt = None
            for j in range(i - 1, -1, -1):
                if seq[j] in coords and seq[j] != sid:
                    prev = (coords[seq[j]], i - j)
                    break
            for j in range(i + 1, len(seq)):
                if seq[j] in coords and seq[j] != sid:
                    nxt = (coords[seq[j]], j - i)
                    break
            if not prev and not nxt:
                continue
            tests += 1
            ok = True
            for nb in (prev, nxt):
                if nb and haversine_km(cand, nb[0]) > min(3.0 * nb[1] + 1.5, 25.0):
                    ok = False
            if ok and prev and nxt:
                direct = haversine_km(prev[0], nxt[0])
                via = haversine_km(prev[0], cand) + haversine_km(cand, nxt[0])
                if via > direct * 1.6 + 3.0:
                    ok = False
            passes += ok
    return tests, passes


def plausible(sid, cand, route_seqs, stop_routes, coords, need_test=True):
    t, p = neighbour_tests(sid, cand, route_seqs, stop_routes, coords)
    if t == 0:
        return not need_test, t, p
    return p * 2 >= t, t, p


def cluster(cands, radius=1.0):
    """Group candidate points within radius km. Returns list of (lat, lon, refs, names)."""
    groups = []
    for lat, lon, ref, name in cands:
        for g in groups:
            if haversine_km((lat, lon), (g[0], g[1])) <= radius:
                g[2].append(ref)
                g[3].append(name)
                n = len(g[2])
                g[0] = (g[0] * (n - 1) + lat) / n
                g[1] = (g[1] * (n - 1) + lon) / n
                break
        else:
            groups.append([lat, lon, [ref], [name]])
    return [(round(g[0], 5), round(g[1], 5), g[2], g[3]) for g in groups if in_wide_bbox(g[0], g[1])]


def pick(sid, name, cands, route_seqs, stop_routes, coords):
    """Choose the plausible cluster closest to the route. Returns (lat, lon, refs, tests, passes) or None."""
    best = None
    for lat, lon, refs, names in cluster(cands):
        if any(haversine_km((lat, lon), bad) <= 0.5 for bad in REJECTED.get(sid, ())):
            continue
        ok, t, p = plausible(sid, (lat, lon), route_seqs, stop_routes, coords)
        if not ok:
            continue
        # score: mean distance to located neighbours (smaller is better)
        score = 0.0
        for rid in stop_routes[sid]:
            for other in route_seqs[rid]:
                if other in coords and other != sid:
                    score = min(score or 1e9, haversine_km((lat, lon), coords[other]))
        if best is None or score < best[0]:
            best = (score, lat, lon, refs, t, p)
    return None if best is None else best[1:]


# ----------------------------------------------------------------- main
def merge_upstream(up):
    """One record per published route. A government number published by both WBTC and
    Bus-O-Pedia's government page is one bus: keep the list that names more stops and credit both."""
    def rk(no):
        return ref_key(no)
    gov = defaultdict(list)
    for r in up["kbo"]:
        if r["list"] == "government":
            gov[rk(r["no"])].append(r)
    out, used_kbo = [], set()
    for w in up["wbtc"]:
        k = rk(w["no"])
        rec = dict(no=w["no"], stops=w["stops"], mode="bus", operator="WBTC", srcs=["wbtc_routes"],
                   section=w["section"], sta=False, notes=[])
        if w["reversed"]:
            rec["notes"].append("WBTC stoppage list printed from the far end; reordered origin to destination")
        k_list = gov.get(k, [])
        if len(k_list) == 1:
            kb = k_list[0]
            used_kbo.add(id(kb))
            rec["srcs"].append("kolbusopedia")
            rec["notes"].append(f"Kolkata Bus-O-Pedia section: {kb['section']}")
            if len(kb["stops"]) > len(w["stops"]):
                rec["stops"] = kb["stops"]
                rec["notes"].append(f"stop list from Bus-O-Pedia (names more stops); WBTC lists {w['origin']} to {w['destination']}")
            else:
                rec["notes"].append(f"stop list from WBTC; Bus-O-Pedia lists {kb['origin']} to {kb['destination']}")
        elif len(k_list) > 1:
            rec["notes"].append("Bus-O-Pedia lists more than one variant under this number; kept separately")
        out.append(rec)
    for r in up["kbo"]:
        if id(r) in used_kbo:
            continue
        rec = dict(no=r["no"], stops=r["stops"], mode=r["mode"], operator=r["operator"], srcs=["kolbusopedia"],
                   section=r["section"], sta=r["sta"], notes=[f"Kolkata Bus-O-Pedia section: {r['section']}"])
        if r["reversed"]:
            rec["notes"].append("via list printed from the far end; reordered origin to destination")
        out.append(rec)
    return out


def main():
    up = load_upstream()
    OUT.mkdir(parents=True, exist_ok=True)
    fl = up["fetched"]
    log("upstream pages: " + "; ".join(f"{k} {v['url']} fetched {v['fetched_utc']}" for k, v in sorted(fl.items())
                                       if not k.startswith("extra_")))
    log(f"WBTC table rows: {len(up['wbtc'])}; Bus-O-Pedia route lines: {len(up['kbo'])}; "
        f"unparsed catalogue lines: {len(up['skipped'])}")
    for x in up["skipped"]:
        log("  unparsed: " + x)
    src_routes = merge_upstream(up)
    log(f"routes after merging WBTC with Bus-O-Pedia government numbers: {len(src_routes)}")

    all_raw = {s for r in src_routes for s in r["stops"]}
    known_keys = {norm_key(s) for s in all_raw if len(s) <= 40}

    # ---- clean names
    clean_of, name_notes = {}, {}
    for raw in all_raw:
        c, note = clean_name(raw, known_keys)
        clean_of[raw] = c
        if note:
            name_notes[raw] = note

    # ---- stop identity: group spelling variants by norm_key (homonyms are split later by geography)
    usage = Counter(clean_of[s] for r in src_routes for s in r["stops"] if clean_of[s])
    name_srcs = defaultdict(set)
    for r in src_routes:
        for s in r["stops"]:
            if clean_of[s]:
                name_srcs[clean_of[s]].update(r["srcs"])
    by_key = defaultdict(list)
    for n in usage:
        by_key[norm_key(n)].append(n)
    canon = {}
    merges = []
    for key, names in by_key.items():
        groups = []
        for n in sorted(names, key=lambda x: (-usage[x], -len(x), x)):
            for g in groups:
                if not any(frozenset({n, m}) in DONT_MERGE for m in g):
                    g.append(n)
                    break
            else:
                groups.append([n])
        for g in groups:
            for n in g:
                canon[n] = g[0]
            if len(g) > 1:
                merges.append(g)
    log(f"spelling variants merged into one stop: {len(merges)} groups")
    for g in merges:
        log("  merge: " + " | ".join(g))

    stop_name, stop_id_of = {}, {}
    for n in sorted(set(canon.values())):
        sid = "bus_" + slug(n)
        base, k = sid, 2
        while sid in stop_name:
            sid, k = f"{base}_{k}", k + 1
        stop_name[sid] = n
        stop_id_of[n] = sid
    sid_of_clean = {n: stop_id_of[canon[n]] for n in canon}
    stop_srcs = defaultdict(set)
    for n, sid in sid_of_clean.items():
        stop_srcs[sid].update(name_srcs[n])

    coords, method, cnote = {}, {}, defaultdict(list)

    # ---- routes
    routes, route_seqs, route_meta = [], {}, {}
    used_ids = set()
    for r in src_routes:
        no = re.sub(r"\s+", " ", ascii_fold(r["no"])).strip()
        if r["sta"]:
            rid = "bus_sta_" + slug(no)
        else:
            rid = "bus_" + re.sub(r"_+", "_", slug(no).replace("_", "", 1) if re.match(r"^[A-Za-z]+-\d", no) else slug(no))
        base, k = rid, 2
        while rid in used_ids:
            rid, k = f"{base}_{k}", k + 1
        used_ids.add(rid)
        seq, notes = [], list(r["notes"])
        for raw in r["stops"]:
            c = clean_of[raw]
            if c is None:
                notes.append(f"dropped source stop: {name_notes.get(raw, 'unusable')}")
                continue
            if raw in name_notes:
                notes.append(f"stop name cleaned ({name_notes[raw]})")
            sid = sid_of_clean[c]
            if not seq or seq[-1] != sid:
                seq.append(sid)
        route_seqs[rid] = seq
        if re.search(r"\bAC\b|^AC|^VS", no, flags=re.I):
            notes.insert(0, "AC")
        notes.append("direction 1 is direction 0 reversed (source publishes one direction)")
        route_meta[rid] = dict(
            route_id=rid, route_name=no, mode=r["mode"], operator=r["operator"],
            source_id=";".join(r["srcs"]),
            confidence="verified" if "wbtc_routes" in r["srcs"] else "community", notes=notes, no=no,
        )
        routes.append(rid)

    stop_routes = defaultdict(set)
    for rid, seq in route_seqs.items():
        for s in seq:
            stop_routes[s].add(rid)

    # ---- OSM
    osm_stops, osm_places, osm_landmarks, rels, nodes = load_osm()
    log(f"OSM: {len(osm_stops)} named bus stops, {len(osm_places)} named places/stations, "
        f"{len(osm_landmarks)} named landmarks, {len(rels)} bus relations")
    stop_idx = build_index(osm_stops)
    place_idx = build_index(osm_places)
    landmark_idx = build_index(osm_landmarks)

    rel_by_ref = defaultdict(list)
    for rel in rels:
        ref = rel.get("tags", {}).get("ref")
        if ref:
            rel_by_ref[ref_key(ref)].append(rel)
    up_refs = set()
    rel_matched = 0
    for rid in routes:
        rk = ref_key(route_meta[rid]["no"])
        up_refs.add(rk)
        rs = rel_by_ref.get(rk, [])
        if rs:
            rel_matched += 1
            route_meta[rid]["notes"].append("OSM relation " + ",".join(f"r{x['id']}" for x in rs))
    log(f"upstream routes with an OSM relation of the same number: {rel_matched}/{len(routes)}")
    ways = {}
    for e in json.loads(OSM_ROUTES.read_text(encoding="utf-8"))["elements"] if OSM_ROUTES.exists() else []:
        if e["type"] == "way":
            ways[e["id"]] = e.get("nodes", [])
    for rid in routes:
        pts = []
        for rel in rel_by_ref.get(ref_key(route_meta[rid]["no"]), []):
            for m in rel["members"]:
                if m["type"] == "way":
                    for nid in ways.get(m["ref"], []):
                        n = nodes.get(nid)
                        if n and "lat" in n:
                            pts.append((n["lat"], n["lon"]))
        if len(pts) >= 10:
            GEOM[rid] = pts[::2]
    log(f"upstream routes with OSM route geometry for checks: {len(GEOM)}")

    # Tier 1: osm_route, a named platform on the OSM relation carrying the same route number
    for rid in routes:
        rs = rel_by_ref.get(ref_key(route_meta[rid]["no"]), [])
        if not rs:
            continue
        idx = build_index([p for rel in rs for p in rel_platforms(rel, nodes)])
        for sid in route_seqs[rid]:
            n = stop_name[sid]
            cands = idx.get(norm_key(n)) or idx.get("L:" + loose_key(n)) or []
            cl = cluster(cands)
            if len(cl) != 1 or sid in coords:
                continue
            lat, lon, refs, _ = cl[0]
            coords[sid] = (lat, lon)
            method[sid] = "osm_route"
            cnote[sid].append(f"OSM {refs[0]} on route relation")
    log(f"stops placed from same-number OSM route relations: {len(coords)}")

    # Tiers 2-4 (route-checked), iterated so new anchors unlock neighbours
    nomi_raw = json.loads(NOMINATIM_CACHE.read_text(encoding="utf-8")) if NOMINATIM_CACHE.exists() else {}
    nomi = defaultdict(list)
    for q, res in nomi_raw.items():
        for x in res or []:
            if x.get("addresstype") in ("state", "state_district", "county", "country"):
                continue
            nomi[norm_key(q.split("|")[0])].append((float(x["lat"]), float(x["lon"]),
                                                    f"{x.get('osm_type', '')}/{x.get('osm_id', '')}",
                                                    x.get("display_name", "")[:60]))

    spellings = defaultdict(list)  # stop_id -> canonical name first, then the other published spellings
    for n, c in sorted(canon.items()):
        sid0 = stop_id_of[c]
        if n != c:
            spellings[sid0].append(n)

    def names_of(sid):
        return [stop_name[sid]] + (spellings.get(sid) or spellings.get(re.sub(r"_\d+x*$", "", sid), []))

    def by_names(idx, loose=True):
        def get(sid):
            for n in names_of(sid):
                c = idx.get(norm_key(n)) or (idx.get("L:" + loose_key(n)) if loose else None)
                if c:
                    return c
            return []
        return get

    tiers = [
        ("osm_stop", by_names(stop_idx)),
        ("osm_place", by_names(place_idx)),
        ("osm_landmark", by_names(landmark_idx)),
        ("nominatim", lambda sid: next((nomi[norm_key(n)] for n in names_of(sid) if nomi.get(norm_key(n))), [])),
    ]

    def run_tiers():
        for tname, getc in tiers:
            for _round in range(6):
                added = 0
                for sid in list(stop_name):
                    if sid in coords or sid not in stop_routes:
                        continue
                    cands = getc(sid)
                    if not cands:
                        continue
                    got = pick(sid, stop_name[sid], cands, route_seqs, stop_routes, coords)
                    if got:
                        lat, lon, refs, t, p = got
                        coords[sid] = (lat, lon)
                        method[sid] = tname
                        cnote[sid].append(f"{tname} {refs[0]} (fits {p}/{t} routes)")
                        added += 1
                if not added:
                    break

    run_tiers()

    # Seeds: a stop no located neighbour can test yet, whose exact name matches exactly one OSM
    # bus stop or place cluster in the region. Accepted provisionally, then re-checked below.
    seeded = 0
    for sid in sorted(stop_name):
        if sid in coords or sid not in stop_routes or len(norm_key(stop_name[sid])) < 5:
            continue
        for tname, idx in (("osm_stop", stop_idx), ("osm_place", place_idx)):
            cl = cluster(idx.get(norm_key(stop_name[sid]), []))
            if len(cl) == 1:
                lat, lon, refs, _ = cl[0]
                coords[sid] = (lat, lon)
                method[sid] = tname
                cnote[sid].append(f"{tname} {refs[0]} (only OSM feature with this name)")
                seeded += 1
                break
            if len(cl) > 1:
                break
    log(f"provisional seeds from unique OSM name matches: {seeded}")

    # Re-check every coordinate leave-one-out against its located neighbours and OSM route lines.
    # The single worst offender goes first, then everything is re-tested, so one bad point cannot
    # drag correct neighbours out with it.
    rejected = []

    def recheck():
        while True:
            bad = []
            for sid in list(coords):
                ll = coords.pop(sid)
                t, p = neighbour_tests(sid, ll, route_seqs, stop_routes, coords)
                coords[sid] = ll
                if t >= 2 and p * 2 < t:
                    bad.append(((t - p) / t, t, sid, p))
            if not bad:
                return
            for ratio, t, sid, p in [max(bad)]:  # only the single worst per pass, then re-test
                ll = coords.pop(sid)
                REJECTED[sid].append(ll)
                rejected.append((sid, method[sid], t, p))
                cnote[sid].append(f"{method[sid]} coordinate {ll[0]},{ll[1]} rejected: off-route on {t - p}/{t} routes")
                del method[sid]

    recheck()
    for _ in range(3):
        n_before = len(coords)
        run_tiers()
        recheck()
        if len(coords) == n_before:
            break
    for sid in coords:
        if "only OSM feature" in " ".join(cnote[sid]) and method[sid] in ("osm_stop", "osm_place"):
            t, _p = neighbour_tests(sid, coords[sid], route_seqs, stop_routes,
                                    {k: v for k, v in coords.items() if k != sid})
            if t < 2:
                cnote[sid].append("not route-checked (too few located neighbours)")
    log(f"coordinates rejected by route-geometry check: {len(rejected)}")
    for sid, m, t, p in rejected:
        log(f"  rejected {m}: {stop_name[sid]} ({t - p}/{t} routes off)")

    # ---- homonyms: one name used for places far apart on different routes -> separate stops
    def context(rid, sid):
        """Tight location context: midpoint of the located immediate neighbours (they must agree)."""
        seq = route_seqs[rid]
        for i, s2 in enumerate(seq):
            if s2 != sid:
                continue
            prev = coords.get(seq[i - 1]) if i > 0 else None
            nxt = coords.get(seq[i + 1]) if i + 1 < len(seq) else None
            if prev and nxt and haversine_km(prev, nxt) <= 6.0:
                return ((prev[0] + nxt[0]) / 2, (prev[1] + nxt[1]) / 2)
            if (i == 0 and nxt) or (i == len(seq) - 1 and prev):
                return nxt or prev
        return None

    n_split = 0
    for sid in sorted(stop_name):
        rids = sorted(stop_routes.get(sid, []))
        if len(rids) < 2:
            continue
        ctx = {r: context(r, sid) for r in rids}
        groups = []  # [ [centroid, [rids]] ]
        for r in rids:
            c = ctx[r]
            if c is None:
                continue
            for g in groups:
                if haversine_km(c, g[0]) <= 15.0:
                    g[1].append(r)
                    break
            else:
                groups.append([c, [r]])
        if len(groups) < 2:
            continue
        # the group nearest the current coordinate keeps the id (else the biggest group)
        if sid in coords:
            groups.sort(key=lambda g: haversine_km(g[0], coords[sid]))
        else:
            groups.sort(key=lambda g: -len(g[1]))
        for k, (c, grp) in enumerate(groups[1:], 2):
            new = f"{sid}_{k}"
            while new in stop_name:
                new += "x"
            stop_name[new] = stop_name[sid]
            far = round(haversine_km(c, groups[0][0]), 1)
            cnote[new].append(f"split from {sid}: same name used about {far} km away on other routes")
            for r in grp:
                route_seqs[r] = [new if x == sid else x for x in route_seqs[r]]
                stop_routes[sid].discard(r)
                stop_routes[new].add(r)
            n_split += 1
        cnote[sid].append("name also used for a different place on other routes (see split ids)")
        # re-test the kept coordinate now that the far routes are gone
        if sid in coords:
            ll = coords.pop(sid)
            ok, t, p = plausible(sid, ll, route_seqs, stop_routes, coords, need_test=False)
            if ok:
                coords[sid] = ll
            else:
                cnote[sid].append(f"{method.pop(sid)} coordinate dropped after split")
    log(f"homonym stops split into separate ids: {n_split}")
    run_tiers()
    recheck()
    run_tiers()

    # ---- OSM-only routes
    osm_only = 0
    osm_new_stops = 0
    for rk, rs in sorted(rel_by_ref.items()):
        if rk in up_refs or not rk:
            continue
        rs = sorted(rs, key=lambda x: x["id"])
        plat0 = rel_platforms(rs[0], nodes)
        if len(plat0) < 3:
            continue
        tags = rs[0]["tags"]
        ref = tags["ref"].strip()
        rid = "bus_" + slug(ref).replace("_", "", 1) if re.match(r"^[A-Za-z]+-\d", ref) else "bus_" + slug(ref)
        if rid in used_ids:
            rid = rid + "_osm"
        used_ids.add(rid)
        seqs = {}
        for d, rel in enumerate(rs[:2]):
            seq = []
            for names, lat, lon, nref in rel_platforms(rel, nodes):
                key = norm_key(names[0])
                sid = None
                for cand_sid in (stop_id_of.get(n) for n in [names[0]]):
                    sid = cand_sid
                if sid is None:
                    for s2, nm in stop_name.items():
                        if norm_key(nm) == key and (s2 not in coords or haversine_km(coords[s2], (lat, lon)) <= 1.0):
                            sid = s2
                            break
                if sid is None or (sid in coords and haversine_km(coords[sid], (lat, lon)) > 1.0):
                    base = "bus_" + slug(names[0])
                    sid, k = base, 2
                    while sid in stop_name and not (sid in coords and haversine_km(coords[sid], (lat, lon)) <= 1.0):
                        sid, k = f"{base}_{k}", k + 1
                    if sid not in stop_name:
                        stop_name[sid] = names[0]
                        stop_id_of.setdefault(names[0], sid)
                        coords[sid] = (round(lat, 5), round(lon, 5))
                        method[sid] = "osm_route"
                        cnote[sid].append(f"OSM {nref}, stop added from OSM-only route")
                        osm_new_stops += 1
                elif sid not in coords:
                    coords[sid] = (round(lat, 5), round(lon, 5))
                    method[sid] = "osm_route"
                    cnote[sid].append(f"OSM {nref} on route relation")
                if not seq or seq[-1] != sid:
                    seq.append(sid)
            seqs[d] = seq
        route_seqs[rid] = seqs[0]
        if 1 in seqs and len(seqs[1]) >= 3:
            route_seqs[rid + "#1"] = seqs[1]
        notes = [f"OSM relation(s) {','.join('r' + str(x['id']) for x in rs)}; not in the WBTC or Bus-O-Pedia lists"]
        if tags.get("air_conditioning") == "yes":
            notes.insert(0, "AC")
        if 1 not in seqs or len(seqs.get(1, [])) < 3:
            notes.append("direction 1 is direction 0 reversed")
        if any(not m.get("role") for x in rs for m in x["members"] if m["type"] == "node"):
            pass
        op = tags.get("operator", "")
        route_meta[rid] = dict(
            route_id=rid, route_name=ref, mode="bus",
            operator="WBTC" if re.search(r"WBTC|WBSTC|CSTC|Transport Corporation", op) else ("" if not op else op),
            source_id="osm", confidence="community", notes=notes, no=ref,
            origin=tags.get("from", ""), destination=tags.get("to", ""),
        )
        if not op:
            route_meta[rid]["notes"].append("operator unknown")
        routes.append(rid)
        osm_only += 1
    log(f"OSM-only routes added: {osm_only}, new stops from them: {osm_new_stops}")

    # ---- write
    used_stops = set()
    for rid in routes:
        used_stops.update(route_seqs[rid])
        used_stops.update(route_seqs.get(rid + "#1", []))

    with open(OUT / "stops.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["stop_id", "stop_name", "lat", "lon", "source_id", "notes"])
        for sid in sorted(used_stops):
            ll = coords.get(sid)
            m = method.get(sid)
            csrc = {None: [], "nominatim": ["nominatim"]}.get(m, ["osm"])
            if "added from OSM-only" in " ".join(cnote[sid]):
                src = "osm"
            else:
                src = ";".join(sorted(stop_srcs.get(sid) or stop_srcs.get(re.sub(r"_\d+x*$", "", sid)) or []) + csrc)
            notes = ([f"coord: {m}"] if m else ["no coordinate found"]) + cnote[sid]
            aliases = sorted(n for n, c in canon.items() if stop_id_of.get(c) == sid and n != stop_name[sid])
            if aliases:
                notes.append("also spelled: " + ", ".join(aliases))
            w.writerow([sid, stop_name[sid], f"{ll[0]:.5f}" if ll else "", f"{ll[1]:.5f}" if ll else "", src, "; ".join(notes)])

    with open(OUT / "routes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["route_id", "route_name", "mode", "operator", "origin", "destination", "headway_min_peak",
                    "headway_min_offpeak", "first_service", "last_service", "fare_min_inr", "fare_max_inr",
                    "source_id", "confidence", "notes"])
        for rid in routes:
            m = route_meta[rid]
            seq = route_seqs[rid]
            origin = m.get("origin") or stop_name[seq[0]]
            dest = m.get("destination") or stop_name[seq[-1]]
            w.writerow([rid, m["route_name"], m["mode"], m["operator"], origin, dest, "", "", "", "", "", "",
                        m["source_id"], m["confidence"], "; ".join(dict.fromkeys(m["notes"]))])

    with open(OUT / "route_stops.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["route_id", "direction", "seq", "stop_id", "travel_min_from_start"])
        for rid in routes:
            d0 = route_seqs[rid]
            d1 = route_seqs.get(rid + "#1") or list(reversed(d0))
            for d, seq in ((0, d0), (1, d1)):
                for i, sid in enumerate(seq, 1):
                    w.writerow([rid, d, i, sid, ""])

    # unresolved list for the Nominatim step
    unresolved = []
    for sid in sorted(used_stops):
        if sid in coords:
            continue
        unresolved.append({"stop_id": sid, "name": stop_name[sid], "routes": sorted(stop_routes.get(sid, []))[:5]})
    UNRESOLVED.write_text(json.dumps(unresolved, ensure_ascii=False, indent=0), encoding="utf-8")

    # ---- summary
    mc = Counter(method.get(s, "none") for s in used_stops)
    log(f"stops written: {len(used_stops)}; with coords: {sum(1 for s in used_stops if s in coords)}; by method: {dict(mc)}")
    rc = Counter((route_meta[r]["mode"], route_meta[r]["operator"] or "unknown") for r in routes)
    log(f"routes written: {len(routes)}; by mode/operator: {dict(rc)}")
    full = sum(1 for r in routes if len(route_seqs[r]) >= 3)
    log(f"routes with >=3 ordered stops: {full}; endpoints only: {len(routes) - full}")
    loc_full = sum(1 for r in routes if all(s in coords for s in route_seqs[r]))
    loc_ends = sum(1 for r in routes if route_seqs[r][0] in coords and route_seqs[r][-1] in coords)
    log(f"routes with every stop located: {loc_full}; with both endpoints located: {loc_ends}")
    log(f"unresolved stops listed for Nominatim: {len(unresolved)}")
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    for line in report:
        if not line.startswith("  "):
            print(line)


if __name__ == "__main__":
    sys.exit(main())
