"""Fetch Kolkata bus data from OpenStreetMap via Overpass.

Writes raw JSON to sources/raw/bus/:
  osm_bus_routes.json  route=bus relations (with members) in the Kolkata metro bbox,
                       their ways and way nodes (geometry), and member nodes (with tags)
  osm_places.json      place=* and railway station/halt nodes (fallback name gazetteer)
  osm_bus_stops.json   highway=bus_stop / public_transport=platform|stop_position nodes
                       in a wider bbox that also covers the STA suburban termini
  osm_landmarks.json   named landmarks in the metro bbox (hospitals, colleges, cinemas, malls,
                       parks, temples, junctions, bridges, housing estates); Kolkata stops are
                       mostly named after these. Points or way/relation centres.

Usage: venv/bin/python scripts/bus_fetch_osm.py [--force] [osm_bus_stops.json ...]
(rail platforms are fetched too and filtered out at build time)
Stdlib only. Data (c) OpenStreetMap contributors, ODbL.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "sources" / "raw" / "bus"
from bus_common import UA  # noqa: E402
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
METRO_BBOX = "22.3,88.0,23.1,88.7"
WIDE_BBOX = "21.5,87.7,23.4,89.1"

QUERIES = {
    "osm_bus_routes.json": f"""[out:json][timeout:180];
relation["route"~"^(bus|minibus|trolleybus)$"]({METRO_BBOX})->.r;
.r out body;
way(r.r)->.w;
.w out skel qt;
node(w.w);
out skel qt;
node(r.r);
out body qt;
""",
    "osm_bus_stops.json": f"""[out:json][timeout:180];
(
  node["highway"="bus_stop"]({WIDE_BBOX});
  node["public_transport"="platform"]({WIDE_BBOX});
  node["public_transport"="stop_position"]["bus"="yes"]({WIDE_BBOX});
  node["amenity"="bus_station"]({WIDE_BBOX});
);
out body qt;
""",
    "osm_places.json": f"""[out:json][timeout:180];
(
  node["place"~"^(city|town|suburb|quarter|neighbourhood|village|hamlet|locality)$"]({WIDE_BBOX});
  node["railway"~"^(station|halt)$"]({WIDE_BBOX});
);
out body qt;
""",
    "osm_landmarks.json": f"""[out:json][timeout:300];
(
  nwr["amenity"~"^(hospital|clinic|college|university|school|cinema|theatre|place_of_worship|police|post_office|bus_station|marketplace|fire_station|townhall|library|courthouse|community_centre)$"]["name"]({METRO_BBOX});
  nwr["shop"="mall"]["name"]({METRO_BBOX});
  nwr["leisure"~"^(park|stadium|sports_centre)$"]["name"]({METRO_BBOX});
  nwr["tourism"~"^(attraction|museum|zoo|theme_park)$"]["name"]({METRO_BBOX});
  nwr["landuse"="residential"]["name"]({METRO_BBOX});
  node["highway"~"^(traffic_signals|crossing|motorway_junction)$"]["name"]({METRO_BBOX});
  node["junction"]["name"]({METRO_BBOX});
  way["bridge"="yes"]["name"]({METRO_BBOX});
  way["man_made"="bridge"]["name"]({METRO_BBOX});
);
out center tags qt;
""",
}


def fetch(query):
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for ep in ENDPOINTS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(ep, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=400) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"  {ep} attempt {attempt + 1} failed: {e}", file=sys.stderr)
                time.sleep(10)
    raise RuntimeError(f"all Overpass endpoints failed: {last}")


def main():
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if a.endswith(".json")]
    RAW.mkdir(parents=True, exist_ok=True)
    for fname, q in QUERIES.items():
        if only and fname not in only:
            continue
        out = RAW / fname
        if out.exists() and not force:
            print(f"skip {fname} (exists, use --force)")
            continue
        print(f"fetching {fname} ...")
        js = fetch(q)
        out.write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
        print(f"  {len(js.get('elements', []))} elements -> {out}")


if __name__ == "__main__":
    main()
