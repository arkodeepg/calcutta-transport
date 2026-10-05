"""Harvest Kolkata suburban (EMU/MEMU) trains and their stop lists from erail.in.

Steps (each cached, re-runs only fetch what is missing):
  1. enumerate: getTrains for seed station pairs (both directions) -> local trains found
  2. routes: TRAINROUTE for every local train found -> ordered stops with times, km, lat/lon
Raw responses are appended to sources/raw/rail/erail_cache.jsonl as {"kind","key","fetched","text"}.

Usage: venv/bin/python scripts/erail_suburban.py [--workers 3] [--expand]
  --expand adds a closure round over every consecutive stop pair already seen (getTrains results are
  partial per pair, so the union over many pairs gives better coverage).
Data is community/aggregator grade (erail.in). Personal use; be polite (small worker count).
"""
import sys, json, time, pathlib, threading, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "sources" / "raw" / "rail" / "erail_cache.jsonl"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
      "Referer": "https://erail.in/"}
LOCAL_TYPES = {"EMU", "MEMU", "DEMU"}

# Seed pairs: a terminal and its first neighbour, so every local leaving/entering it shows up,
# plus pairs on lines that have locals not touching the big terminals.
SEEDS = [
    ("SDAH", "BNXR"),   # Sealdah north/main (Ranaghat, Bangaon, Hasnabad, Dankuni via DDJ...)
    ("SDAH", "PQS"),    # Sealdah south (Budge Budge, Diamond Harbour, Namkhana, Canning, Lakshmikantapur)
    ("HWH", "LLH"),     # Howrah ER (main, chord, Tarakeswar, Bandel, Katwa)
    ("HWH", "TPKR"),    # Howrah SER (Kharagpur side, Amta, Santragachi)
    ("SHM", "PDPK"),    # Shalimar SER locals
    ("NH", "HGY"),      # Naihati - Bandel link
    ("BT", "HB"),       # Barasat - Hridaypur (Hasnabad/Bangaon side locals starting at Barasat)
    ("DDJ", "KOAA"),    # Circular/Kolkata terminal locals
    ("BBDB", "PPGT"),   # Circular railway via BBD Bag - Prinsep Ghat
    ("MJT", "BRJ"),     # Majerhat - Brace Bridge (Budge Budge branch / circular)
    ("SHE", "DJW"),     # Sheoraphuli - Diara (Tarakeswar branch shuttles)
    ("SRC", "MRGM"),    # Santragachi locals
    ("DKAE", "BLYG"),   # Dankuni - Bally Ghat
    ("BDC", "HGY"),     # Bandel side
    ("SNR", "GRP"),     # Shyamnagar - Garifa (rarely needed)
    ("BGB", "PJA"),     # Budge Budge - Pujali
    ("PKU", "KIG"),     # Panskura - Kolaghat side
    # second-order seeds catch locals that skip the first neighbour station
    ("SDAH", "DDJ"), ("SDAH", "BLN"), ("SDAH", "BT"), ("SDAH", "BP"), ("SDAH", "NH"), ("SDAH", "SPR"),
    ("HWH", "BLY"), ("HWH", "SRC"), ("HWH", "BDC"), ("HWH", "SHE"), ("HWH", "DKAE"), ("HWH", "ULB"),
    ("BLN", "SPR"), ("BLN", "BRP"), ("DDJ", "BT"), ("DDJ", "DKAE"), ("SRC", "ULB"), ("BDC", "NH"),
    ("SHE", "TAK"), ("BT", "BNJ"), ("BT", "HNB"), ("SPR", "BGB"), ("BRP", "DH"), ("BRP", "LKPR"), ("SPR", "CG"),
]

lock = threading.Lock()


def load_cache():
    c = {}
    if CACHE.exists():
        for line in CACHE.open():
            r = json.loads(line)
            c[(r["kind"], r["key"])] = r["text"]
    return c


def get(url):
    for attempt in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        time.sleep(2 + attempt * 3)
    return None


