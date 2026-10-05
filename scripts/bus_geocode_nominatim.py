"""Geocode unresolved bus stop names with Nominatim, politely, into a cache.

Reads sources/raw/bus/unresolved_stops.json (written by bus_build.py) and appends raw
Nominatim results to sources/raw/bus/nominatim_cache.json, keyed "name|query".
bus_build.py decides which result (if any) to accept, using the route-geometry check.

Policy: max 1 request per second, descriptive User-Agent, bounded viewbox (Kolkata region
incl. STA suburban termini), countrycodes=in, results cached so a rerun only asks new names.

Usage: venv/bin/python scripts/bus_geocode_nominatim.py [--limit N]
Stdlib only. Results (c) OpenStreetMap contributors, ODbL.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

from bus_common import NOMINATIM_CACHE, UA, UNRESOLVED, WIDE_BBOX

URL = "https://nominatim.openstreetmap.org/search"


def queries_for(name):
    base = re.sub(r"\s+", " ", name).strip()
    qs = [base]
    short = re.sub(r"\b(more|mor|crossing|xing|bus stand|bus stop|stand|stoppage)\b\.?", "", base, flags=re.I)
    short = re.sub(r"\s+", " ", short).strip(" ,.-")
    if short and short.lower() != base.lower() and len(short) >= 4:
        qs.append(short)
    return qs


def search(q):
    s, w, n, e = WIDE_BBOX
    params = {
        "q": q + ", West Bengal",
        "format": "jsonv2",
        "limit": 5,
        "countrycodes": "in",
        "viewbox": f"{w},{n},{e},{s}",
        "bounded": 1,
    }
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read().decode("utf-8"))
    keep = ("lat", "lon", "display_name", "class", "type", "addresstype", "osm_type", "osm_id", "importance")
    return [{k: x.get(k) for k in keep} for x in res]


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    todo = json.loads(UNRESOLVED.read_text(encoding="utf-8"))
    cache = json.loads(NOMINATIM_CACHE.read_text(encoding="utf-8")) if NOMINATIM_CACHE.exists() else {}
    asked = 0
    last = 0.0
    for i, item in enumerate(todo):
        name = item["name"]
        for q in queries_for(name):
            key = f"{name}|{q}"
            if key in cache:
                continue
            wait = 1.1 - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.time()
            try:
                cache[key] = search(q)
            except Exception as ex:  # noqa: BLE001
                print(f"  error on {q!r}: {ex}", file=sys.stderr)
                if "429" in str(ex) or "403" in str(ex):
                    time.sleep(60)
                continue
            asked += 1
            if cache[key]:
                break  # full name found something; skip the shortened query
        if asked and asked % 20 == 0:
            NOMINATIM_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"{i + 1}/{len(todo)} names, {asked} requests", flush=True)
        if limit and asked >= limit:
            break
    NOMINATIM_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"done: {asked} requests, cache has {len(cache)} queries")


if __name__ == "__main__":
    main()
