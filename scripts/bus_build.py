"""Build data/bus/{stops,routes,route_stops}.csv from the published upstream route lists + OpenStreetMap.

Route sources (fetched first-hand, see bus_fetch_upstream.py; parsed by bus_common.load_upstream):
  wbtc_routes   WBTC city bus route table (wbtconline.in)
  kolbusopedia  Kolkata Bus-O-Pedia private and government route catalogue (kolbusopedia.com)
  osm           OpenStreetMap route=bus relations (route numbers not in either list)

Pipeline (re-runnable, offline once the raw files exist):
  1. venv/bin/python scripts/bus_fetch_upstream.py     # WBTC + Bus-O-Pedia -> sources/raw/bus/upstream/
  2. venv/bin/python scripts/bus_fetch_osm.py          # Overpass -> sources/raw/bus/osm_*.json
     venv/bin/python scripts/bus_fetch_osm.py --pbf sources/raw/bus/eastern-zone-latest.osm.pbf
                                                       # Geofabrik extract -> osm_gazetteer.json
     venv/bin/python scripts/bus_fetch_gazetteers.py   # Wikidata (CC0) + GeoNames (CC BY 4.0)
  3. venv/bin/python scripts/bus_build.py              # writes CSVs + unresolved_stops.json
  4. venv/bin/python scripts/bus_geocode_nominatim.py  # 1 req/s, cached, for unresolved stops
  5. venv/bin/python scripts/bus_build.py              # again, now using the Nominatim cache

Coordinate tiers, best first. Neither upstream list publishes coordinates; all come from open data
(OSM, Wikidata CC0, GeoNames CC BY 4.0), never from commercial geocoders:
  1. osm_route      a named platform in the OSM relation carrying the same route number
  2. osm_stop       a named OSM bus stop / platform, route-checked
  3. osm_place      an OSM place or railway station node of the same name, route-checked
  4. osm_landmark   a named OSM landmark (Overpass extract), route-checked
  5. osm_any        any named OSM feature in the Geofabrik gazetteer (all name tags), route-checked
  6. osm_bn         OSM Bengali-script name, transliterated, consonant-skeleton match, route-checked
  7. nominatim      Nominatim search bounded to the region, route-checked
  8. wikidata       Wikidata item coordinate (P625), English/Bengali label or alias, route-checked
  9. geonames       GeoNames India dump, name or alternate name, route-checked
 10. fuzzy          Latin consonant-skeleton match (Ajaynagar / Ajoynagar): against another located
                    spelling of the same stop (stop_variant), then OSM (osm_fuzzy) and Wikidata
                    (wikidata_fuzzy); route-checked
 (+) interpolated   only with --interpolate: see interpolate() for the strict conditions
Every tier also searches the hand-curated aliases in data/bus/stop_aliases.csv (unless --no-aliases).
Route-checked = close to the nearest already-located neighbours on at least half of the routes that
can test it (tolerance scales with the route's own stop spacing), no fold-back, and within 1 km of
the OSM route line where OSM has the same route number. Stops no neighbour can test yet are seeded
only when their exact name matches a single OSM feature; every coordinate is then re-checked
leave-one-out and rejected if off-route. A stop that fits most routes but is far off one route is
split there (homonym). Remaining out-and-back spikes are blanked. No coordinate is invented.

Options: --tiers a,b,c (enable only these tiers; for measuring), --no-aliases, --interpolate.
Also adds OSM-only bus routes (refs neither list carries) with >= 3 named stops.
Stdlib only.
"""
import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict

from bus_common import (NOMINATIM_CACHE, OSM_ROUTES, OSM_STOPS, OUT, RAW, UNRESOLVED,
                        ascii_fold, bn_to_latin, haversine_km, in_wide_bbox, load_upstream, loose_key,
                        norm_key, skeleton_key, slug)

OSM_PLACES = RAW / "osm_places.json"
OSM_LANDMARKS = RAW / "osm_landmarks.json"
OSM_GAZETTEER = RAW / "osm_gazetteer.json"
WIKIDATA = RAW / "wikidata_places.json"
GEONAMES = RAW / "geonames_IN.txt"
ALIASES = OUT / "stop_aliases.csv"
REPORT = RAW / "build_report.txt"
DONT_MERGE = {frozenset({"Batanagar", "Bhattanagar"})}
ALL_TIERS = ("osm_route", "osm_stop", "osm_place", "osm_landmark", "osm_any", "osm_bn", "nominatim",
             "wikidata", "geonames", "fuzzy")
# Evidence strength, used to decide which of two clashing points to drop first (higher = weaker)
WEAKNESS = {"osm_route": 0, "osm_stop": 1, "osm_place": 2, "osm_landmark": 2, "osm_any": 3, "nominatim": 3,
            "wikidata": 3, "osm_bn": 4, "geonames": 4, "osm_fuzzy": 4, "wikidata_fuzzy": 4, "stop_variant": 4,
            "interpolated": 6}

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


def order_along_ways(plats, rel, ways, nodes, max_km=0.3):
    """Reorder relation platforms by their position along the relation's way chain.

    OSM relations sometimes list stop members out of travel order (audit F-10: r2900668, L30B, lists
    Babu Ghat last although its ways start there). When the relation has a way chain and every platform
    lies within max_km of it, platforms are sorted by the index of the nearest chain point; otherwise the
    member order is kept. Returns (platforms, reordered_flag)."""
    pts = []
    for m in rel.get("members", []):
        if m["type"] == "way":
            for nid in ways.get(m["ref"], []):
                n = nodes.get(nid)
                if n and "lat" in n:
                    pts.append((n["lat"], n["lon"]))
    if len(pts) < 10 or len(plats) < 2:
        return plats, False
    idx = []
    for p in plats:
        k = min(range(len(pts)), key=lambda i: (pts[i][0] - p[1]) ** 2 + (pts[i][1] - p[2]) ** 2)
        if haversine_km(pts[k], (p[1], p[2])) > max_km:
            return plats, False
        idx.append(k)
    out = [p for _, _, p in sorted(zip(idx, range(len(plats)), plats))]
    return out, out != plats


