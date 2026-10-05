"""Fetch the upstream published bus route lists directly, politely, into a raw cache.

Sources (the route lists that third-party apps compile from; fetched first-hand here):
  wbtc_routes   WBTC official city bus route table   https://wbtconline.in/wbtc-city-bus-routes
  kolbusopedia  Kolkata Bus-O-Pedia route catalogue  https://www.kolbusopedia.com/bus-routes
                                                     https://www.kolbusopedia.com/bus-routes-government

Writes raw HTML to sources/raw/bus/upstream/{name}.html plus fetch_log.json (URL, UTC time,
HTTP status, bytes). Policy: at most 1 request per 2 seconds, descriptive User-Agent with no
personal details, cached (a rerun only fetches missing files unless --force).

Usage: venv/bin/python scripts/bus_fetch_upstream.py [--force] [name ...]
Stdlib only. Parsing lives in bus_common.load_upstream().
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

from bus_common import UA, UPSTREAM_DIR

PAGES = {
    "wbtc_city_bus_routes": "https://wbtconline.in/wbtc-city-bus-routes",
    "kbo_bus_routes": "https://www.kolbusopedia.com/bus-routes",
    "kbo_bus_routes_government": "https://www.kolbusopedia.com/bus-routes-government",
}
DELAY_S = 2.0


def main():
    force = "--force" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    extra = [a for a in only if a.startswith("http")]
    UPSTREAM_DIR.mkdir(parents=True, exist_ok=True)
    log_path = UPSTREAM_DIR / "fetch_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}
    pages = dict(PAGES)
    for u in extra:  # ad hoc extra page: name derived from the path
        pages["extra_" + u.rstrip("/").rsplit("/", 1)[-1].replace("-", "_").removesuffix(".pdf")] = u
    last = 0.0
    for name, url in pages.items():
        if only and name not in only and url not in extra:
            continue
        out = UPSTREAM_DIR / (f"{name}.pdf" if url.lower().endswith(".pdf") else f"{name}.html")
        if out.exists() and not force:
            print(f"skip {name} (cached)")
            continue
        wait = DELAY_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                status = r.status
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: {e}", file=sys.stderr)
            continue
        out.write_bytes(body)
        log[name] = {"url": url, "fetched_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "status": status, "bytes": len(body)}
        print(f"{name}: {status}, {len(body)} bytes -> {out}")
    log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
