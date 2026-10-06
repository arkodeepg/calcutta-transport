"""Extract transit route relations and stations from a Geofabrik .osm.pbf into Overpass-style JSON.

Fallback for when Overpass is overloaded. Keeps route relations (subway, light_rail, train,
ferry, tram) that have at least one member node inside the Kolkata metro bbox, their member
nodes and member ways (with node coords for way centroids), plus every railway/ferry/tram
station-like node in the bbox.

Usage: venv/bin/python scripts/osm_pbf_extract.py <in.osm.pbf> sources/raw/<out>.json

--rail-ways mode keeps full track geometry instead, for the trip planner's metro and rail shapes
(scripts/build_web.py): every railway=rail/subway/light_rail way with at least one node in the bbox
(abandoned or construction track and industrial lines are left out; service track is kept with its
service tag so the shape builder can make it costlier), with all node
refs and those nodes' coords, inside RAIL_BBOX (wider than the metro bbox: suburban lines run to
Bardhaman, Kharagpur, Gede, Namkhana and Digha). Two passes, only the needed node coords are held in memory.

Usage: venv/bin/python scripts/osm_pbf_extract.py --rail-ways <in.osm.pbf> sources/raw/osm_rail_ways.json

--water mode (alias --river) keeps, inside the metro bbox, the waterway=river centreline of the Hooghly
(name or name:en matching Hooghly, Hugli or Bhagirathi), every other named canal or river, and every
walkable highway bridge (foot=no and private left out), for the planner's walking barrier check.

Usage: venv/bin/python scripts/osm_pbf_extract.py --water <in.osm.pbf> sources/raw/osm_water.json

--places mode writes the raw input for the trip planner's place search (scripts/build_web.py builds
docs/data/places.json from it). Inside PLACES_BBOX (the planner region, same as bus_common.WIDE_BBOX) it keeps:
every named node, and every named closed way or multipolygon (at the centre of its outer ring bbox, with its
size), whose tags make it a place a person might travel to (localities, hospitals, schools, colleges, temples,
parks, markets, malls, offices, housing, named buildings, landmarks; PLACE_SKIP drops shops, ATMs and other
noise), plus named streets: highway ways of the classes in STREET_HW merged by name where they share a node,
one point per merged street (the street node nearest the street's bbox centre) with its length in metres.
Rows: [lat, lon, "key=value", {name tags}, {"span" m | "len" m, "religion"}].

Usage: venv/bin/python scripts/osm_pbf_extract.py --places <in.osm.pbf> sources/raw/osm_places_index.json
"""
import sys, json, osmium

S, W, N, E = 22.3, 88.0, 23.1, 88.7
ROUTES = {"subway", "light_rail", "train", "ferry", "tram"}
RAIL_BBOX = (21.5, 87.0, 23.9, 89.2)  # S, W, N, E for --rail-ways
inbox = lambda lat, lon: S <= lat <= N and W <= lon <= E
STATION_KEYS = [("railway", {"station", "halt", "stop", "tram_stop"}),
                ("amenity", {"ferry_terminal"}), ("public_transport", {"station"})]