def rel_platforms(rel, nodes):
    """Named stop nodes of a relation in member order: [(name_variants, lat, lon, ref)]."""
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


# ----------------------------------------------------------------- open gazetteers (beyond the Overpass extracts)
SKELETON_MIN = 5  # consonant-skeleton matches need at least this many consonants
TOWN_PTS = defaultdict(list)  # norm_key -> OSM place=city/town points (from the gazetteer)
TRUSTED = {}  # stop_id -> point of a unique-name town seed: distance limits waived, shape tests kept


def load_gazetteer():
    """Geofabrik-derived OSM gazetteer (bus_fetch_osm.py --pbf). Returns (latin_points, bengali_points)
    as (names, lat, lon, ref) lists. Bengali-script names are transliterated for the osm_bn tier."""
    latin, beng = [], []
    if not OSM_GAZETTEER.exists():
        return latin, beng
    for lat, lon, ref, _kind, nm in json.loads(OSM_GAZETTEER.read_text(encoding="utf-8"))["elements"]:
        if not in_wide_bbox(lat, lon):
            continue
        names, bn = [], []
        for k, v in nm.items():
            for x in v.split(";"):
                x = x.strip()
                if not x:
                    continue
                if re.search(r"[A-Za-z]", x):
                    names.append(x)
                else:
                    t = bn_to_latin(x)
                    if t:
                        bn.append(t)
        if names:
            latin.append((names, lat, lon, ref))
            if _kind in ("place=city", "place=town"):
                for n in names:
                    TOWN_PTS[norm_key(n)].append((lat, lon))
        if bn:
            beng.append((bn, lat, lon, ref))
    return latin, beng


def load_wikidata():
    pts, bn = [], []
    if not WIKIDATA.exists():
        return pts, bn
    for tile in json.loads(WIKIDATA.read_text(encoding="utf-8")).get("tiles", {}).values():
        for lat, lon, qid, nm in tile:
            if not in_wide_bbox(lat, lon):
                continue
            if round(lat, 2) == lat and round(lon, 2) == lon:
                continue  # coarse coordinate (2 decimals or fewer, often a block or district centre)
            names = [nm["en"]] if nm.get("en") else []
            names += nm.get("alias", [])
            names = [x for x in names if re.search(r"[A-Za-z]", x) and not re.fullmatch(r"Q\d+", x)]
            if names:
                pts.append((names, lat, lon, f"wikidata/{qid}"))
            t = bn_to_latin(nm["bn"]) if nm.get("bn") else None
            if t:
                bn.append(([t], lat, lon, f"wikidata/{qid}"))
    return pts, bn


# GeoNames feature codes kept: populated places, localities, stations, bus stations, markets, temples,
# hospitals, schools, bridges, crossings (classes P, S, L.AREA-like); not admin areas or water.
GN_CLASSES = {"P", "S"}
GN_EXTRA_CODES = {"LCTY", "AREA", "PRK", "RDJCT", "BDG", "CRSRD"}


