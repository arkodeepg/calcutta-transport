"""Fetch open place-name gazetteers (Wikidata, GeoNames) for the bus stop coordinate tiers.

Writes raw files to sources/raw/bus/:
  wikidata_places.json  every Wikidata item with a coordinate (P625) inside bus_common.WIDE_BBOX:
                        [lat, lon, "Q123", {"en": label, "bn": label, "alias": [..]}].
                        Wikidata is CC0. Queried tile by tile through the public SPARQL endpoint,
                        one request at a time with a pause, cached (a rerun skips done tiles).
  geonames_IN.txt       GeoNames country dump for India (download.geonames.org/export/dump/IN.zip),
                        unzipped. GeoNames is CC BY 4.0: credit "GeoNames (geonames.org)".
bus_build.py filters both to the wide bbox and route-checks every point, exactly like OSM.

Usage: venv/bin/python scripts/bus_fetch_gazetteers.py [--force] [wikidata] [geonames]
Stdlib only.
"""
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile

from bus_common import RAW, UA, WIDE_BBOX

WD_OUT = RAW / "wikidata_places.json"
GN_OUT = RAW / "geonames_IN.txt"
WD_URL = "https://query.wikidata.org/sparql"
GN_URL = "https://download.geonames.org/export/dump/IN.zip"
TILE = 0.25  # degrees

WD_QUERY = """SELECT ?item ?coord ?en ?bn (GROUP_CONCAT(DISTINCT ?al; separator="|") AS ?aliases) WHERE {
  SERVICE wikibase:box {
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%(w)s %(s)s)"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point(%(e)s %(n)s)"^^geo:wktLiteral .
  }
  OPTIONAL { ?item rdfs:label ?en FILTER(LANG(?en) = "en") }
  OPTIONAL { ?item rdfs:label ?bn FILTER(LANG(?bn) = "bn") }
  OPTIONAL { ?item skos:altLabel ?al FILTER(LANG(?al) = "en") }
} GROUP BY ?item ?coord ?en ?bn"""


def get(url, data=None, accept=None, timeout=120):
    h = {"User-Agent": UA}
    if accept:
        h["Accept"] = accept
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_wikidata(force=False):
    cache = {} if force or not WD_OUT.exists() else json.loads(WD_OUT.read_text(encoding="utf-8"))
    tiles = cache.setdefault("tiles", {})
    s0, w0, n0, e0 = WIDE_BBOX
    lat = s0
    todo = []
    while lat < n0:
        lon = w0
        while lon < e0:
            todo.append((round(lat, 3), round(lon, 3)))
            lon += TILE
        lat += TILE
    for i, (s, w) in enumerate(todo):
        key = f"{s},{w}"
        if key in tiles:
            continue
        q = WD_QUERY % {"s": s, "w": w, "n": round(s + TILE, 3), "e": round(w + TILE, 3)}
        rows = None
        for attempt in range(3):
            try:
                raw = get(WD_URL, data=urllib.parse.urlencode({"query": q}).encode(),
                          accept="application/sparql-results+json")
                rows = json.loads(raw.decode("utf-8"))["results"]["bindings"]
                break
            except Exception as ex:  # noqa: BLE001
                print(f"  tile {key} attempt {attempt + 1}: {ex}", file=sys.stderr)
                time.sleep(20 * (attempt + 1))
        if rows is None:
            continue
        out = []
        for b in rows:
            pt = b["coord"]["value"]  # Point(lon lat)
            try:
                lon_, lat_ = map(float, pt[pt.index("(") + 1:pt.index(")")].split())
            except ValueError:
                continue
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            names = {}
            if "en" in b:
                names["en"] = b["en"]["value"]
            if "bn" in b:
                names["bn"] = b["bn"]["value"]
            if b.get("aliases", {}).get("value"):
                names["alias"] = b["aliases"]["value"].split("|")
            if names:
                out.append([round(lat_, 6), round(lon_, 6), qid, names])
        tiles[key] = out
        print(f"wikidata tile {i + 1}/{len(todo)} {key}: {len(out)} items", flush=True)
        WD_OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        time.sleep(2)
    cache["fetched_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    WD_OUT.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"wikidata: {sum(len(v) for v in tiles.values())} items in {len(tiles)} tiles -> {WD_OUT}")


def fetch_geonames(force=False):
    if GN_OUT.exists() and not force:
        print(f"skip geonames (exists, use --force): {GN_OUT}")
        return
    z = zipfile.ZipFile(io.BytesIO(get(GN_URL, timeout=600)))
    GN_OUT.write_bytes(z.read("IN.txt"))
    print(f"geonames: {GN_OUT} ({GN_OUT.stat().st_size} bytes)")


def main():
    force = "--force" in sys.argv
    which = [a for a in sys.argv[1:] if a in ("wikidata", "geonames")] or ["wikidata", "geonames"]
    if "geonames" in which:
        fetch_geonames(force)
    if "wikidata" in which:
        fetch_wikidata(force)


if __name__ == "__main__":
    main()