def rail_ways(src, out):
    """Track geometry for shapes. Writes Overpass-style JSON (ways with full node refs, plus nodes)."""
    kinds = {"rail", "subway", "light_rail"}
    ways = {}
    for o in osmium.FileProcessor(src, osmium.osm.WAY):
        t = o.tags
        if t.get("railway") not in kinds:
            continue
        if t.get("usage") in ("industrial", "military", "test"):
            continue
        ways[o.id] = {"type": "way", "id": o.id,
                      "tags": {k: t.get(k) for k in ("railway", "usage", "service", "name", "tunnel", "bridge", "layer") if t.get(k)},
                      "nodes": [n.ref for n in o.nodes]}
    need = {n for w in ways.values() for n in w["nodes"]}
    S2, W2, N2, E2 = RAIL_BBOX
    nodes = {}
    for o in osmium.FileProcessor(src, osmium.osm.NODE):
        if o.id in need and o.location.valid():
            lat, lon = o.location.lat, o.location.lon
            if S2 <= lat <= N2 and W2 <= lon <= E2:
                nodes[o.id] = {"type": "node", "id": o.id, "lat": round(lat, 6), "lon": round(lon, 6)}
    keep = []
    for w in ways.values():
        refs = [n for n in w["nodes"] if n in nodes]
        if len(refs) >= 2:
            w["nodes"] = refs
            keep.append(w)
    used = {n for w in keep for n in w["nodes"]}
    els = keep + [nodes[n] for n in used]
    json.dump({"generator": f"osm_pbf_extract.py --rail-ways from {src.split('/')[-1]}", "elements": els},
              open(out, "w"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print("rail ways", len(keep), Counter(w["tags"]["railway"] for w in keep), "nodes", len(used))


WALK_HW = {"primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
           "residential", "living_street", "service", "pedestrian", "footway", "path", "steps", "track", "trunk",
           "trunk_link", "cycleway", "road"}


def river(src, out):
    """Water barriers for walking: Hooghly centreline (tag kind=river), named canals and other named rivers in
    the bbox (kind=canal), and walkable highway bridges in the bbox (kind=bridge; foot=no left out)."""
    import re
    pat = re.compile(r"hoo?ghly|hugli|bhagirathi", re.I)
    ways = {}
    for o in osmium.FileProcessor(src, osmium.osm.WAY):
        t = o.tags
        ww, name = t.get("waterway"), t.get("name:en") or t.get("name") or ""
        kind = None
        if ww == "river" and (pat.search(t.get("name", "")) or pat.search(t.get("name:en", ""))):
            kind = "river"
        elif ww in ("canal", "river") and name:
            kind = "canal"
        elif t.get("bridge") and t.get("bridge") != "no" and t.get("highway") in WALK_HW and t.get("foot") != "no" and t.get("access") not in ("no", "private"):
            kind = "bridge"
        if kind:
            ways[o.id] = {"type": "way", "id": o.id, "tags": {"kind": kind, "name": name, "highway": t.get("highway", "")}, "nodes": [n.ref for n in o.nodes]}
    need = {n for w in ways.values() for n in w["nodes"]}
    nodes = {}
    for o in osmium.FileProcessor(src, osmium.osm.NODE):
        if o.id in need and o.location.valid():
            nodes[o.id] = {"type": "node", "id": o.id, "lat": round(o.location.lat, 6), "lon": round(o.location.lon, 6)}
    keep = []
    for w in ways.values():
        w["nodes"] = [n for n in w["nodes"] if n in nodes]
        if len(w["nodes"]) >= 2 and any(inbox(nodes[n]["lat"], nodes[n]["lon"]) for n in w["nodes"]):
            keep.append(w)
    used = {n for w in keep for n in w["nodes"]}
    json.dump({"generator": f"osm_pbf_extract.py --water from {src.split('/')[-1]}", "elements": keep + [nodes[n] for n in used]},
              open(out, "w"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print("water ways", Counter(w["tags"]["kind"] for w in keep), "nodes", len(used))


PLACES_BBOX = (21.5, 87.4, 23.6, 89.1)  # S, W, N, E, the planner region (bus_common.WIDE_BBOX)
NAME_KEYS = ("name", "name:en", "name:bn", "alt_name", "alt_name:en", "old_name", "old_name:en", "official_name",
             "official_name:en", "short_name", "loc_name", "int_name")
KIND_KEYS = ("place", "amenity", "healthcare", "shop", "tourism", "leisure", "historic", "office", "railway",
             "public_transport", "aeroway", "landuse", "building", "man_made", "highway", "bridge", "junction", "natural")
STREET_HW = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
             "pedestrian", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "service"}
PLACE_SKIP = {
    "amenity": {"atm", "bench", "parking", "parking_space", "toilets", "waste_basket", "drinking_water", "vending_machine",
                "fuel", "pharmacy", "bicycle_parking", "telephone", "charging_station", "shelter", "recycling", "post_box",
                "fast_food", "cafe", "ice_cream", "bureau_de_change", "money_transfer", "car_wash", "motorcycle_parking",
                "waste_disposal", "water_point", "letter_box", "parcel_locker", "taxi", "dentist", "doctors"},
    "highway": None,  # every highway node (bus stops, crossings, signals) except junction-like names, see below
    "natural": {"tree", "water", "wood", "scrub", "wetland", "grassland"},
    "man_made": {"tower", "water_tower", "mast", "pipeline", "survey_point", "chimney", "antenna", "storage_tank"},
    "power": None, "barrier": None, "waterway": None, "boundary": None, "route": None, "entrance": None,
}
SHOP_KEEP = {"mall", "department_store", "supermarket", "wholesale"}


def places(src, out):
    """Named places and streets for the planner's place search (see the module docstring)."""
    from math import cos, radians
    s, w, n, e = PLACES_BBOX
    inb = lambda la, lo: s <= la <= n and w <= lo <= e
    rows = []

    def kind(t):
        for k in KIND_KEYS:
            if k in t:
                return k, t[k]
        return "", ""

    def wanted(t):
        k, v = kind(t)
        if not k:
            return None
        if k in PLACE_SKIP:
            skip = PLACE_SKIP[k]
            if skip is None:
                if k == "highway" and (t.get("junction") or v in ("traffic_signals", "crossing", "motorway_junction")) and "name" in t:
                    return "junction=yes"  # named crossings are landmarks (Chowmatha, Five Point Crossing)
                if k == "highway" and v == "bus_stop":
                    return "highway=bus_stop"
                return None
            if v in skip:
                return None
        if k == "shop" and v not in SHOP_KEEP:
            return None
        if k == "building" and v in ("garage", "garages", "shed", "roof", "construction", "toilets"):
            return None
        return f"{k}={v}"

    names = lambda t: {k: t[k] for k in NAME_KEYS if k in t}
    streets = {}  # way id -> (name, node refs, coords)
    fp = osmium.FileProcessor(src).with_locations().with_areas()
    for o in fp:
        if o.is_node():
            if not o.location.valid() or not inb(o.location.lat, o.location.lon):
                continue
            t = dict(o.tags)
            nm = names(t)
            kd = nm and wanted(t)
            if kd:
                ex = {"religion": t["religion"]} if t.get("religion") else {}
                rows.append([round(o.location.lat, 6), round(o.location.lon, 6), kd, nm, ex])
        elif o.is_way():
            t = o.tags
            if t.get("highway") in STREET_HW and ("name" in t or "name:en" in t) and t.get("area") != "yes":
                pts = [(nd.ref, nd.location.lat, nd.location.lon) for nd in o.nodes if nd.location.valid()]
                if len(pts) >= 2 and any(inb(la, lo) for _, la, lo in pts):
                    streets[o.id] = (dict(names(dict(t))), t.get("highway"), pts)
        elif o.is_area():
            t = dict(o.tags)
            nm = names(t)
            if not nm or t.get("highway") or t.get("waterway") or t.get("boundary") or t.get("route"):
                continue
            kd = wanted(t)
            if not kd:
                continue
            lats, lons = [], []
            for ring in o.outer_rings():
                for nd in ring:
                    if nd.location.valid():
                        lats.append(nd.location.lat)
                        lons.append(nd.location.lon)
            if not lats:
                continue
            la, lo = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
            span = max((max(lats) - min(lats)) * 110540, (max(lons) - min(lons)) * 111320 * cos(radians(la)))
            if not inb(la, lo) or span > 8000:
                continue  # bigger than 8 km: the centre is not a place to travel to
            ex = {"span": round(span)}
            if t.get("religion"):
                ex["religion"] = t["religion"]
            rows.append([round(la, 6), round(lo, 6), kd, nm, ex])
    # merge street ways by name where they share a node (union-find), one point per merged street
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent.setdefault(parent[x], parent[x])
            x = parent[x]
        return x
    by_node = {}
    for wid, (nm, hw, pts) in streets.items():
        key = nm.get("name") or nm.get("name:en")
        for ref, _, _ in pts:
            other = by_node.setdefault((key, ref), wid)
            if other != wid:
                parent[find(wid)] = find(other)
    comps = {}
    for wid in streets:
        comps.setdefault(find(wid), []).append(wid)
    hw_rank = {h: i for i, h in enumerate(["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
                                           "residential", "living_street", "pedestrian"])}
    n_st = 0
    for ws in comps.values():
        nm = {}
        L = 0.0
        pts = []
        best_hw = "service"
        for wid in ws:
            nmw, hw, p = streets[wid]
            for k, v in nmw.items():
                nm.setdefault(k, v)
            if hw_rank.get(hw, 99) < hw_rank.get(best_hw, 99):
                best_hw = hw
            for (_, a, b), (_, c, d) in zip(p, p[1:]):
                L += (((a - c) * 110540) ** 2 + ((b - d) * 111320 * cos(radians(a))) ** 2) ** 0.5
            pts += [(a, b) for _, a, b in p]
        if L < 120 or (best_hw == "service" and L < 400):
            continue
        cla = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        clo = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
        la, lo = min(pts, key=lambda p: (p[0] - cla) ** 2 + (p[1] - clo) ** 2)
        if not inb(la, lo):
            continue
        rows.append([round(la, 6), round(lo, 6), f"highway={best_hw}", nm, {"len": round(L)}])
        n_st += 1
    json.dump({"generator": f"osm_pbf_extract.py --places from {src.split('/')[-1]}", "bbox": list(PLACES_BBOX), "rows": rows},
              open(out, "w"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print("places rows", len(rows), "streets", n_st, Counter(r[2].split("=")[0] for r in rows).most_common())


if sys.argv[1] == "--places":
    places(sys.argv[2], sys.argv[3])
    sys.exit(0)

if sys.argv[1] in ("--water", "--river"):
    river(sys.argv[2], sys.argv[3])
    sys.exit(0)

if sys.argv[1] == "--rail-ways":
    rail_ways(sys.argv[2], sys.argv[3])
    sys.exit(0)

src, out = sys.argv[1], sys.argv[2]
rels, need_nodes, need_ways = {}, set(), set()
for o in osmium.FileProcessor(src, osmium.osm.RELATION):
    t = dict(o.tags)
    if t.get("type") in ("route",) and t.get("route") in ROUTES:
        mem = [{"type": {"n": "node", "w": "way", "r": "relation"}[m.type], "ref": m.ref, "role": m.role} for m in o.members]
        rels[o.id] = {"type": "relation", "id": o.id, "tags": t, "members": mem}
        for m in mem:
            (need_nodes if m["type"] == "node" else need_ways if m["type"] == "way" else set()).add(m["ref"])
ways = {}
for o in osmium.FileProcessor(src, osmium.osm.WAY):
    if o.id in need_ways:
        # only keep refs for platform-ish small ways, skip long track ways to save space
        t = dict(o.tags)
        refs = [n.ref for n in o.nodes]
        keep_refs = t.get("railway") == "platform" or t.get("public_transport") in ("platform", "station") or t.get("amenity") == "ferry_terminal" or len(refs) < 40
        ways[o.id] = {"type": "way", "id": o.id, "tags": t, "nodes": refs if keep_refs else refs[:1] + refs[-1:]}
        need_nodes.update(ways[o.id]["nodes"])
nodes = {}
for o in osmium.FileProcessor(src, osmium.osm.NODE):
    if not o.location.valid():
        continue
    lat, lon = o.location.lat, o.location.lon
    t = dict(o.tags)
    station = inbox(lat, lon) and any(t.get(k) in v for k, v in STATION_KEYS)
    if o.id in need_nodes or station:
        nodes[o.id] = {"type": "node", "id": o.id, "lat": lat, "lon": lon, "tags": t}

def touches(r):
    for m in r["members"]:
        if m["type"] == "node" and m["ref"] in nodes and inbox(nodes[m["ref"]]["lat"], nodes[m["ref"]]["lon"]):
            return True
        if m["type"] == "way" and m["ref"] in ways:
            for nid in ways[m["ref"]]["nodes"]:
                if nid in nodes and inbox(nodes[nid]["lat"], nodes[nid]["lon"]):
                    return True
    return False

keep = [r for r in rels.values() if touches(r)]
els = keep + list(ways.values()) + list(nodes.values())
json.dump({"generator": f"osm_pbf_extract.py from {src.split('/')[-1]}", "elements": els}, open(out, "w"), ensure_ascii=False)
from collections import Counter
print("relations kept", len(keep), Counter(r["tags"]["route"] for r in keep), "ways", len(ways), "nodes", len(nodes))
