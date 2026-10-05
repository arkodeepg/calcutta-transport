"""Fetch an Overpass QL query and save raw JSON to sources/raw/.

Usage: venv/bin/python scripts/overpass_fetch.py <out_name.json> '<overpass ql>'
Tries several public mirrors; sends a User-Agent (overpass-api.de returns 406 without one).
"""
import sys, json, pathlib, requests

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = {"User-Agent": "calcutta-transport-dataset/0.1 (personal research)"}
ROOT = pathlib.Path(__file__).resolve().parent.parent


def fetch(query):
    last = None
    for m in MIRRORS:
        try:
            r = requests.post(m, data={"data": query}, headers=UA, timeout=180)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json(), m
            last = f"{m} {r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa
            last = f"{m} {e}"
        print("mirror failed:", last, file=sys.stderr)
    raise SystemExit(f"all mirrors failed: {last}")


if __name__ == "__main__":
    out, q = sys.argv[1], sys.argv[2]
    data, mirror = fetch(q)
    p = ROOT / "sources" / "raw" / out
    p.write_text(json.dumps(data, ensure_ascii=False))
    print(f"saved {p} elements={len(data.get('elements', []))} via {mirror}")
