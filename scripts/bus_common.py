"""Shared helpers for the bus build: paths, name normalisation, distance, upstream route-list parsers.

Imported by bus_build.py and bus_geocode_nominatim.py. Stdlib only.
"""
import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "sources" / "raw" / "bus"
OUT = ROOT / "data" / "bus"
OSM_ROUTES = RAW / "osm_bus_routes.json"
OSM_STOPS = RAW / "osm_bus_stops.json"
NOMINATIM_CACHE = RAW / "nominatim_cache.json"
UNRESOLVED = RAW / "unresolved_stops.json"
UPSTREAM_DIR = RAW / "upstream"

UA = "calcutta-transport/0.2 (open Kolkata transit dataset build; low volume, cached)"

# Wide box: Kolkata metro plus the STA suburban termini (Amta, Bagnan, Hasnabad, Taki ...)
WIDE_BBOX = (21.5, 87.7, 23.4, 89.1)  # S, W, N, E


def ascii_fold(s):
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def slug(s):
    s = ascii_fold(s).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


# Bengali-English transliteration variants, applied to a lowercase, alnum-only string.
_PHON = [
    ("bajar", "bazar"), ("bazaar", "bazar"),
    ("gunge", "gunj"), ("ganj", "gunj"), ("gange", "gunj"), ("gong", "gunj"),
    ("chh", "ch"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("kh", "k"),
    ("gh", "g"), ("ph", "f"), ("jh", "j"),
    ("w", "b"), ("v", "b"), ("y", "i"), ("ee", "i"), ("oo", "u"), ("ou", "u"),
    ("calcutta", "kolkata"),
]
# Generic suffix words dropped for a looser second-tier key.
_SUFFIX = r"(more|mor|crossing|xing|busstand|busstop|bustermin(al|us)|stand|stop|station|stn|halt|ps|chowmatha|chowrasta|chowrangi|chourasta)$"


def norm_key(name):
    """Strict-ish key: case, spacing, punctuation and common spelling variants."""
    s = ascii_fold(name).lower()
    s = s.replace("&", "and")
    s = re.sub(r"\brd\b", "road", s)
    s = re.sub(r"\bst\b", "street", s)
    s = re.sub(r"\bave?\b", "avenue", s)
    s = re.sub(r"\bno\b\.?", "no", s)
    s = re.sub(r"\bstn\b\.?", "station", s)
    s = re.sub(r"\b(xing|x-ing)\b\.?", "crossing", s)
    s = re.sub(r"\b(sq|squre)\b\.?", "square", s)
    s = re.sub(r"\bhosp\b\.?", "hospital", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    for a, b in _PHON:
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)  # collapse doubled letters
    return s


def loose_key(name):
    s = norm_key(re.sub(r"\(.*?\)", "", name))
    prev = None
    while prev != s:
        prev = s
        s = re.sub(_SUFFIX, "", s)
    s = re.sub(r"(road|street|avenue)$", "", s)
    return s if len(s) >= 4 else norm_key(name)


def haversine_km(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(d))


def in_wide_bbox(lat, lon):
    s, w, n, e = WIDE_BBOX
    return s <= lat <= n and w <= lon <= e


# ----------------------------------------------------------------- upstream route lists
import html as _html  # noqa: E402

WBTC_PAGE = "wbtc_city_bus_routes.html"
KBO_PAGES = (("kbo_bus_routes.html", "private"), ("kbo_bus_routes_government.html", "government"))


def _strip(h):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", " | ", h)
    h = re.sub(r"(?i)</(p|div|h\d|li|tr)>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = _html.unescape(h).replace("\u200b", "").replace("\u00a0", " ")
    return [re.sub(r"[ \t]+", " ", ln).strip() for ln in h.split("\n")]


def _cell(h):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", h))).strip()


def _fix_stop(t):
    t = ascii_fold(t)
    t = re.sub(r"^\s*(via\b\s*[:\-]*)", "", t, flags=re.I)
    t = t.strip(" .:;,-|\t\u2026")
    t = re.sub(r"\s+", " ", t)
    return t


def _keep_stop(t):
    return len(t) > 2 and not re.fullmatch(r"\d+", t) and not re.fullmatch(r"(more|mor|road|rd|via|etc)", t, flags=re.I) \
        and not re.match(r"^circuitous", t, flags=re.I)


def _orient(origin, via, dest):
    """Upstream lists sometimes print the stoppages from the far end. Reverse when the list
    plainly starts at the destination or ends at the origin. Then drop end repeats."""
    if via:
        ko, kd = loose_key(origin), loose_key(dest)
        k0, k1 = loose_key(via[0]), loose_key(via[-1])
        rev = (k0 == kd and k0 != ko) or (k1 == ko and k1 != kd)
        if rev:
            via = list(reversed(via))
    seq = [origin] + via + [dest]
    out = []
    for s in seq:
        if out and loose_key(out[-1]) == loose_key(s):
            continue
        out.append(s)
    return out, bool(via) and rev


def parse_wbtc(path):
    """WBTC city bus route table: Sl / Route No / Originating / Terminating / Stoppage."""
    src = path.read_text(encoding="utf-8", errors="replace")
    table = src[src.index("<table"):src.index("</table>")]
    routes = []
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table):
        cells = [_cell(c) for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", row)]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        no, frm, to = cells[1], _fix_stop(re.sub(r"\bto$", "", cells[2], flags=re.I)), _fix_stop(cells[3])
        stoppage = cells[4] if len(cells) > 4 else ""
        parts = re.split(r"\s*(?:,|;|/|\||\s-+\s*|-{1,}|\.{2,})\s*", stoppage)
        via = [_fix_stop(x) for x in parts]
        via = [x for x in via if _keep_stop(x)]
        seq, rev = _orient(frm, via, to)
        routes.append({"no": ascii_fold(no).strip(), "origin": frm, "destination": to, "stops": seq,
                       "src": "wbtc_routes", "section": "WBTC city bus routes", "list": "government",
                       "reversed": rev})
    return routes


_KBO_HEAD = re.compile(
    r"^((?:[A-Za-z]{1,3}[- ]?)?\d+[A-Za-z]{0,2}(?:/\d*[A-Za-z]{0,2})*(?:\s*\([^)]*\))?)\s*(?::\s*-?|-\s+|\s)\s*(.+)$")
_KBO_SECTIONS = {
    "Blue-Yellow Buses (Private)": ("bus", "private"),
    "200 Series General Bus Routes": ("bus", "private"),
    "Mini Series Bus Routes": ("minibus", "private"),
    "SD Series General Bus Routes": ("bus", "private"),
    "DN Series General Bus Routes": ("bus", "private"),
    "K and KB Series": ("bus", "private"),
    "M and MM and MN series": ("bus", "private"),
    "E series (Howrah)": ("bus", "private"),
    "STA": ("bus", "private"),
    "Routes operated by WBTC (CSTC Management)": ("bus", "WBTC"),
    "Routes Operated by WBTC( West Bengal Surface Transport Corporation)": ("bus", "WBTC"),
    "Non AC series of Surface": ("bus", "WBTC"),
    'Routes operated by WBTC(CTC) management "C" series': ("bus", "WBTC"),
    "D & E Series": ("bus", "WBTC"),
}


def _split_head(head):
    head = ascii_fold(head).strip()
    for pat in (r"\s*(?:->|=>)\s*", r"\s+to\s+", r"\s+-+\s+", r"\s*-\s*(?=[A-Z])"):
        m = re.split(pat, head, maxsplit=1, flags=re.I if "to" in pat else 0)
        if len(m) == 2 and m[0].strip() and m[1].strip():
            return _fix_stop(m[0]), _fix_stop(m[1])
    return None


def parse_kbo_line(line, numbered=True):
    """One catalogue line -> (no, origin, destination, via list) or None."""
    line = ascii_fold(line).strip()
    no, rest = None, line
    if numbered:
        m = _KBO_HEAD.match(line)
        if not m:
            return None
        no, rest = re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2)
    rest = re.sub(r"^\s*-+\s*", "", rest).strip()
    head, blob = rest, ""
    m = re.search(r"\[(.*)\]?\s*\.?\s*$", rest)
    if m and "[" in rest:
        head, blob = rest[:rest.index("[")], rest[rest.index("[") + 1:].rstrip(" .]")
    else:
        m = re.search(r"\(\s*via\b", rest, flags=re.I)
        if m:
            head, blob = rest[:m.start()], rest[m.end():].rstrip(" .)")
        else:
            m = re.search(r"\s+via\b\s*:?", rest, flags=re.I)
            if m:
                head, blob = rest[:m.start()], rest[m.end():]
    if not blob:
        return None
    ends = _split_head(head)
    if not ends or len(ends[0]) > 45 or len(ends[1]) > 45:
        return None
    blob = re.sub(r"^\s*via\b\s*[:\-]*", "", blob.strip(), flags=re.I)
    via = [_fix_stop(x) for x in re.split(r"\s*(?:,|;|/|\||(?<=[a-z]{3})\.\s+(?=[A-Z]))\s*", blob)]
    via = [x for x in via if _keep_stop(x)]
    return no, ends[0], ends[1], via


def parse_kbo(path, page):
    routes, skipped, section = [], [], None
    for ln in _strip(path.read_text(encoding="utf-8", errors="replace")):
        if not ln:
            continue
        if ln in _KBO_SECTIONS:
            section = ln
            continue
        if section is None or ln.startswith("Kolkata bus-o-pedia") or ln.startswith("\u00a9"):
            if ln.startswith("Kolkata bus-o-pedia") and section:
                section = None
            continue
        got = parse_kbo_line(ln, numbered=(section != "STA"))
        if got is None and section != "STA":
            got = parse_kbo_line(ln, numbered=False)
            if got:
                got = None  # a numbered section line without a number is page furniture
        if got is None:
            skipped.append(ln[:120])
            continue
        no, frm, to, via = got
        seq, rev = _orient(frm, via, to)
        mode, operator = _KBO_SECTIONS[section]
        routes.append({"no": no or f"{frm} - {to}", "origin": frm, "destination": to, "stops": seq,
                       "src": "kolbusopedia", "section": section, "list": page, "mode": mode,
                       "operator": operator, "sta": section == "STA", "reversed": rev})
    return routes, skipped


def load_upstream():
    """Parse the cached upstream pages (see bus_fetch_upstream.py).
    Returns {"wbtc": [...], "kbo": [...], "skipped": [...], "fetched": {...}}."""
    fetched = {}
    log = UPSTREAM_DIR / "fetch_log.json"
    if log.exists():
        fetched = json.loads(log.read_text(encoding="utf-8"))
    wbtc = parse_wbtc(UPSTREAM_DIR / WBTC_PAGE)
    kbo, skipped = [], []
    for fname, page in KBO_PAGES:
        r, s = parse_kbo(UPSTREAM_DIR / fname, page)
        kbo += r
        skipped += [f"{fname}: {x}" for x in s]
    return {"wbtc": wbtc, "kbo": kbo, "skipped": skipped, "fetched": fetched}
