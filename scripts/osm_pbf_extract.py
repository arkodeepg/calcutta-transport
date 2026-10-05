"""Extract transit route relations and stations from a Geofabrik .osm.pbf into Overpass-style JSON.

Fallback for when Overpass is overloaded. Keeps route relations (subway, light_rail, train,
ferry, tram) that have at least one member node inside the Kolkata metro bbox, their member
nodes and member ways (with node coords for way centroids), plus every railway/ferry/tram
station-like node in the bbox.

Usage: venv/bin/python scripts/osm_pbf_extract.py <in.osm.pbf> sources/raw/<out>.json
"""
import sys, json, osmium

S, W, N, E = 22.3, 88.0, 23.1, 88.7
ROUTES = {"subway", "light_rail", "train", "ferry", "tram"}
inbox = lambda lat, lon: S <= lat <= N and W <= lon <= E
STATION_KEYS = [("railway", {"station", "halt", "stop", "tram_stop"}),
                ("amenity", {"ferry_terminal"}), ("public_transport", {"station"})]

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