def load_geonames():
    pts = []
    if not GEONAMES.exists():
        return pts
    with open(GEONAMES, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 9:
                continue
            lat, lon = float(c[4]), float(c[5])
            if not in_wide_bbox(lat, lon) or (round(lat, 2) == lat and round(lon, 2) == lon):
                continue  # outside the region, or a coarse coordinate (2 decimals or fewer)
            if c[6] not in GN_CLASSES and c[7] not in GN_EXTRA_CODES:
                continue
            names = [c[1], c[2]] + [x for x in c[3].split(",") if x]
            names = list(dict.fromkeys(x.strip() for x in names if x.strip() and re.search(r"[A-Za-z]", x)))
            if names:
                pts.append((names, lat, lon, f"geonames/{c[0]}"))
    return pts


def load_aliases():
    """data/bus/stop_aliases.csv: stop_name, alias, evidence, reason. Each alias is an extra name
    to search for that stop in every tier (still route-checked). Keyed by norm_key(stop_name)."""
    out = defaultdict(list)
    if not ALIASES.exists():
        return out
    with open(ALIASES, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("stop_name") and r.get("alias"):
                out[norm_key(r["stop_name"])].append(r["alias"].strip())
    return out


def build_skeleton_index(points):
    idx = defaultdict(list)
    for names, lat, lon, ref in points:
        seen = set()
        for n in names:
            k = skeleton_key(n)
            if len(k) >= SKELETON_MIN and k not in seen:
                seen.add(k)
                idx[k].append((lat, lon, ref, n))
    return idx


# ----------------------------------------------------------------- geometry check
GEOM = {}  # route_id -> [(lat, lon)] from the OSM relation's ways, when one carries the same number
GEOM_TOL_KM = 1.0
REJECTED = defaultdict(list)  # stop_id -> coordinates already rejected as off-route (never re-picked)


def near_geom(cand, pts):
    return any(abs(la - cand[0]) < 0.012 and abs(lo - cand[1]) < 0.012 and haversine_km(cand, (la, lo)) <= GEOM_TOL_KM
               for la, lo in pts)


SCALE_MIN, SCALE_MAX, SCALE_MAX_PAIR, TOL_MAX_KM = 1.0, 6.0, 10.0, 60.0


ROUTE_PAIRS = {}  # route_id -> [(stop_a, stop_b, km per published stop gap)] over consecutive located stops


def update_scales(route_seqs, coords):
    """Cache each route's consecutive located stop pairs (refreshed whenever points change)."""
    ROUTE_PAIRS.clear()
    for rid, seq in route_seqs.items():
        idx = [(i, s) for i, s in enumerate(seq) if s in coords]
        ROUTE_PAIRS[rid] = [(a, b, haversine_km(coords[a], coords[b]) / (j - i))
                            for (i, a), (j, b) in zip(idx, idx[1:]) if j > i]


def route_cap(rid, exclude):
    """Upper bound for the local spacing: twice the route's median km per stop gap (pairs touching
    the point under test ignored), at least 3 km. Stops two wrong points at a route end from
    widening each other's tolerance beyond anything the rest of the route shows."""
    r = sorted(x for a, b, x in ROUTE_PAIRS.get(rid, ()) if exclude not in (a, b))
    if len(r) < 3:
        return SCALE_MAX_PAIR
    return max(3.0, 2.0 * r[len(r) // 2])


def _nearest(seq, i, sid, coords, step):
    j = i + step
    while 0 <= j < len(seq):
        if seq[j] in coords and seq[j] != sid:
            return coords[seq[j]], abs(j - i), j
        j += step
    return None


def _beyond(seq, nb, sid, coords, step, min_km=3.0):
    """The first located stop past neighbour nb (away from the point) that is at least min_km from
    nb, so the heading into nb is measured over a real distance; else the nearest one, if any."""
    first = None
    j = nb[2] + step
    while 0 <= j < len(seq):
        s = seq[j]
        if s in coords and s != sid:
            if first is None:
                first = (coords[s], abs(j - nb[2]), j)
            if haversine_km(coords[s], nb[0]) >= min_km:
                return coords[s], abs(j - nb[2]), j
        j += step
    return first


def _detour_ok(a, mid, b, factor=1.6, slack=3.0):
    direct = haversine_km(a, b)
    return haversine_km(a, mid) + haversine_km(mid, b) <= direct * factor + slack


def _turn_deg(a, b, c):
    """Change of heading at b on the path a -> b -> c, in degrees (0 = straight on, 180 = U-turn)."""
    k = math.cos(math.radians(b[0]))
    v1 = ((b[1] - a[1]) * k, b[0] - a[0])
    v2 = ((c[1] - b[1]) * k, c[0] - b[0])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _no_foldback(nb2, nb, cand):
    """The line must not fold back at the near neighbour: no 1.3x + 2 km detour against the stop
    beyond it, and no U-turn (over 120 degrees) when both legs are longer than 3 km."""
    if not _detour_ok(nb2, nb, cand, 1.3, 2.0):
        return False
    if haversine_km(nb2, nb) > 3.0 and haversine_km(nb, cand) > 3.0 and _turn_deg(nb2, nb, cand) > 120.0:
        return False
    return True


def route_results(sid, cand, route_seqs, stop_routes, coords):
    """Per-route verdicts for a candidate point: [(route_id, ok, strong_fail)].
    Tests on each route that lists the stop: distance to the nearest located neighbour on each side
    within 1.5 + 3 x gap x local stop spacing km (the located pair bracketing the point, 1 to 10 km
    per stop, or with one side only the hop beyond the near neighbour, 1 to 6 km per stop); with
    neighbours on both sides, no detour over 1.6x + 3 km; and no fold-back at either neighbour
    (1.3x + 2 km detour or a U-turn against the located stop beyond it), which stops a run of wrong
    points from vouching for each other. Plus, where OSM has the same route number, within 1 km of
    the OSM route line. strong_fail: the point breaks the line's shape (detour, fold-back) or is
    more than twice the distance tolerance away, as opposed to a near miss on distance alone."""
    out = []
    for rid in stop_routes[sid]:
        if GEOM.get(rid):
            out.append((rid, near_geom(cand, GEOM[rid]), False))
        seq = route_seqs[rid]
        for i, s in enumerate(seq):
            if s != sid:
                continue
            prev = _nearest(seq, i, sid, coords, -1)
            nxt = _nearest(seq, i, sid, coords, 1)
            if not prev and not nxt:
                continue
            # local spacing: the located pair bracketing the point, or on one side only, the hop
            # beyond the near neighbour (a wrong point never sets its own tolerance)
            if prev and nxt:
                sc = min(max(SCALE_MIN, haversine_km(prev[0], nxt[0]) / (prev[1] + nxt[1])), SCALE_MAX_PAIR)
            else:
                nb = prev or nxt
                nb2 = _nearest(seq, nb[2], sid, coords, -1 if prev else 1)
                sc = haversine_km(nb[0], nb2[0]) / abs(nb2[2] - nb[2]) if nb2 else SCALE_MIN
                sc = min(max(SCALE_MIN, sc), SCALE_MAX)
            sc = min(sc, max(SCALE_MIN, route_cap(rid, sid)))
            ok, strong = True, False
            town = sid in TRUSTED and haversine_km(cand, TRUSTED[sid]) <= 0.5
            for nb in (prev, nxt):
                if nb and not town:
                    tol = min(1.5 + 3.0 * nb[1] * sc, TOL_MAX_KM)
                    d = haversine_km(cand, nb[0])
                    if d > tol:
                        ok = False
                        strong = strong or d > 2 * tol
            if prev and nxt and not _detour_ok(prev[0], cand, nxt[0]):
                ok, strong = False, True
            # the line must not fold back at either neighbour (checked against the stop beyond it)
            for nb, step in ((prev, -1), (nxt, 1)):
                if nb:
                    nb2 = _beyond(seq, nb, sid, coords, step)
                    if nb2 and not _no_foldback(nb2[0], nb[0], cand):
                        ok, strong = False, True
            out.append((rid, ok, strong))
    return out


def neighbour_tests(sid, cand, route_seqs, stop_routes, coords):
    """Return (n_tests, n_pass) over route_results."""
    res = route_results(sid, cand, route_seqs, stop_routes, coords)
    return len(res), sum(1 for _r, ok, _d in res if ok)


def verdict(res):
    """Accept on a clear majority of route tests; a tie only when no failing test is a strong fail."""
    t, p = len(res), sum(1 for _r, ok, _st in res if ok)
    strong = any(not ok and st for _r, ok, st in res)
    return p * 2 > t or (p * 2 == t and not strong)


def plausible(sid, cand, route_seqs, stop_routes, coords, need_test=True):
    res = route_results(sid, cand, route_seqs, stop_routes, coords)
    t, p = len(res), sum(1 for _r, ok, _d in res if ok)
    if t == 0:
        return not need_test, t, p
    return verdict(res), t, p


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


def only_weak_tests(sid, route_seqs, stop_routes, coords):
    """True when every route can test the stop only from one side, from 3+ published stops away
    (typically the far terminus of a long route). Such a test cannot tell same-name places apart."""
    for rid in stop_routes[sid]:
        seq = route_seqs[rid]
        for i, s in enumerate(seq):
            if s != sid:
                continue
            pv, nx = _nearest(seq, i, sid, coords, -1), _nearest(seq, i, sid, coords, 1)
            if (pv and nx) or (pv and pv[1] < 3) or (nx and nx[1] < 3):
                return False
    return True


def pick(sid, name, cands, route_seqs, stop_routes, coords):
    """Choose the plausible cluster closest to the route. Returns (lat, lon, refs, tests, passes) or None."""
    best = None
    cl = cluster(cands)
    if len(cl) > 1 and only_weak_tests(sid, route_seqs, stop_routes, coords):
        return None  # e.g. a terminus 3+ stops past the last located stop: several same-name places, no way to tell
    for lat, lon, refs, names in cl:
        if any(haversine_km((lat, lon), bad) <= 0.5 for bad in REJECTED.get(sid, ())):
            continue
        ok, t, p = plausible(sid, (lat, lon), route_seqs, stop_routes, coords)
        if not ok:
            continue
        # score: more routes passed first, then distance to the nearest located route-mate
        score = 1e9
        for rid in stop_routes[sid]:
            for other in route_seqs[rid]:
                if other in coords and other != sid:
                    score = min(score, haversine_km((lat, lon), coords[other]))
        if best is None or (-p, score) < best[0]:
            best = ((-p, score), lat, lon, refs, t, p)
    return None if best is None else best[1:]


# ----------------------------------------------------------------- quality and interpolation
SPIKE_MIN_KM, LONG_HOP_KM = 5.0, 20.0  # as SPIKE_MIN_M / LONG_HOP_M in build_gtfs.py


def quality(routes, route_seqs, coords):
    """Coverage and the build_gtfs.py geometry warnings, measured on direction 0."""
    rows = located = spikes = hops = dropped = 0
    for rid in routes:
        seq = route_seqs[rid]
        kept = [s for s in seq if s in coords]
        rows += len(seq)
        located += len(kept)
        d1 = [s for s in route_seqs.get(rid + "#1", seq) if s in coords]
        if len(kept) < 2 and len(d1) < 2:
            dropped += 1
        for i in range(1, len(kept) - 1):
            a = haversine_km(coords[kept[i - 1]], coords[kept[i]])
            b = haversine_km(coords[kept[i]], coords[kept[i + 1]])
            if min(a, b) > SPIKE_MIN_KM and a + b > 3 * haversine_km(coords[kept[i - 1]], coords[kept[i + 1]]):
                spikes += 1
        hops += sum(1 for i in range(1, len(kept)) if haversine_km(coords[kept[i - 1]], coords[kept[i]]) > LONG_HOP_KM)
    used = {s for r in routes for s in route_seqs[r] + route_seqs.get(r + "#1", [])}
    return {"stops": len(used), "located": sum(1 for s in used if s in coords),
            "route_weighted_pct": round(100.0 * located / max(rows, 1), 1), "spikes": spikes,
            "hops_over_20km": hops, "routes_dropped": dropped}


INTERP_MAX_SPAN_KM, INTERP_MAX_GAP, INTERP_SNAP_KM, INTERP_AGREE_KM = 3.0, 3, 0.5, 0.5


def interpolate(route_seqs, stop_routes, coords, method, cnote, stop_name):
    """Opt-in (--interpolate). Place an unlocated stop on the OSM route line only when all hold on
    every route that lists it and can place it: the route has a same-number OSM relation (GEOM);
    located neighbours on both sides, at most INTERP_MAX_GAP published stops apart in total and at
    most INTERP_MAX_SPAN_KM apart, both within 1 km of that line and neither interpolated itself;
    the point at the published-order fraction between them snaps to the OSM line within
    INTERP_SNAP_KM; all routes agree within INTERP_AGREE_KM; and the point passes the route
    tests on every route that lists the stop. The result is approximate (error up
    to about half the span) and is marked coord_method=interpolated, coord_confidence=low."""
    placed = {}
    for sid in sorted(stop_name):
        if sid in coords or not stop_routes.get(sid):
            continue
        pts, ok = [], True
        for rid in stop_routes[sid]:
            seq = route_seqs[rid]
            for i, s in enumerate(seq):
                if s != sid:
                    continue
                pv, nx = _nearest(seq, i, sid, coords, -1), _nearest(seq, i, sid, coords, 1)
                if not GEOM.get(rid) or not pv or not nx:
                    continue
                if pv[1] + nx[1] > INTERP_MAX_GAP or haversine_km(pv[0], nx[0]) > INTERP_MAX_SPAN_KM:
                    continue
                if method.get(seq[pv[2]]) == "interpolated" or method.get(seq[nx[2]]) == "interpolated":
                    continue
                if not (near_geom(pv[0], GEOM[rid]) and near_geom(nx[0], GEOM[rid])):
                    continue
                f = pv[1] / (pv[1] + nx[1])
                guess = (pv[0][0] + f * (nx[0][0] - pv[0][0]), pv[0][1] + f * (nx[0][1] - pv[0][1]))
                best = min(GEOM[rid], key=lambda q: haversine_km(guess, q))
                if haversine_km(guess, best) > INTERP_SNAP_KM:
                    ok = False
                    continue
                pts.append((best, rid, stop_name[seq[pv[2]]], stop_name[seq[nx[2]]]))
        if not ok or not pts:
            continue
        if any(haversine_km(a[0], b[0]) > INTERP_AGREE_KM for a in pts for b in pts):
            continue
        lat = sum(p[0][0] for p in pts) / len(pts)
        lon = sum(p[0][1] for p in pts) / len(pts)
        ll = (round(lat, 5), round(lon, 5))
        # it must also fit every other route that lists the stop (no homonym gets a borrowed point)
        if not all(r_ok for _r, r_ok, _st in route_results(sid, ll, route_seqs, stop_routes, coords)):
            continue
        placed[sid] = (ll, pts)
    for sid, (ll, pts) in placed.items():
        coords[sid] = ll
        method[sid] = "interpolated"
        b = pts[0]
        cnote[sid].append(f"interpolated: approximate point on the OSM route line of {b[1]} between {b[2]} and "
                          f"{b[3]} (published order); not a surveyed stop position")
    return len(placed)


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


def parse_args():
    ap = argparse.ArgumentParser(description="Build data/bus CSVs from upstream route lists + open gazetteers")
    ap.add_argument("--tiers", default=",".join(ALL_TIERS),
                    help="comma list of coordinate tiers to enable (default all): " + ",".join(ALL_TIERS))
    ap.add_argument("--no-aliases", action="store_true", help="ignore data/bus/stop_aliases.csv")
    ap.add_argument("--interpolate", action="store_true",
                    help="place a few unlocated stops on the OSM route line between located neighbours "
                         "(coord_method=interpolated, coord_confidence=low)")
    a = ap.parse_args()
    a.tiers = {t.strip() for t in a.tiers.split(",") if t.strip()}
    bad = a.tiers - set(ALL_TIERS)
    if bad:
        ap.error(f"unknown tiers: {sorted(bad)}")
    return a


def main():
    args = parse_args()
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
    for rid in (routes if "osm_route" in args.tiers else []):
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

    # Tiers 2-9 (route-checked), iterated so new anchors unlock neighbours
    nomi_raw = json.loads(NOMINATIM_CACHE.read_text(encoding="utf-8")) if NOMINATIM_CACHE.exists() else {}
    nomi = defaultdict(list)
    for q, res in nomi_raw.items():
        for x in res or []:
            if x.get("addresstype") in ("state", "state_district", "county", "country"):
                continue
            if not in_wide_bbox(float(x["lat"]), float(x["lon"])):
                continue
            nomi[norm_key(q.split("|")[0])].append((float(x["lat"]), float(x["lon"]),
                                                    f"{x.get('osm_type', '')}/{x.get('osm_id', '')}",
                                                    x.get("display_name", "")[:60]))
    gaz_latin, gaz_bn = load_gazetteer()
    wd_latin, wd_bn = load_wikidata()
    gn_pts = load_geonames()
    any_idx = build_index(gaz_latin)
    bn_idx = build_skeleton_index(gaz_bn)
    wd_idx, wd_bn_idx = build_index(wd_latin), build_skeleton_index(wd_bn)
    gn_idx = build_index(gn_pts)
    aliases = defaultdict(list) if args.no_aliases else load_aliases()
    log(f"open gazetteers: OSM (Geofabrik) {len(gaz_latin)} named features, {len(gaz_bn)} with Bengali names; "
        f"Wikidata {len(wd_latin)} items ({len(wd_bn)} with Bengali labels); GeoNames {len(gn_pts)} places; "
        f"aliases for {len(aliases)} stop names; tiers enabled: {','.join(t for t in ALL_TIERS if t in args.tiers)}")

    spellings = defaultdict(list)  # stop_id -> canonical name first, then the other published spellings
    for n, c in sorted(canon.items()):
        sid0 = stop_id_of[c]
        if n != c:
            spellings[sid0].append(n)

    def base_names(sid):
        return [stop_name[sid]] + (spellings.get(sid) or spellings.get(re.sub(r"_\d+x*$", "", sid), []))

    def names_of(sid):
        base = base_names(sid)
        extra = [a for n in base for a in aliases.get(norm_key(n), [])]
        for n in base:  # standard abbreviation: "Golabari PS" = Golabari Police Station
            m = re.match(r"^(.*\S)\s+P\.?\s?S\.?$", n)
            if m:
                extra.append(f"{m.group(1)} Police Station")
        return base + [a for a in dict.fromkeys(extra) if a not in base]

    def by_names(idx, loose=True):
        def get(sid):
            for n in names_of(sid):
                c = idx.get(norm_key(n)) or (idx.get("L:" + loose_key(n)) if loose else None)
                if c:
                    return c, n
            return [], None
        return get

    def by_skeleton(idx):
        def get(sid):
            for n in names_of(sid):
                k = skeleton_key(n)
                if len(k) >= SKELETON_MIN and idx.get(k):
                    return idx[k], n
            return [], None
        return get

    def chain(*getters):
        def get(sid):
            for g in getters:
                c, n = g(sid)
                if c:
                    return c, n
            return [], None
        return get

    def nomi_get(sid):
        for n in names_of(sid):
            if nomi.get(norm_key(n)):
                return nomi[norm_key(n)], n
        return [], None

    # Latin consonant-skeleton matching (Ajaynagar / Ajoynagar, Rashbihari / Rashbehari): first against
    # other located stops of this dataset (a spelling variant of the same place), then OSM and Wikidata.
    fuzzy_idx = build_skeleton_index(osm_stops + osm_places + osm_landmarks + gaz_latin + wd_latin)
    variant_src = {}

    own_cache = {"key": None, "idx": {}}
    dir_re = re.compile(r"\b(north|south|east|west|uttar|dakshin|purba|paschim|purbo|poschim)\b", re.I)

    def dirs(n):  # the skeleton drops vowels, so East/South or Purba/Paschim must still agree
        return {w.lower()[:4] for w in dir_re.findall(n)}

    def fuzzy_get(sid):
        key = (len(coords), len(stop_name))
        if own_cache["key"] != key:  # rebuild the index of located stops when the located set changes
            own = defaultdict(list)
            for o, ll in coords.items():
                k = skeleton_key(stop_name[o])
                if len(k) >= SKELETON_MIN:
                    own[k].append((ll[0], ll[1], "stop/" + o, stop_name[o]))
            own_cache.update(key=key, idx=own)
        own = own_cache["idx"]
        for n in names_of(sid):
            k = skeleton_key(n)
            if len(k) < SKELETON_MIN:
                continue
            c = [x for x in own.get(k, []) if x[2] != "stop/" + sid and x[3] != stop_name[sid]
                 and dirs(x[3]) == dirs(n)]
            if c:
                return c, n
            c = [x for x in fuzzy_idx.get(k, []) if dirs(x[3]) == dirs(n)]
            if c:
                return c, n
        return [], None

    tiers = [t for t in [
        ("osm_stop", by_names(stop_idx)),
        ("osm_place", by_names(place_idx)),
        ("osm_landmark", by_names(landmark_idx)),
        ("osm_any", by_names(any_idx)),
        ("osm_bn", by_skeleton(bn_idx)),
        ("nominatim", nomi_get),
        ("wikidata", chain(by_names(wd_idx), by_skeleton(wd_bn_idx))),
        ("geonames", by_names(gn_idx)),
        ("fuzzy", fuzzy_get),
    ] if t[0] in args.tiers]

    def place(sid, tname, got, qname, why=None):
        lat, lon, refs, t, p = got
        coords[sid] = (lat, lon)
        if tname == "fuzzy":  # name the evidence: another located spelling of this stop, OSM or Wikidata
            tname = ("stop_variant" if refs[0].startswith("stop/") else
                     "wikidata_fuzzy" if refs[0].startswith("wikidata/") else "osm_fuzzy")
            if tname == "stop_variant":
                variant_src[sid] = method.get(refs[0][5:], "")
        method[sid] = tname
        via = f" via alias '{qname}'" if qname and qname not in base_names(sid) else ""
        cnote[sid].append(f"{tname} {refs[0]}{via} " + (why or f"(fits {p}/{t} routes)"))

    def run_tiers():
        update_scales(route_seqs, coords)
        for tname, getc in tiers:
            for _round in range(6):
                added = 0
                for sid in list(stop_name):
                    if sid in coords or sid not in stop_routes:
                        continue
                    cands, qname = getc(sid)
                    if not cands:
                        continue
                    got = pick(sid, stop_name[sid], cands, route_seqs, stop_routes, coords)
                    if got:
                        place(sid, tname, got, qname)
                        added += 1
                if not added:
                    break
                update_scales(route_seqs, coords)

    run_tiers()

    # Seeds: a stop no located neighbour can test yet, whose exact name matches exactly one OSM
    # feature cluster in the region (bus stops, places, stations and the full OSM gazetteer
    # together). Accepted provisionally, then re-checked below.
    seeded = 0
    seed_tiers = [(t, i) for t, i in (("osm_stop", stop_idx), ("osm_place", place_idx), ("osm_any", any_idx))
                  if t in args.tiers]
    for sid in sorted(stop_name):
        if sid in coords or sid not in stop_routes or len(norm_key(stop_name[sid])) < 5:
            continue
        allc = [c for _t, idx in seed_tiers for c in idx.get(norm_key(stop_name[sid]), [])]
        cl = cluster(allc)
        if len(cl) != 1 or any(haversine_km(cl[0][:2], bad) <= 0.5 for bad in REJECTED.get(sid, ())):
            continue
        tname = next(t for t, idx in seed_tiers if idx.get(norm_key(stop_name[sid])))
        lat, lon, refs, _ = cl[0]
        coords[sid] = (lat, lon)
        method[sid] = tname
        cnote[sid].append(f"{tname} {refs[0]} (only OSM feature with this name)")
        seeded += 1
    # Town seeds: a stop whose name (or alias) matches exactly one OSM place=city/town in the region,
    # even if villages or suburbs share the name. Long express routes name only towns, with hops of
    # 40 km or more per published stop (Nandakumar to Kanthi), so for these points the distance
    # limits are waived; the detour and fold-back tests still apply and the recheck can still reject.
    n_town = 0
    for sid in sorted(stop_name):
        if sid not in stop_routes:
            continue
        pts = [(a, b, "", n) for n in names_of(sid) for a, b in TOWN_PTS.get(norm_key(n), [])]
        towns = cluster(pts, 2.0)
        if len(towns) != 1:
            continue
        tl = towns[0][:2]
        if sid in coords and haversine_km(coords[sid], tl) > 2.0:
            if method.get(sid) in ("osm_route", "osm_stop"):
                continue
            # a weaker same-name point (village item, fuzzy match) loses to the region's only town
            ll = coords.pop(sid)
            cnote[sid].append(f"{method.pop(sid)} coordinate {ll[0]},{ll[1]} replaced by the only OSM town "
                              f"or city with this name")
        if any(haversine_km(tl, bad) <= 0.5 for bad in REJECTED.get(sid, ())):
            REJECTED[sid] = [b for b in REJECTED[sid] if haversine_km(tl, b) > 2.0]
        if sid not in coords:
            coords[sid] = tl
            method[sid] = "osm_place"
            cnote[sid].append(f"osm_place town {towns[0][3][0]} (only OSM town or city with this name)")
        TRUSTED[sid] = coords[sid]
        cnote[sid].append("unique OSM town name: distance limits waived, detour and fold-back tests kept")
        n_town += 1
    log(f"provisional seeds from unique OSM name matches: {seeded}; unique-town points: {n_town}")

    # Re-check every coordinate leave-one-out against its located neighbours and OSM route lines.
    # The single worst offender goes first (weaker evidence first on a tie), then everything is
    # re-tested, so one bad point cannot drag correct neighbours out with it. A point only one
    # route can test is rejected when it fails that test (except points on a same-number OSM route).
    rejected = []

    def weakness(sid):
        return WEAKNESS.get(method.get(sid), 5) + ("only OSM feature" in " ".join(cnote[sid]))

    def recheck():
        update_scales(route_seqs, coords)
        while True:
            bad = []
            for sid in list(coords):
                ll = coords.pop(sid)
                res = route_results(sid, ll, route_seqs, stop_routes, coords)
                coords[sid] = ll
                t, p = len(res), sum(1 for _r, ok, _d in res if ok)
                if t and not verdict(res) and (t >= 2 or method.get(sid) != "osm_route"):
                    bad.append(((t - p) / t, t - p, weakness(sid), sid, t, p))
            if not bad:
                return
            ratio, _nf, _w, sid, t, p = max(bad)  # only the single worst per pass, then re-test
            if split_one(sid):  # fits some routes, a homonym on others: split instead of rejecting
                update_scales(route_seqs, coords)
                continue
            ll = coords.pop(sid)
            REJECTED[sid].append(ll)
            rejected.append((sid, method[sid], t, p))
            cnote[sid].append(f"{method[sid]} coordinate {ll[0]},{ll[1]} rejected: off-route on {t - p}/{t} routes")
            del method[sid]
            update_scales(route_seqs, coords)

    def split_off(sid, grp, why):
        """Move the routes in grp to a new stop id with the same name (blank, re-geocoded later)."""
        k = 2
        new = f"{sid}_{k}"
        while new in stop_name:
            k += 1
            new = f"{sid}_{k}"
        stop_name[new] = stop_name[sid]
        if sid in spellings:
            spellings[new] = list(spellings[sid])
        cnote[new].append(f"split from {sid}: {why}")
        for r in grp:
            route_seqs[r] = [new if x == sid else x for x in route_seqs[r]]
            stop_routes[sid].discard(r)
            stop_routes[new].add(r)
        stop_srcs[new] = set(stop_srcs.get(sid, set()))
        return new

    def support(sid):
        """Leave-one-out passes of a located stop over all its route tests."""
        ll = coords.pop(sid)
        t, p = neighbour_tests(sid, ll, route_seqs, stop_routes, coords)
        coords[sid] = ll
        return p

    def split_one(sid):
        """A point that fits one or more routes but fails on another route, sitting 15 km or more from a
        well-supported local context there (located neighbours at most 2 stops away on both sides and
        at most 10 km apart, or one such neighbour 15 km or more from the point; each neighbour fitting
        2+ routes itself) is a homonym there: same name, different place. That route gets its own
        stop id, which the tiers then try to place near its own context. Returns True if split."""
        if len(stop_routes.get(sid, ())) < 2:
            return False
        ll = coords.pop(sid)
        res = route_results(sid, ll, route_seqs, stop_routes, coords)
        okr = {r for r, ok, _d in res if ok}
        far = []
        for rid in sorted({r for r, ok, st in res if not ok and st} - okr):
            seq = route_seqs[rid]
            i = seq.index(sid)
            pv, nx = _nearest(seq, i, sid, coords, -1), _nearest(seq, i, sid, coords, 1)
            if pv and nx and pv[1] <= 2 and nx[1] <= 2 and haversine_km(pv[0], nx[0]) <= 10.0 \
                    and min(haversine_km(ll, pv[0]), haversine_km(ll, nx[0])) >= 15.0:
                far.append((rid, seq[pv[2]], seq[nx[2]]))
            else:  # one close, well-placed neighbour 15 km or more away is enough on its own
                close = [nb for nb in (pv, nx) if nb and nb[1] <= 2 and haversine_km(ll, nb[0]) >= 15.0]
                if close:
                    far.append((rid, seq[close[0][2]], seq[close[0][2]]))
        coords[sid] = ll
        far = [rid for rid, a, b in far if support(a) >= 2 and support(b) >= 2]
        if not okr or not far:
            return False
        split_off(sid, far, f"same name, but on {len(far)} route(s) its neighbours place it 15 km or more "
                            f"from the {method[sid]} point that fits {len(okr)} other routes")
        return True

    def split_far_routes():
        update_scales(route_seqs, coords)
        return sum(1 for sid in sorted(coords) if sid in coords and split_one(sid))

    recheck()
    for _ in range(3):
        n_before = len(coords)
        run_tiers()
        recheck()
        if len(coords) == n_before:
            break
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
        for c, grp in groups[1:]:
            far = round(haversine_km(c, groups[0][0]), 1)
            split_off(sid, grp, f"same name used about {far} km away on other routes")
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
    log(f"homonym stops split into separate ids (neighbour context): {n_split}")
    n_far = 0
    for _ in range(4):
        k = split_far_routes()
        n_far += k
        run_tiers()
        recheck()
        if not k:
            break
    log(f"homonym stops split off routes whose own neighbours place them 15 km or more away: {n_far}")
    run_tiers()
    recheck()

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
        reordered = []
        for d, rel in enumerate(rs[:2]):
            seq = []
            plats, moved = order_along_ways(rel_platforms(rel, nodes), rel, ways, nodes)
            if moved:
                reordered.append(f"r{rel['id']}")
            for names, lat, lon, nref in plats:
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
        if reordered:
            notes.append("stop order taken from the relation's way chain, not its member list (" + ",".join(reordered) + ")")
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

    # ---- final guard: out-and-back spikes (same test as build_gtfs.py). A wrong point is worse than
    # a blank: a spike stop that fits two or more other routes is split off this route (homonym),
    # otherwise its coordinate is dropped. Then the tiers get one more chance at the blanks.
    def find_spikes():
        out = []
        for rid in routes:
            for key in (rid, rid + "#1"):
                kept = [s for s in route_seqs.get(key, []) if s in coords]
                for i in range(1, len(kept) - 1):
                    a = haversine_km(coords[kept[i - 1]], coords[kept[i]])
                    b = haversine_km(coords[kept[i]], coords[kept[i + 1]])
                    d = haversine_km(coords[kept[i - 1]], coords[kept[i + 1]])
                    if min(a, b) > SPIKE_MIN_KM and a + b > 3 * d:
                        out.append((rid, kept[i], round(min(a, b), 1)))
        return out

    n_spike_split = n_spike_drop = 0
    for it in range(8):
        sp = find_spikes()
        n_late = split_far_routes() if it else 0
        n_far += n_late
        if not sp and not n_late:
            break
        for rid, sid, off in sp:
            if sid not in coords or rid not in stop_routes.get(sid, ()):
                continue
            ll = coords.pop(sid)
            res = route_results(sid, ll, route_seqs, stop_routes, coords)
            coords[sid] = ll
            okr = {r for r, ok, _d in res if ok} - {rid}
            seq = route_seqs[rid]
            kept = [x for x in seq if x in coords]
            j = kept.index(sid)
            if j == 0 or j == len(kept) - 1:
                continue
            nbs = [kept[j - 1], kept[j + 1]]
            nb_sup = {x: support(x) for x in nbs}
            if len(okr) >= 2 and len(stop_routes[sid]) > 1 and min(nb_sup.values()) >= 2:
                split_off(sid, [rid], f"same name, but on {rid} the {method[sid]} point is an out-and-back "
                                      f"spike {off} km off the line between well-placed neighbours")
                n_spike_split += 1
                continue
            # blame the weakest of the three points (the spike stop on a tie)
            cand = [(len(okr), weakness(sid) * -1, 0, sid)] + [(nb_sup[x], -weakness(x), 1, x) for x in nbs]
            _s, _w, _o, victim = min(cand)
            if method.get(victim) == "osm_route" and victim != sid:
                victim = sid
            vll = coords.pop(victim)
            REJECTED[victim].append(vll)
            cnote[victim].append(f"{method.pop(victim)} coordinate {vll[0]},{vll[1]} dropped: "
                                 + (f"out-and-back spike {off} km off the line on {rid}" if victim == sid else
                                    f"neighbour of an out-and-back spike at {stop_name[sid]} on {rid}, weaker evidence"))
            n_spike_drop += 1
        run_tiers()
        recheck()
    left = find_spikes()
    log(f"out-and-back spikes resolved: {n_spike_split} split off as homonyms, {n_spike_drop} coordinates "
        f"dropped; spikes left: {len(left)}; homonym splits in total (incl. late ones): {n_far}")
    for rid, sid, off in left:
        log(f"  spike left: {rid} {sid} {off} km")

    # ---- optional interpolation along OSM route lines (see interpolate())
    n_interp = interpolate(route_seqs, stop_routes, coords, method, cnote, stop_name) if args.interpolate else 0
    if args.interpolate:
        log(f"stops interpolated on OSM route lines (coord_method=interpolated, low confidence): {n_interp}")

    # ---- confidence per located stop, from its final leave-one-out route tests
    conf = {}
    update_scales(route_seqs, coords)
    for sid in list(coords):
        m = method[sid]
        if m == "interpolated":
            conf[sid] = "low"
            continue
        ll = coords.pop(sid)
        t, p = neighbour_tests(sid, ll, route_seqs, stop_routes, coords)
        coords[sid] = ll
        if m == "osm_route":
            conf[sid] = "high"
        elif p == 0:
            conf[sid] = "low"
            if "not route-checked" not in " ".join(cnote[sid]):
                cnote[sid].append("not route-checked (too few located neighbours)")
        elif p >= 2 and m not in ("geonames", "osm_bn", "osm_fuzzy", "wikidata_fuzzy", "stop_variant"):
            conf[sid] = "high"
        else:
            conf[sid] = "medium"

    # ---- write
    used_stops = set()
    for rid in routes:
        used_stops.update(route_seqs[rid])
        used_stops.update(route_seqs.get(rid + "#1", []))

    with open(OUT / "stops.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["stop_id", "stop_name", "lat", "lon", "source_id", "notes", "coord_method", "coord_confidence"])
        for sid in sorted(used_stops):
            ll = coords.get(sid)
            m = method.get(sid)
            m_src = variant_src.get(sid, "osm") if m == "stop_variant" else m
            csrc = {None: [], "nominatim": ["nominatim"], "wikidata": ["wikidata"], "wikidata_fuzzy": ["wikidata"],
                    "geonames": ["geonames"]}.get(m_src, ["osm"])
            if "added from OSM-only" in " ".join(cnote[sid]):
                src = "osm"
            else:
                src = ";".join(sorted(stop_srcs.get(sid) or stop_srcs.get(re.sub(r"_\d+x*$", "", sid)) or []) + csrc)
            notes = ([f"coord: {m}"] if m else ["no coordinate found"]) + cnote[sid]
            aliases = sorted(n for n, c in canon.items() if stop_id_of.get(c) == sid and n != stop_name[sid])
            if aliases:
                notes.append("also spelled: " + ", ".join(aliases))
            w.writerow([sid, stop_name[sid], f"{ll[0]:.5f}" if ll else "", f"{ll[1]:.5f}" if ll else "", src,
                        "; ".join(notes), m or "", conf.get(sid, "")])

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
    q = quality(routes, route_seqs, coords)
    log("quality (same tests as build_gtfs.py, direction 0): " + ", ".join(f"{k} {v}" for k, v in q.items()))
    log(f"coord_confidence: {dict(Counter(conf[s] for s in used_stops if s in conf))}")
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    for line in report:
        if not line.startswith("  "):
            print(line)


if __name__ == "__main__":
    sys.exit(main())
