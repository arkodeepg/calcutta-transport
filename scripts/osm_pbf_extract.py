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