def fetch(cache, kind, key, url):
    if (kind, key) in cache:
        return cache[(kind, key)]
    txt = get(url)
    time.sleep(0.4)
    if txt is None:
        return None
    with lock:
        cache[(kind, key)] = txt
        with CACHE.open("a") as f:
            f.write(json.dumps({"kind": kind, "key": key, "fetched": datetime.date.today().isoformat(), "text": txt}, ensure_ascii=False) + "\n")
    return txt


def parse_trains(txt):
    out = []
    for rec in txt.split("^")[1:]:
        t = rec.split("~")
        try:
            i = next(k for k, v in enumerate(t) if v.startswith("DATASOURCE_"))
        except StopIteration:
            continue
        typ = next((v for v in t[i:] if v in LOCAL_TYPES or v in ("Passenger", "Exp", "SF", "Mail")), "")
        out.append({"number": t[0], "name": t[1], "from": t[3], "to": t[5], "days": t[13],
                    "id": t[i - 3], "datasource": t[i], "type": typ})
    return out


def parse_route(txt):
    stops = []
    for rec in txt.split("^")[1:]:
        t = rec.split("~")
        if len(t) < 16:
            continue
        stops.append({"seq": int(t[0]), "code": t[1], "name": t[2], "arr": t[3], "dep": t[4], "day": t[5],
                      "km": t[6], "lat": t[14], "lon": t[15]})
    return stops


REGION = (21.7, 23.5, 87.3, 89.0)  # lat_min, lat_max, lon_min, lon_max


def in_region(s):
    try:
        lat, lon = float(s["lat"]), float(s["lon"])
    except (ValueError, KeyError):
        return False
    return REGION[0] <= lat <= REGION[1] and REGION[2] <= lon <= REGION[3]


def is_local(tr):
    return tr["type"] in LOCAL_TYPES or tr["datasource"] == "DATASOURCE_KOLKATA" or "LOCAL" in tr["name"].upper()


def main():
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 3
    cache = load_cache()
    base = "https://erail.in/rail/getTrains.aspx?Station_From={}&Station_To={}&DataSource=0&Language=0&Cache=true"
    trains = {}
    pairs = SEEDS + [(b, a) for a, b in SEEDS]
    if "--expand" in sys.argv:
        # closure round: every consecutive stop pair (inside REGION) seen in already-fetched routes
        idx = ROOT / "sources" / "raw" / "rail" / "erail_trains_index.json"
        seen = set(pairs)
        for tr in json.load(idx.open()):
            st = [s for s in tr.get("stops", []) if in_region(s)]
            for a, b in zip(st, st[1:]):
                k = tuple(sorted((a["code"], b["code"])))
                if k not in seen and (k[1], k[0]) not in seen:
                    seen.add(k); pairs.append(k)
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch, cache, "trains", f"{a}-{b}", base.format(a, b)): (a, b) for a, b in pairs}
        for f in as_completed(futs):
            txt = f.result()
            if not txt:
                print("fail", futs[f]); continue
            n = 0
            for tr in parse_trains(txt):
                if is_local(tr):
                    trains[tr["number"]] = tr; n += 1
            print("pair", futs[f], "locals", n, flush=True)
    print("unique local trains", len(trains), flush=True)
    rurl = "https://erail.in/data.aspx?Action=TRAINROUTE&Password=2012&Data1={}&Data2=0&Cache=true"
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fetch, cache, "route", tr["id"], rurl.format(tr["id"])) for tr in trains.values()]
        for f in as_completed(futs):
            done += 1
            if done % 100 == 0:
                print("routes", done, "/", len(futs), flush=True)
    out = ROOT / "sources" / "raw" / "rail" / "erail_trains_index.json"
    for tr in trains.values():
        tr["stops"] = parse_route(cache.get(("route", tr["id"]), "") or "")
    json.dump(list(trains.values()), out.open("w"), ensure_ascii=False)
    print("wrote", out, "trains", len(trains), "with stops", sum(1 for t in trains.values() if t["stops"]))


if __name__ == "__main__":
    main()
