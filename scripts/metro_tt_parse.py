"""Parse Metro Railway Kolkata timetable PDFs (mtp.indianrailways.gov.in) into departure lists.

Words are read with pdfplumber and assigned to header columns by x position, so blank cells
(short-turn trains) do not shift values. Prints, per direction and timepoint column, first and
last time, trip count, and median headway in peak (08:00-11:00, 17:00-20:00) and off-peak
(11:00-16:00) windows, measured at the busiest column of that direction.

Usage: venv/bin/python scripts/metro_tt_parse.py <pdf> [<pdf> ...] [--json out.json]
"""
import sys, re, json, statistics, pdfplumber

TIME = re.compile(r"^\d{1,2}[:.]\d{2}$")
CODE = re.compile(r"^(K[A-Z]{3,4}|HWMM|SVSA|SVSA/CCSC|CCSC)$")


def tmin(s):
    h, m = re.split(r"[:.]", s)
    return int(h) * 60 + int(m)


def parse(pdf):
    cols = None  # list of (code, xcenter)
    rows = []
    with pdfplumber.open(pdf) as p:
        for page in p.pages:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            hdr = [w for w in words if CODE.match(w["text"])]
            if len(hdr) >= 2:
                cols = [(w["text"], (w["x0"] + w["x1"]) / 2) for w in sorted(hdr, key=lambda w: w["x0"])]
            if not cols:
                continue
            lines = {}
            for w in words:
                if TIME.match(w["text"]):
                    lines.setdefault(round(w["top"] / 3), []).append(w)
            for k in sorted(lines):
                row = [None] * len(cols)
                for w in lines[k]:
                    xc = (w["x0"] + w["x1"]) / 2
                    i = min(range(len(cols)), key=lambda i: abs(cols[i][1] - xc))
                    row[i] = tmin(w["text"])
                rows.append(row)
    return cols, rows


def median_gap(times, windows):
    ts = sorted(t for t in times if any(a <= t < b for a, b in windows))
    gaps = [b - a for a, b in zip(ts, ts[1:]) if 0 < b - a < 90 and any(w[0] <= a < w[1] and w[0] <= b <= w[1] for w in windows)]
    return (statistics.median(gaps) if gaps else None), len(ts)


def summarise(pdf):
    cols, rows = parse(pdf)
    n = len(cols)
    # split columns into two directions: header repeats a code -> halves
    halves = [list(range(0, n // 2)), list(range(n // 2, n))]
    out = {"pdf": pdf, "columns": [c for c, _ in cols], "directions": []}
    hhmm = lambda m: None if m is None else f"{m // 60:02d}:{m % 60:02d}"
    for idx in halves:
        percol = {cols[i][0] + f"#{i}": [r[i] for r in rows if r[i] is not None] for i in idx}
        busiest = max(idx, key=lambda i: sum(r[i] is not None for r in rows))
        bt = [r[busiest] for r in rows if r[busiest] is not None]
        pk, npk = median_gap(bt, [(480, 660), (1020, 1200)])
        op, nop = median_gap(bt, [(660, 960)])
        # typical end-to-end times between first and last column of the half, for full trips
        a, b = idx[0], idx[-1]
        full = [r for r in rows if r[a] is not None and r[b] is not None]
        tt = [r[b] - r[a] for r in full]
        out["directions"].append({
            "columns": [cols[i][0] for i in idx],
            "counts": {k: len(v) for k, v in percol.items()},
            "first": {k: hhmm(min(v)) if v else None for k, v in percol.items()},
            "last": {k: hhmm(max(v)) if v else None for k, v in percol.items()},
            "measured_at": cols[busiest][0], "trips_at_measure": len(bt),
            "headway_peak_median": pk, "headway_offpeak_median": op,
            "median_offsets_from_first_col": {cols[i][0]: (statistics.median([r[i] - r[a] for r in full if r[i] is not None]) if full else None) for i in idx},
            "end_to_end_median": statistics.median(tt) if tt else None,
        })
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    jout = None
    if "--json" in args:
        jout = args[args.index("--json") + 1]
        args = args[: args.index("--json")]
    res = [summarise(a) for a in args]
    for r in res:
        print("==", r["pdf"].split("/")[-1], r["columns"])
        for d in r["directions"]:
            print("  ", {k: d[k] for k in ("measured_at", "trips_at_measure", "headway_peak_median", "headway_offpeak_median", "end_to_end_median")})
            print("     first", d["first"], "\n     last", d["last"], "\n     offsets", d["median_offsets_from_first_col"])
    if jout:
        json.dump(res, open(jout, "w"), indent=1)
