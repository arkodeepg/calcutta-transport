/* Kolkata trip planner, proof of concept.
 * Loads data/network.json (built by scripts/build_web.py) and routes entirely in the browser.
 * Routing: round-based search (RAPTOR style) over route patterns with frequency-based boarding cost,
 * up to MAX_RIDES rides, with walking for access, egress and transfers.
 * Map geometry: metro and rail legs follow OSM track shapes precomputed by build_web.py ("sh"). Walking legs
 * and road legs (bus, minibus, auto, tram) of the selected itinerary only are routed on demand by the
 * FOSSGIS OSRM service (routing.openstreetmap.de, usage policy: at most 1 request per second, no bulk,
 * attribution), queued, cached, with a straight-line fallback. Ferry legs stay straight across the river.
 */
(function () {
  "use strict";

  // ---------- settings ----------
  const WALK_M_PER_MIN = 75;          // 4.5 km/h
  const WALK_DETOUR = 1.2;            // straight line to street distance
  // Each walking minute weighs this much in the search and the ranking (shown times stay real minutes).
  // Straight-line walks underestimate where canals, railways and the river force detours (Salt Lake to
  // Kestopur across the Kestopur canal is 13 min estimated but 24 min on the street), so long walks to
  // save a ride are distrusted, as in OpenTripPlanner's walkReluctance (default 2).
  let WALK_RELUCTANCE = 2;
  // Backtracking: an option whose straight-line path (start, each boarding and alighting stop, end) is
  // longer than DETOUR_FREE x the direct distance + DETOUR_FREE_M costs DETOUR_MIN_PER_KM per extra km in
  // the ranking (e.g. riding the metro away from the destination to catch a bus back).
  let DETOUR_FREE = 1.4;
  const DETOUR_FREE_M = 1000;
  let DETOUR_MIN_PER_KM = 4;
  const ACCESS_M = 1500;              // access and egress walk radius
  const TRANSFER_M = 600;             // stop to stop transfer walk radius
  const WAIT_CAP = 15;                // expected wait = half headway, capped
  const BOARD_PENALTY = 3;            // per boarding, minutes (preference, not counted as travel time)
  const MAX_RIDES = 4;
  const DIVERSITY_PENALTY = 20;       // minutes added per boarding of a route used by an earlier option
  const WALK_ONLY_MAX = 40;           // show a walk-only option up to this many minutes
  const KOLKATA_VIEWBOX = "88.05,22.85,88.65,22.30"; // left,top,right,bottom
  // walks may only cross the Hooghly on a bridge with a footway (centreline "rv" in network.json)
  const WALK_BRIDGES = [[22.5851, 88.3469], [22.6500, 88.3546]]; // Howrah Bridge, Vivekananda Setu (Bally)
  const BRIDGE_SNAP_M = 600;
  // stops placed on the deck of a road bridge with no footway: ride through only, never walk to or from
  const NO_WALK_SPANS = [[22.5569, 88.3278, 250]]; // Vidyasagar Setu, mid-river (lat, lon, radius m)
  // street routing (FOSSGIS OSRM). One request at a time, at least OSRM_GAP_MS apart.
  const OSRM_BASE = "https://routing.openstreetmap.de/";
  const OSRM_GAP_MS = 1100;
  const OSRM_TIMEOUT_MS = 12000;
  const ROAD_MAX_WAYPOINTS = 24;
  const ROAD_MODES = new Set(["bus", "minibus", "auto", "tram"]);
  const STREET_WALK_MIN_M = 80;       // shorter walks are drawn straight, no request

  const MODES = [
    { key: "bus", label: "Bus" },
    { key: "minibus", label: "Minibus" },
    { key: "metro", label: "Metro" },
    { key: "rail", label: "Train" },
    { key: "auto", label: "Auto" },
    { key: "ferry", label: "Ferry" },
    { key: "tram", label: "Tram" },
  ];
  const MODE_LABEL = { bus: "Bus", minibus: "Minibus", metro: "Metro", rail: "Train", auto: "Auto", ferry: "Ferry", tram: "Tram" };
  const LETTER_MODE = { b: "bus", n: "minibus", m: "metro", r: "rail", a: "auto", f: "ferry", t: "tram" };
  let COLORS = {};

  // ---------- state ----------
  const NET = { stops: [], lat: null, lon: null, routes: [], pats: [], stopPats: [], foot: [], grid: new Map(), places: [], shapes: {}, shapeCache: new Map(), river: [], waterGrid: new Map(), bridges: [], noWalk: new Set() };
  const WCELL = 0.01; // water segment grid, about 1 km
  const sel = { from: null, to: null };
  const activeModes = new Set(MODES.map((m) => m.key));
  let itineraries = [];
  let selIdx = 0;

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function distM(lat1, lon1, lat2, lon2) {
    const R = 6371000, toR = Math.PI / 180;
    const dLat = (lat2 - lat1) * toR, dLon = (lon2 - lon1) * toR;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * toR) * Math.cos(lat2 * toR) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }
  const walkMin = (m) => (m * WALK_DETOUR) / WALK_M_PER_MIN;
  function decodePoly(str) {
    const out = [];
    let i = 0, lat = 0, lon = 0;
    while (i < str.length) {
      for (let k = 0; k < 2; k++) {
        let sh = 0, r = 0, b;
        do { b = str.charCodeAt(i++) - 63; r |= (b & 0x1f) << sh; sh += 5; } while (b >= 0x20);
        const v = r & 1 ? ~(r >> 1) : r >> 1;
        if (k === 0) lat += v; else lon += v;
      }
      out.push([lat / 1e5, lon / 1e5]);
    }
    return out;
  }
  // spelling-tolerant key: Keshtopur = Kestopur, Shobhabazar = Sovabazar, Dum Dum = Dumdum
  function normName(s) {
    return s.toLowerCase().replace(/[^a-z0-9]/g, "")
      .replace(/([bcdgkpst])h/g, "$1").replace(/v/g, "b").replace(/w/g, "b")
      .replace(/ee/g, "i").replace(/oo/g, "u").replace(/(.)\1+/g, "$1");
  }
  // Walking distance in metres between two points: the straight line, or, when it crosses water, the
  // shortest straight-line path over a bridge: for a canal any walkable OSM road bridge over it within
  // CANAL_BRIDGE_M of the crossing ("wb"), for the Hooghly only WALK_BRIDGES. Infinity when no bridge fits.
  const CANAL_BRIDGE_M = 2000;
  function firstCrossing(lat1, lon1, lat2, lon2) {
    const k = Math.cos(lat1 * Math.PI / 180);
    const ax = lon1 * k, ay = lat1, bx = lon2 * k, by = lat2;
    const minX = Math.min(ax, bx), maxX = Math.max(ax, bx), minY = Math.min(ay, by), maxY = Math.max(ay, by);
    const seen = new Set();
    let best = null;
    for (let i = Math.floor(Math.min(lat1, lat2) / WCELL); i <= Math.floor(Math.max(lat1, lat2) / WCELL); i++) {
      for (let j = Math.floor(Math.min(lon1, lon2) / WCELL); j <= Math.floor(Math.max(lon1, lon2) / WCELL); j++) {
        for (const sg of NET.waterGrid.get(i + ":" + j) || []) {
          if (seen.has(sg)) continue;
          seen.add(sg);
          if (sg.maxX < minX || sg.minX > maxX || sg.maxY < minY || sg.minY > maxY) continue;
          const rx = sg.bx - sg.ax, ry = sg.by - sg.ay, qx = bx - ax, qy = by - ay;
          const den = qx * ry - qy * rx;
          if (den === 0) continue;
          const t = ((sg.ax - ax) * ry - (sg.ay - ay) * rx) / den;
          const u = ((sg.ax - ax) * qy - (sg.ay - ay) * qx) / den;
          if (t <= 1e-6 || t >= 1 - 1e-6 || u < 0 || u > 1) continue; // ending on the bank is not crossing
          if (!best || t < best.t) best = { t, lat: ay + t * qy, lon: (ax + t * qx) / k, river: sg.river };
        }
      }
    }
    return best;
  }
  function walkM(lat1, lon1, lat2, lon2, depth) {
    const d = distM(lat1, lon1, lat2, lon2);
    if (!NET.waterGrid.size) return d;
    const x = firstCrossing(lat1, lon1, lat2, lon2);
    if (!x) return d;
    if ((depth || 0) >= 2) return Infinity;
    const cands = x.river ? WALK_BRIDGES.filter(([a, b]) => distM(x.lat, x.lon, a, b) <= BRIDGE_SNAP_M)
      : NET.bridges.filter(([a, b]) => Math.abs(a - x.lat) < 0.02 && Math.abs(b - x.lon) < 0.02 && distM(x.lat, x.lon, a, b) <= CANAL_BRIDGE_M);
    let best = Infinity;
    for (const [blat, blon] of cands) {
      const viaD = distM(lat1, lon1, blat, blon) + distM(blat, blon, lat2, lon2);
      if (viaD >= best) continue;
      const v = walkM(lat1, lon1, blat, blon, (depth || 0) + 1) + walkM(blat, blon, lat2, lon2, (depth || 0) + 1);
      if (v < best) best = v;
    }
    return best;
  }

  const fmtMin = (x) => {
    const m = Math.max(1, Math.round(x));
    if (m < 60) return m + " min";
    return Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0") + " min";
  };
  const fmtDist = (m) => (m < 1000 ? Math.round(m / 10) * 10 + " m" : (m / 1000).toFixed(1) + " km");
  function setStatus(msg, err) {
    const el = $("status");
    el.textContent = msg || "";
    el.classList.toggle("err", !!err);
  }

  // grid index, cell about 0.01 deg
  const CELL = 0.01;
  const cellKey = (lat, lon) => Math.floor(lat / CELL) + ":" + Math.floor(lon / CELL);
  function nearbyStops(lat, lon, radius) {
    const out = [];
    const r = Math.ceil(radius / 1000 / (CELL * 100)) + 1;
    const ci = Math.floor(lat / CELL), cj = Math.floor(lon / CELL);
    for (let i = ci - r; i <= ci + r; i++) {
      for (let j = cj - r; j <= cj + r; j++) {
        const arr = NET.grid.get(i + ":" + j);
        if (!arr) continue;
        for (const s of arr) {
          const d = distM(lat, lon, NET.lat[s], NET.lon[s]);
          if (d <= radius) out.push([s, d]);
        }
      }
    }
    return out;
  }

  // stops within radius by walking distance (river aware)
  function nearbyWalk(lat, lon, radius) {
    const out = [];
    for (const [s] of nearbyStops(lat, lon, radius)) {
      if (NET.noWalk.has(s)) continue;
      const w = walkM(lat, lon, NET.lat[s], NET.lon[s]);
      if (w <= radius) out.push([s, w]);
    }
    return out;
  }

  // ---------- network load ----------
  async function loadNetwork() {
    const res = await fetch("data/network.json");
    if (!res.ok) throw new Error("network.json HTTP " + res.status);
    const net = await res.json();
    COLORS = net.meta.colors;
    const n = net.stops.length;
    NET.stops = net.stops.map((s) => ({ id: s[0], name: s[1], modes: s[4].split("").map((c) => LETTER_MODE[c]) }));
    NET.lat = new Float64Array(n);
    NET.lon = new Float64Array(n);
    net.stops.forEach((s, i) => {
      NET.lat[i] = s[2];
      NET.lon[i] = s[3];
      const k = cellKey(s[2], s[3]);
      if (!NET.grid.has(k)) NET.grid.set(k, []);
      NET.grid.get(k).push(i);
    });
    NET.routes = net.routes;
    NET.shapes = net.sh || {};
    NET.bridges = net.wb || [];
    const addWater = (enc, river) => {
      const pts = decodePoly(enc);
      for (let i = 0; i + 1 < pts.length; i++) {
        const k = Math.cos(pts[i][0] * Math.PI / 180);
        const sg = { ax: pts[i][1] * k, ay: pts[i][0], bx: pts[i + 1][1] * k, by: pts[i + 1][0], river };
        sg.minX = Math.min(sg.ax, sg.bx); sg.maxX = Math.max(sg.ax, sg.bx); sg.minY = Math.min(sg.ay, sg.by); sg.maxY = Math.max(sg.ay, sg.by);
        NET.river.push(sg);
        const la0 = Math.min(pts[i][0], pts[i + 1][0]), la1 = Math.max(pts[i][0], pts[i + 1][0]);
        const lo0 = Math.min(pts[i][1], pts[i + 1][1]), lo1 = Math.max(pts[i][1], pts[i + 1][1]);
        for (let a = Math.floor(la0 / WCELL); a <= Math.floor(la1 / WCELL); a++) {
          for (let b = Math.floor(lo0 / WCELL); b <= Math.floor(lo1 / WCELL); b++) {
            const key = a + ":" + b;
            if (!NET.waterGrid.has(key)) NET.waterGrid.set(key, []);
            NET.waterGrid.get(key).push(sg);
          }
        }
      }
    };
    for (const enc of net.rv || []) addWater(enc, true);
    for (const enc of net.wc || []) addWater(enc, false);
    NET.stopPats = Array.from({ length: n }, () => []);
    net.routes.forEach((r, ri) => {
      for (const p of r.p) {
        const pi = NET.pats.length;
        const h = p.length > 3 ? p[3] : r.h;
        NET.pats.push({ r: ri, dir: p[0], stops: p[1], cum: p[2], h: h, mode: r.m });
        p[1].forEach((s, pos) => NET.stopPats[s].push(pi, pos));
      }
    });
    // transfer footpaths: stops within TRANSFER_M, plus reviewed interchanges
    // stops placed on a road bridge deck without a footway (Vidyasagar Setu): ride through only
    NET.noWalk = new Set();
    for (let s = 0; s < n; s++) {
      if (NO_WALK_SPANS.some(([a, b, r]) => distM(NET.lat[s], NET.lon[s], a, b) <= r)) NET.noWalk.add(s);
    }
    NET.foot = Array.from({ length: n }, () => new Map());
    for (let s = 0; s < n; s++) {
      if (NET.noWalk.has(s)) continue;
      for (const [t, d] of nearbyWalk(NET.lat[s], NET.lon[s], TRANSFER_M)) {
        if (t !== s && d <= TRANSFER_M) NET.foot[s].set(t, walkMin(d));
      }
    }
    for (const [a, b, m] of net.transfers) NET.foot[a].set(b, Math.max(m, NET.foot[a].get(b) || 0));
    // places for autocomplete: group same-name stops within 1 km. The group's point is its most
    // significant stop (a rail or metro station over a bus stop of the same name), so picking "Sealdah"
    // starts at the station rather than at whichever stop happened to be listed first.
    const RANK = { rail: 6, metro: 5, ferry: 4, tram: 3, bus: 2, minibus: 2, auto: 1 };
    const rankOf = (s) => Math.max(0, ...s.modes.map((m) => RANK[m] || 0));
    const byName = new Map();
    NET.stops.forEach((s, i) => {
      const key = s.name.toLowerCase();
      if (!byName.has(key)) byName.set(key, []);
      const groups = byName.get(key);
      let g = groups.find((g) => distM(g.lat, g.lon, NET.lat[i], NET.lon[i]) < 1000);
      if (!g) { g = { name: s.name, lat: NET.lat[i], lon: NET.lon[i], modes: new Set(), n: 0, rank: -1 }; groups.push(g); }
      const rk = rankOf(s);
      if (rk > g.rank) { g.rank = rk; g.lat = NET.lat[i]; g.lon = NET.lon[i]; }
      s.modes.forEach((m) => g.modes.add(m));
      g.n++;
    });
    for (const groups of byName.values()) for (const g of groups) { g.norm = normName(g.name); NET.places.push(g); }
    $("builtInfo").textContent = "Network built " + net.meta.built + ": " + n + " stops, " + net.routes.length + " routes.";
  }

  // ---------- routing ----------
  function route(from, to, modes, penalties) {
    const n = NET.stops.length;
    const INF = Infinity;
    const arr = [], how = [], walkFrom = [], arrR = [], rideP = [];
    for (let k = 0; k <= MAX_RIDES; k++) {
      arr.push(new Float64Array(n).fill(INF));
      how.push(new Int8Array(n));         // 0 carried, 1 ride, 2 transfer walk, 3 access walk
      walkFrom.push(new Int32Array(n).fill(-1));
      arrR.push(new Float64Array(n).fill(INF));
      rideP.push(new Int32Array(n * 2).fill(-1)); // [pattern, boardPos]; alight pos found from stop
    }
    const access = nearbyWalk(from.lat, from.lon, ACCESS_M);
    const egress = nearbyWalk(to.lat, to.lon, ACCESS_M);
    let marked = new Set();
    for (const [s, d] of access) {
      arr[0][s] = walkMin(d) * WALK_RELUCTANCE;
      how[0][s] = 3;
      marked.add(s);
    }
    const alightPos = [];
    for (let k = 1; k <= MAX_RIDES; k++) {
      alightPos.push(null);
      const A = arr[k], Ap = arr[k - 1];
      A.set(Ap); // carry over
      // patterns to scan from the earliest marked position
      const q = new Map();
      for (const s of marked) {
        const sp = NET.stopPats[s];
        for (let i = 0; i < sp.length; i += 2) {
          const pi = sp[i], pos = sp[i + 1];
          if (!modes.has(NET.pats[pi].mode)) continue;
          const cur = q.get(pi);
          if (cur === undefined || pos < cur) q.set(pi, pos);
        }
      }
      const improved = new Set();
      const ap = new Map(); // stop -> alight position, for this round
      for (const [pi, pos0] of q) {
        const p = NET.pats[pi];
        const board = Math.min(p.h / 2, WAIT_CAP) + BOARD_PENALTY + (penalties.get(p.r) || 0);
        let base = INF, bPos = -1;
        for (let i = pos0; i < p.stops.length; i++) {
          const s = p.stops[i];
          if (base < INF) {
            const t = base + p.cum[i];
            if (t < arrR[k][s]) {
              arrR[k][s] = t;
              rideP[k][2 * s] = pi;
              rideP[k][2 * s + 1] = bPos;
              ap.set(s, i);
              if (t < A[s]) { A[s] = t; how[k][s] = 1; improved.add(s); }
            }
          }
          if (Ap[s] < INF) {
            const b = Ap[s] + board - p.cum[i];
            if (b < base) { base = b; bPos = i; }
          }
        }
      }
      alightPos[k - 1] = ap;
      // transfer walks from stops reached by a ride this round
      for (const s of Array.from(improved)) {
        if (how[k][s] !== 1) continue;
        for (const [t, w] of NET.foot[s]) {
          const v = arrR[k][s] + w * WALK_RELUCTANCE;
          if (v < A[t]) { A[t] = v; how[k][t] = 2; walkFrom[k][t] = s; improved.add(t); }
        }
      }
      marked = improved;
      if (!marked.size) break;
    }
    // best per number of rides
    const results = [];
    for (let k = 1; k <= MAX_RIDES; k++) {
      let best = INF, bs = -1, bd = 0;
      for (const [s, d] of egress) {
        if (how[k][s] === 0 && k > 0 && arr[k][s] === arr[k - 1][s]) continue; // only stops reached in this round
        const v = arr[k][s] + walkMin(d) * WALK_RELUCTANCE;
        if (v < best) { best = v; bs = s; bd = d; }
      }
      if (bs >= 0) results.push({ k, cost: best, legs: trace(k, bs, bd) });
    }
    return results;

    function trace(k, s, egressD) {
      const legs = [{ type: "walk", fromStop: s, toPoint: "to", m: egressD }];
      let guard = 0;
      while (guard++ < 50) {
        if (k === 0 || how[k][s] === 3) {
          legs.unshift({ type: "walk", fromPoint: "from", toStop: s, m: walkM(from.lat, from.lon, NET.lat[s], NET.lon[s]) });
          break;
        }
        const h = how[k][s];
        if (h === 0) { k--; continue; }
        let rs = s;
        if (h === 2) {
          const u = walkFrom[k][s];
          legs.unshift({ type: "walk", fromStop: u, toStop: s, min: NET.foot[u].get(s) });
          rs = u;
        }
        const pi = rideP[k][2 * rs], bPos = rideP[k][2 * rs + 1];
        const aPos = alightPos[k - 1].get(rs);
        legs.unshift({ type: "ride", pat: pi, bPos, aPos });
        s = NET.pats[pi].stops[bPos];
        k--;
      }
      return finishLegs(legs);
    }
  }

  function finishLegs(legs) {
    // merge consecutive walks, compute minutes, drop zero walks
    const out = [];
    for (const l of legs) {
      if (l.type === "walk") {
        const mins = l.min !== undefined ? l.min : walkMin(l.m);
        const prev = out[out.length - 1];
        if (prev && prev.type === "walk") { prev.min += mins; prev.toStop = l.toStop; prev.toPoint = l.toPoint; continue; }
        out.push({ type: "walk", fromStop: l.fromStop, fromPoint: l.fromPoint, toStop: l.toStop, toPoint: l.toPoint, min: mins });
      } else {
        const p = NET.pats[l.pat];
        const wait = Math.min(p.h / 2, WAIT_CAP);
        out.push({ type: "ride", pat: l.pat, bPos: l.bPos, aPos: l.aPos, min: p.cum[l.aPos] - p.cum[l.bPos], wait, h: p.h });
      }
    }
    return out.filter((l) => !(l.type === "walk" && l.min < 0.5 && out.length > 1));
  }

  function totalMin(legs) {
    return legs.reduce((a, l) => a + l.min + (l.type === "ride" ? l.wait : 0), 0);
  }
  // ranking score: real minutes plus the walk reluctance surcharge
  const walkTotal = (legs) => legs.filter((l) => l.type === "walk").reduce((a, l) => a + l.min, 0);
  function pathM(legs) {
    const pts = [[sel.from.lat, sel.from.lon]];
    for (const l of legs) {
      if (l.type !== "ride") continue;
      const p = NET.pats[l.pat];
      pts.push(stopLL(p.stops[l.bPos]), stopLL(p.stops[l.aPos]));
    }
    pts.push([sel.to.lat, sel.to.lon]);
    let d = 0;
    for (let i = 1; i < pts.length; i++) d += distM(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]);
    return d;
  }
  function detourPenalty(legs) {
    if (!sel.from || !sel.to) return 0;
    const direct = distM(sel.from.lat, sel.from.lon, sel.to.lat, sel.to.lon);
    const extra = pathM(legs) - (direct * DETOUR_FREE + DETOUR_FREE_M);
    return extra > 0 ? (extra / 1000) * DETOUR_MIN_PER_KM : 0;
  }
  // One weighted cost, used both to choose which options to show and, with the corrected street walk
  // minutes, to order them: real minutes + walk reluctance surcharge + backtracking penalty.
  const scoreOf = (c) => c.total + (WALK_RELUCTANCE - 1) * walkTotal(c.legs) + detourPenalty(c.legs);
  const rideRoutes = (legs) => legs.filter((l) => l.type === "ride").map((l) => NET.pats[l.pat].r);
  const signature = (legs) => legs.filter((l) => l.type === "ride").map((l) => NET.pats[l.pat].r + "@" + NET.pats[l.pat].stops[l.bPos] + ">" + NET.pats[l.pat].stops[l.aPos]).join("|");
  const stopSig = (legs) => legs.filter((l) => l.type === "ride").map((l) => { const p = NET.pats[l.pat]; return p.mode + "@" + p.stops[l.bPos] + ">" + p.stops[l.aPos]; }).join("|");
  const routeSig = (legs) => rideRoutes(legs).join("|");

  // Shown order: real minutes (street-corrected where known), ties by weighted cost. Which options make
  // the list is decided by the weighted cost (scoreOf) in plan().
  const byTime = (a, b) => Math.round(a.total) - Math.round(b.total) || scoreOf(a) - scoreOf(b);
  function resortKeepSelection() {
    const cur = itineraries[selIdx];
    itineraries.sort(byTime);
    selIdx = Math.max(0, itineraries.indexOf(cur));
  }

  function plan(from, to, modes) {
    const cands = [];
    const add = (legs) => {
      if (!legs || !legs.some((l) => l.type === "ride")) return;
      const rs = routeSig(legs);
      if (cands.some((c) => c.rsig === rs)) return;
      // same boarding and alighting stops on the same modes: fold in as an alternative route ("or 218")
      const ss = stopSig(legs);
      const twin = cands.find((c) => stopSig(c.legs) === ss);
      if (twin) {
        const mine = legs.filter((l) => l.type === "ride"), theirs = twin.legs.filter((l) => l.type === "ride");
        mine.forEach((l, i) => {
          const r = NET.pats[l.pat].r, t = theirs[i];
          if (r !== NET.pats[t.pat].r) { t.alts = t.alts || []; if (!t.alts.includes(r)) t.alts.push(r); }
        });
        twin.rsig += "/" + rs;
        return;
      }
      cands.push({ legs, total: totalMin(legs), rides: rideRoutes(legs).length, rsig: rs, sig: signature(legs) });
    };
    // run 1: best per ride count, keep only options that save time over fewer rides
    const r1 = route(from, to, modes, new Map());
    let bestCost = Infinity;
    for (const r of r1) {
      if (r.cost < bestCost - 1) { add(r.legs); bestCost = r.cost; }
    }
    // runs 2 to 6: penalise routes already used
    const pen = new Map();
    for (let run = 0; run < 5; run++) {
      for (const c of cands) {
        for (const l of c.legs) {
          if (l.type !== "ride") continue;
          for (const ri of [NET.pats[l.pat].r].concat(l.alts || [])) pen.set(ri, (pen.get(ri) || 0) + DIVERSITY_PENALTY);
        }
      }
      const rr = route(from, to, modes, pen);
      if (!rr.length) break;
      rr.sort((a, b) => a.cost - b.cost);
      add(rr[0].legs);
    }
    const directM = walkM(from.lat, from.lon, to.lat, to.lon);
    const walkOnly = walkMin(directM);
    let list = cands.sort((a, b) => scoreOf(a) - scoreOf(b));
    if (list.length) list = list.filter((c) => scoreOf(c) <= scoreOf(list[0]) * 2 + 15);
    list = list.slice(0, 3);
    if (walkOnly <= WALK_ONLY_MAX || (!list.length && walkOnly <= 120)) {
      list.push({ legs: [{ type: "walk", fromPoint: "from", toPoint: "to", min: walkOnly }], total: walkOnly, rides: 0, walkOnly: true });
    }
    list.sort(byTime);
    return list;
  }

  // ---------- map ----------
  const map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([22.5726, 88.3639], 12);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
  const darkMQ = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : { matches: false };

  // Base maps. Default: OpenFreeMap vector tiles (free, no key, no usage limits, attribution required),
  // drawn by MapLibre GL, Positron style in light mode and Dark style in dark mode. OpenStreetMap Standard
  // raster tiles stay available from the layer switcher, and are the fallback when WebGL or MapLibre fails.
  const OFM_ATTR = '<a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> &copy; <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
  const ML_JS = { src: "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js", integrity: "sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp" };
  const ML_CSS = { href: "https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css", integrity: "sha384-uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK" };
  const ML_LEAFLET = { src: "https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js", integrity: "sha384-tXYNKOHx4T02jMP7YYCtBxPIv1B5gaA5mcVPBzqMp6d7VzWzxJgI2aWF/nJLrQdS" };
  const osmLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>',
  });
  const BASE_KEY = "kolPlanner.base";
  const store = {
    get() { try { return localStorage.getItem(BASE_KEY); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(BASE_KEY, v); } catch (e) { /* storage blocked */ } },
  };
  let baseName = "pending";
  function loadScript(o) {
    return new Promise((res, rej) => {
      const el = document.createElement("script");
      el.src = o.src; el.integrity = o.integrity; el.crossOrigin = "anonymous";
      el.onload = res; el.onerror = () => rej(new Error("could not load " + o.src));
      document.head.appendChild(el);
    });
  }
  function webglOK() {
    try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch (e) { return false; }
  }
  async function setupBaseMaps() {
    const saved = store.get();
    const useOsm = () => { if (!map.hasLayer(osmLayer)) osmLayer.addTo(map); baseName = "osm"; };
    if (saved === "osm" || !webglOK()) {
      useOsm();
      if (!webglOK()) { syncBaseTheme(); return; }
    }
    try {
      const css = document.createElement("link");
      css.rel = "stylesheet"; css.href = ML_CSS.href; css.integrity = ML_CSS.integrity; css.crossOrigin = "anonymous";
      document.head.appendChild(css);
      await Promise.race([
        loadScript(ML_JS).then(() => loadScript(ML_LEAFLET)),
        new Promise((_, rej) => setTimeout(() => rej(new Error("MapLibre load timeout")), 15000)),
      ]);
      const ofm = (style) => L.maplibreGL({ style: "https://tiles.openfreemap.org/styles/" + style, attribution: OFM_ATTR, interactive: false });
      const clean = ofm(darkMQ.matches ? "dark" : "positron");
      const detailed = ofm("liberty");
      const layers = { "Clean": clean, "Detailed": detailed, "OpenStreetMap": osmLayer };
      L.control.layers(layers, null, { position: "topright" }).addTo(map);
      const want = saved && layers[saved] ? saved : saved === "osm" ? "OpenStreetMap" : "Clean";
      if (want !== "OpenStreetMap") {
        if (map.hasLayer(osmLayer)) map.removeLayer(osmLayer);
        layers[want].addTo(map);
      } else useOsm();
      baseName = want;
      map.on("baselayerchange", (e) => { baseName = e.name; store.set(e.name === "OpenStreetMap" ? "osm" : e.name); syncBaseTheme(); drawItin(itineraries[selIdx], true); });
    } catch (e) {
      useOsm();
    }
    syncBaseTheme();
    drawItin(itineraries[selIdx], true);
  }
  function syncBaseTheme() {
    document.documentElement.dataset.base = baseName === "Clean" && darkMQ.matches ? "dark" : "light";
  }
  setupBaseMaps();

  const routeLayer = L.layerGroup().addTo(map);
  const pinIcon = (cls) => L.divIcon({ className: "", html: '<div class="pin ' + cls + '"></div>', iconSize: [18, 18], iconAnchor: [9, 9] });
  const markers = { from: null, to: null };

  function setPoint(which, pt, keepText) {
    sel[which] = pt;
    if (markers[which]) map.removeLayer(markers[which]);
    markers[which] = null;
    if (pt) {
      markers[which] = L.marker([pt.lat, pt.lon], { icon: pinIcon(which), title: which === "from" ? "Start" : "Destination", keyboard: false }).addTo(map);
      if (!keepText) $(which + "Input").value = pt.label;
    }
    if (sel.from && sel.to) map.fitBounds(L.latLngBounds([[sel.from.lat, sel.from.lon], [sel.to.lat, sel.to.lon]]).pad(0.25), { animate: false });
    else if (pt) map.panTo([pt.lat, pt.lon], { animate: false });
  }

  map.on("click", (e) => {
    const { lat, lng } = e.latlng;
    const label = "Map point " + lat.toFixed(4) + ", " + lng.toFixed(4);
    const div = document.createElement("div");
    div.className = "map-pop";
    const b1 = document.createElement("button");
    b1.textContent = "Start here";
    const b2 = document.createElement("button");
    b2.textContent = "Go here";
    div.append(b1, b2);
    const pop = L.popup({ closeButton: false }).setLatLng(e.latlng).setContent(div).openOn(map);
    b1.onclick = () => { setPoint("from", { lat, lon: lng, label }); map.closePopup(pop); };
    b2.onclick = () => { setPoint("to", { lat, lon: lng, label }); map.closePopup(pop); };
  });

  function stopLL(s) { return [NET.lat[s], NET.lon[s]]; }

  // ---------- leg geometry ----------
  // Track shape for one metro or rail hop, from stop a to stop b, or null.
  function hopShape(a, b) {
    const key = Math.min(a, b) + "-" + Math.max(a, b);
    if (!NET.shapes[key]) return null;
    if (!NET.shapeCache.has(key)) NET.shapeCache.set(key, decodePoly(NET.shapes[key]));
    const pts = NET.shapeCache.get(key);
    return a < b ? pts : pts.slice().reverse();
  }
  const osrmCache = new Map(); // key -> {pts, m} or null (failed)
  let osrmChain = Promise.resolve();
  let osrmLast = 0;
  let osrmCount = 0;
  const llKey = (p) => p[0].toFixed(5) + "," + p[1].toFixed(5);
  function legEnds(l) {
    const a = l.fromPoint ? [sel.from.lat, sel.from.lon] : stopLL(l.fromStop);
    const b = l.toPoint ? [sel.to.lat, sel.to.lon] : stopLL(l.toStop);
    return [a, b];
  }
  // What to ask the street router for a leg: {profile, pts} or null (no request).
  function legRequest(l) {
    if (l.type === "walk") {
      const [a, b] = legEnds(l);
      if (distM(a[0], a[1], b[0], b[1]) < STREET_WALK_MIN_M) return null;
      return { profile: "routed-foot", pts: [a, b] };
    }
    const p = NET.pats[l.pat];
    if (!ROAD_MODES.has(p.mode)) return null;
    let idx = [];
    for (let i = l.bPos; i <= l.aPos; i++) idx.push(i);
    if (idx.length > ROAD_MAX_WAYPOINTS) {
      const n = ROAD_MAX_WAYPOINTS, step = (idx.length - 1) / (n - 1);
      idx = Array.from({ length: n }, (_, i) => idx[Math.round(i * step)]);
    }
    return { profile: "routed-car", pts: idx.map((i) => stopLL(p.stops[i])) };
  }
  const reqKey = (r) => r.profile + ":" + r.pts.map(llKey).join(";");
  function straightLen(pts) { let d = 0; for (let i = 1; i < pts.length; i++) d += distM(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]); return d; }

  // Queue one OSRM request; requests run one at a time, OSRM_GAP_MS apart, and are skipped if the
  // itinerary they belong to is no longer the selected one by the time their turn comes.
  function osrmFetch(req, stillWanted) {
    const key = reqKey(req);
    if (osrmCache.has(key)) return Promise.resolve(osrmCache.get(key));
    osrmChain = osrmChain.then(async () => {
      if (osrmCache.has(key)) return osrmCache.get(key);
      if (!stillWanted()) return undefined;
      const wait = osrmLast + OSRM_GAP_MS - Date.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      if (!stillWanted()) return undefined;
      osrmLast = Date.now();
      osrmCount++;
      const coords = req.pts.map((p) => p[1].toFixed(5) + "," + p[0].toFixed(5)).join(";");
      const car = req.profile === "routed-car";
      // car: per-stop-hop geometry (from steps) so one badly snapped stop only spoils its own hops
      const url = OSRM_BASE + req.profile + "/route/v1/driving/" + coords + "?geometries=geojson&alternatives=false" +
        (car ? "&overview=false&steps=true&continue_straight=false" : "&overview=full&steps=false");
      let val = null;
      try {
        const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
        const timer = ctl ? setTimeout(() => ctl.abort(), OSRM_TIMEOUT_MS) : null;
        const r = await fetch(url, { signal: ctl ? ctl.signal : undefined, headers: { Accept: "application/json" } });
        if (timer) clearTimeout(timer);
        if (r.ok) {
          const js = await r.json();
          const rt = js.code === "Ok" && js.routes && js.routes[0];
          if (rt && car && rt.legs && rt.legs.length === req.pts.length - 1) {
            // a hop is kept when the road distance is plausible for the straight distance between its stops,
            // otherwise (stop snapped onto a flyover or the far carriageway) that hop is drawn straight
            let pts = [], good = 0;
            rt.legs.forEach((lg, i) => {
              const a = req.pts[i], b = req.pts[i + 1];
              const sl = distM(a[0], a[1], b[0], b[1]);
              const hop = [];
              for (const st of lg.steps || []) for (const c of st.geometry.coordinates) hop.push([c[1], c[0]]);
              // a bad hop leaves a gap: the line joins the neighbouring good pieces (or the stop) straight,
              // instead of zigzagging out to a stop placed off the road
              if (hop.length >= 2 && lg.distance <= sl * 2.5 + 600) { pts = pts.concat(hop); good++; }
            });
            if (good) val = { pts: trimSpurs(pts), m: rt.distance, partial: good < rt.legs.length };
          } else if (rt && !car && rt.geometry && rt.geometry.coordinates.length >= 2) {
            const sl = straightLen(req.pts);
            // reject absurd detours (snapped to the wrong side of a canal or a closed road)
            if (rt.distance <= sl * 4 + 500) val = { pts: rt.geometry.coordinates.map((c) => [c[1], c[0]]), m: rt.distance };
          }
        }
      } catch (e) { val = null; }
      osrmCache.set(key, val);
      return val;
    });
    return osrmChain;
  }

  // Drop out-and-back excursions (the route drives up a side road to a stop placed off the main road and
  // returns the same way), up to SPUR_MAX_M long, so the line shows the road actually travelled.
  // Also drops small loops (around a block to reach a stop on a one-way or divided road): whenever the
  // line comes back within LOOP_NEAR_M of a point it passed at most SPUR_MAX_M of travel earlier.
  const SPUR_MAX_M = 1500;
  const LOOP_NEAR_M = 50;
  function trimSpurs(pts) {
    const out = [], cum = [];
    for (const p of pts) {
      const n = out.length;
      const here = n ? cum[n - 1] + distM(out[n - 1][0], out[n - 1][1], p[0], p[1]) : 0;
      let cut = -1;
      for (let j = n - 2; j >= 0 && here - cum[j] <= SPUR_MAX_M; j--) {
        if (here - cum[j] > 4 * LOOP_NEAR_M && distM(out[j][0], out[j][1], p[0], p[1]) <= LOOP_NEAR_M) cut = j;
      }
      if (cut >= 0) { out.length = cut + 1; cum.length = cut + 1; continue; }
      out.push(p);
      cum.push(here);
    }
    return out;
  }

  // Geometry of a leg with what is known now: {pts, src} where src is track, street, straight or pending.
  function legGeom(l) {
    if (l.type === "walk") {
      const [a, b] = legEnds(l);
      const req = legRequest(l);
      const c = req && osrmCache.get(reqKey(req));
      if (c) return { pts: [a].concat(c.pts, [b]), src: "street" };
      return { pts: [a, b], src: req && !osrmCache.has(reqKey(req)) ? "pending" : "straight" };
    }
    const p = NET.pats[l.pat];
    const stops = p.stops.slice(l.bPos, l.aPos + 1);
    if (p.mode === "metro" || p.mode === "rail") {
      let pts = [stopLL(stops[0])], onTrack = 0;
      for (let i = 1; i < stops.length; i++) {
        const sh = hopShape(stops[i - 1], stops[i]);
        if (sh) { pts = pts.concat(sh); onTrack++; } else pts.push(stopLL(stops[i]));
      }
      return { pts, src: onTrack === stops.length - 1 ? "track" : onTrack ? "track+straight" : "straight" };
    }
    const req = legRequest(l);
    const c = req && osrmCache.get(reqKey(req));
    if (c) return { pts: [stopLL(stops[0])].concat(c.pts, [stopLL(stops[stops.length - 1])]), src: c.partial ? "street+straight" : "street" };
    return { pts: stops.map(stopLL), src: req && !osrmCache.has(reqKey(req)) ? "pending" : "straight" };
  }

  // Ask the street router for the selected itinerary's walking and road legs, redrawing as each arrives.
  // Street walking distance also replaces the straight-line walk estimate for that itinerary.
  function refineItin(it) {
    const wanted = () => itineraries[selIdx] === it;
    let changedNow = false;
    for (const l of it.legs) {
      const req = legRequest(l);
      if (!req || osrmCache.has(reqKey(req))) { if (applyStreetWalk(l)) changedNow = true; continue; }
      osrmFetch(req, wanted).then((v) => {
        if (v === undefined || !wanted()) return;
        const changed = applyStreetWalk(l);
        drawItin(it, true);
        if (changed) { it.total = totalMin(it.legs); resortKeepSelection(); renderResults(); }
      });
    }
    if (changedNow) { it.total = totalMin(it.legs); resortKeepSelection(); renderResults(); }
  }
  function applyStreetWalk(l) {
    if (l.type !== "walk" || l.streetM !== undefined) return false;
    const req = legRequest(l);
    const c = req && osrmCache.get(reqKey(req));
    if (!c) return false;
    l.estMin = l.min;
    l.streetM = c.m;
    l.min = c.m / WALK_M_PER_MIN;
    return true;
  }

  function drawItin(it, keepView) {
    routeLayer.clearLayers();
    if (!it) return;
    const bounds = [];
    const darkBase = document.documentElement.dataset.base === "dark";
    const halo = darkBase ? "#111" : "#fff";
    for (const l of it.legs) {
      const g = legGeom(l);
      if (l.type === "walk") {
        L.polyline(g.pts, { color: darkBase ? "#cfd3da" : "#555", weight: 4, dashArray: "4 8", opacity: g.src === "pending" ? 0.45 : 0.9 }).addTo(routeLayer);
        bounds.push(...g.pts);
      } else {
        const p = NET.pats[l.pat];
        const r = NET.routes[p.r];
        L.polyline(g.pts, { color: halo, weight: 9, opacity: 0.85 }).addTo(routeLayer);
        L.polyline(g.pts, { color: r.m === "metro" && r.lc ? r.lc : COLORS[r.m], weight: 5, opacity: g.src === "pending" ? 0.55 : 1 }).addTo(routeLayer);
        p.stops.slice(l.bPos, l.aPos + 1).forEach((s, i, a) => {
          const end = i === 0 || i === a.length - 1;
          L.circleMarker(stopLL(s), { radius: end ? 6 : 3, color: COLORS[r.m], weight: 2, fillColor: "#fff", fillOpacity: 1 })
            .bindTooltip(esc(NET.stops[s].name), { direction: "top" })
            .addTo(routeLayer);
        });
        bounds.push(...g.pts);
      }
    }
    if (bounds.length && !keepView) map.fitBounds(L.latLngBounds(bounds).pad(0.15), { animate: false });
    if (!keepView) refineItin(it);
  }

  // ---------- results UI ----------
  function routeName(r) {
    if (r.m === "metro") return r.l;
    if (r.m === "rail") return r.l;
    if (r.m === "auto") return "Auto " + (r.l || r.s);
    const lbl = MODE_LABEL[r.m];
    return r.s ? lbl + " " + r.s : lbl + " " + r.l;
  }
  function altName(r) { return r.m === "auto" ? r.l : r.s || r.l; }
  function pillName(r) {
    if (r.m === "metro") return r.l.replace(/ \(.*\)/, "");
    if (r.m === "rail") return "Train";
    if (r.m === "auto") return "Auto";
    return (MODE_LABEL[r.m] + " " + (r.s || "")).trim();
  }
  function headwayText(h, mode) {
    if (h >= 90) return "few services a day, check the timetable";
    const est = ["bus", "minibus", "auto"].includes(mode) ? " (estimate)" : "";
    return "every ~" + Math.round(h) + " min" + est;
  }
  function pointName(which) { return sel[which].label; }

  function renderResults() {
    const box = $("results");
    if (!itineraries.length) {
      box.innerHTML = '<div class="card no-res">No route found with the selected modes. Try adding modes, or pick a start or destination nearer a mapped stop (within 1.5 km).</div>';
      routeLayer.clearLayers();
      return;
    }
    box.innerHTML = itineraries.map((it, i) => {
      const changes = Math.max(0, it.rides - 1);
      const meta = it.walkOnly ? "Walk only" : it.rides + (it.rides === 1 ? " ride" : " rides") + ", " + (changes === 0 ? "no changes" : changes + (changes === 1 ? " change" : " changes"));
      const walkTot = it.legs.filter((l) => l.type === "walk").reduce((a, l) => a + l.min, 0);
      const seq = it.legs.map((l) => {
        if (l.type === "walk") return '<span class="pill walk">Walk ' + Math.max(1, Math.round(l.min)) + "</span>";
        const r = NET.routes[NET.pats[l.pat].r];
        const c = r.m === "metro" && r.lc ? r.lc : COLORS[r.m];
        return '<span class="pill" style="background:' + c + '">' + esc(pillName(r)) + (l.alts ? " +" + l.alts.length : "") + "</span>";
      }).join('<span class="arr">&rsaquo;</span>');
      const legs = it.legs.map((l, li) => {
        if (l.type === "walk") {
          const fromN = l.fromPoint ? pointName("from") : NET.stops[l.fromStop].name;
          const toN = l.toPoint ? pointName("to") : NET.stops[l.toStop].name;
          const what = li === 0 ? "Walk to " + esc(toN) : li === it.legs.length - 1 ? "Walk to your destination" : "Walk to " + esc(toN);
          const how = l.streetM !== undefined ? " (" + fmtDist(l.streetM) + " by street)" : "";
          return '<li class="leg walk"><div class="bar"></div><div><div class="lt">' + what + '</div><div class="ld">' + fmtMin(l.min) + how + (li === 0 ? "" : ", from " + esc(fromN)) + "</div></div></li>";
        }
        const p = NET.pats[l.pat];
        const r = NET.routes[p.r];
        const c = r.m === "metro" && r.lc ? r.lc : COLORS[r.m];
        const a = NET.stops[p.stops[l.bPos]].name, b = NET.stops[p.stops[l.aPos]].name;
        const nStops = l.aPos - l.bPos;
        const towards = NET.stops[p.stops[p.stops.length - 1]].name;
        return '<li class="leg" style="--c:' + c + '"><div class="bar"></div><div><div class="lt">Take ' + esc(routeName(r)) + (l.alts ? " or " + esc(l.alts.map((x) => altName(NET.routes[x])).join(", ")) : "") + "</div>" +
          '<div class="ld">' + esc(a) + " to " + esc(b) + " (towards " + esc(towards) + "), " + nStops + (nStops === 1 ? " stop" : " stops") + ", ~" + fmtMin(l.min) +
          ", " + headwayText(l.h, r.m) + ", allow ~" + fmtMin(l.wait) + " wait</div></div></li>";
      }).join("");
      return '<article class="itin' + (i === selIdx ? " sel" : "") + '" data-i="' + i + '" tabindex="0">' +
        '<div class="itin-head"><span class="itin-time">' + fmtMin(it.total) + '</span><span class="itin-meta">' + meta + ", walk " + fmtMin(walkTot) + "</span>" +
        '<div class="itin-seq">' + seq + "</div></div><ol class=\"legs\">" + legs + "</ol></article>";
    }).join("");
    box.querySelectorAll(".itin").forEach((el) => {
      const pick = () => { selIdx = +el.dataset.i; renderResults(); drawItin(itineraries[selIdx]); };
      el.addEventListener("click", pick);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    });
  }

  function runPlan() {
    if (!sel.from || !sel.to) { setStatus("Pick both a start and a destination.", true); return; }
    if (!activeModes.size) { setStatus("Select at least one mode.", true); return; }
    const t0 = performance.now();
    itineraries = plan(sel.from, sel.to, activeModes);
    selIdx = 0;
    renderResults();
    drawItin(itineraries[0]);
    setStatus(itineraries.length ? "Found " + itineraries.length + " option" + (itineraries.length > 1 ? "s" : "") + " in " + Math.round(performance.now() - t0) + " ms. Times are estimates." : "");
    if (window.matchMedia("(max-width: 820px)").matches) $("map").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---------- mode chips ----------
  function renderChips() {
    $("modeChips").innerHTML = MODES.map((m) =>
      '<button type="button" class="chip" style="--c:' + COLORS[m.key] + '" aria-pressed="' + activeModes.has(m.key) + '" data-m="' + m.key + '"><span class="sw"></span>' + m.label + "</button>").join("");
    $("modeChips").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
      const k = b.dataset.m;
      if (activeModes.has(k)) activeModes.delete(k); else activeModes.add(k);
      b.setAttribute("aria-pressed", activeModes.has(k));
    }));
  }
  $("allModes").onclick = () => { MODES.forEach((m) => activeModes.add(m.key)); renderChips(); };
  $("noModes").onclick = () => { activeModes.clear(); renderChips(); };

  // ---------- autocomplete + Nominatim ----------
  // Nominatim usage policy: no as-you-type autocomplete, at most 1 request per second, attribution shown.
  // So place search runs only on an explicit action (Enter or "Search places"), is rate limited and cached.
  const nomCache = new Map();
  let nomLast = 0;
  let nomChain = Promise.resolve();
  function nominatim(q) {
    const key = q.trim().toLowerCase();
    if (nomCache.has(key)) return Promise.resolve(nomCache.get(key));
    nomChain = nomChain.then(async () => {
      if (nomCache.has(key)) return nomCache.get(key);
      const wait = nomLast + 1100 - Date.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      nomLast = Date.now();
      const url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&countrycodes=in&bounded=1&viewbox=" +
        KOLKATA_VIEWBOX + "&accept-language=en&q=" + encodeURIComponent(q);
      const r = await fetch(url, { headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error("Nominatim HTTP " + r.status);
      const js = await r.json();
      const out = js.map((p) => ({ label: p.display_name.split(",").slice(0, 3).join(","), full: p.display_name, lat: +p.lat, lon: +p.lon }));
      nomCache.set(key, out);
      return out;
    });
    return nomChain;
  }

  function matchPlaces(q) {
    q = q.trim().toLowerCase();
    if (q.length < 2) return [];
    const nq = normName(q);
    const res = [];
    for (const p of NET.places) {
      const n = p.name.toLowerCase();
      const i = n.indexOf(q);
      let rank;
      if (i >= 0) rank = i === 0 ? 0 : n[i - 1] === " " || n[i - 1] === "(" ? 1 : 2;
      else if (nq.length >= 3) {
        const j = p.norm.indexOf(nq);
        if (j < 0) continue;
        rank = j === 0 ? 0.5 : 2.5; // other spelling of the same name
      } else continue;
      const score = rank * 1000 - p.modes.size * 20 - p.n + n.length;
      res.push([score, p]);
    }
    res.sort((a, b) => a[0] - b[0]);
    return res.slice(0, 8).map((x) => x[1]);
  }

  function setupAC(which) {
    const input = $(which + "Input"), list = $(which + "List");
    let items = [], active = -1, placeRes = null;
    function close() { list.hidden = true; active = -1; }
    function render() {
      const q = input.value.trim();
      const stops = matchPlaces(q);
      items = [];
      let html = "";
      stops.forEach((p) => {
        items.push({ kind: "stop", p });
        html += '<li role="option" data-k="' + (items.length - 1) + '"><span class="nm">' + esc(p.name) + '</span><span class="mini-modes">' +
          [...p.modes].map((m) => '<span class="mm" title="' + MODE_LABEL[m] + '" style="background:' + COLORS[m] + '"></span>').join("") + "</span></li>";
      });
      if (placeRes) {
        if (!placeRes.length) html += '<li class="note">No places found in the Kolkata area.</li>';
        placeRes.forEach((p) => {
          items.push({ kind: "place", p });
          html += '<li role="option" class="place" data-k="' + (items.length - 1) + '" title="' + esc(p.full) + '"><span class="nm">' + esc(p.label) + "</span></li>";
        });
        if (placeRes.length) html += '<li class="note">Search by OpenStreetMap Nominatim</li>';
      } else if (q.length >= 3) {
        items.push({ kind: "search" });
        html += '<li role="option" class="search" data-k="' + (items.length - 1) + '">Search places for "' + esc(q) + '"</li>';
      }
      list.innerHTML = html;
      list.hidden = !html;
      highlight();
    }
    function highlight() { list.querySelectorAll("li[data-k]").forEach((li) => li.classList.toggle("active", +li.dataset.k === active)); }
    async function doSearch() {
      const q = input.value.trim();
      if (q.length < 3) return;
      list.innerHTML = '<li class="note">Searching OpenStreetMap Nominatim...</li>';
      list.hidden = false;
      try { placeRes = await nominatim(q); } catch (e) { placeRes = []; setStatus("Place search failed: " + e.message, true); }
      if (input.value.trim() === q) render();
    }
    function choose(it) {
      if (!it) return;
      if (it.kind === "search") { doSearch(); return; }
      const p = it.p;
      setPoint(which, { lat: p.lat, lon: p.lon, label: it.kind === "stop" ? p.name : p.label });
      close();
    }
    input.addEventListener("input", () => { placeRes = null; sel[which] = null; if (markers[which]) { map.removeLayer(markers[which]); markers[which] = null; } render(); });
    input.addEventListener("focus", () => { if (input.value.trim().length >= 2 && !sel[which]) render(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(items.length - 1, active + 1); highlight(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(-1, active - 1); highlight(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (!list.hidden && active >= 0) choose(items[active]);
        else if (!list.hidden && !placeRes && input.value.trim().length >= 3) doSearch();
        else if (sel.from && sel.to) runPlan();
      } else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", (e) => e.preventDefault());
    list.addEventListener("click", (e) => { const li = e.target.closest("li[data-k]"); if (li) choose(items[+li.dataset.k]); });
    input.addEventListener("blur", () => setTimeout(close, 150));
  }

  // ---------- wiring ----------
  $("swapBtn").onclick = () => {
    const f = sel.from, t = sel.to;
    const fv = $("fromInput").value, tv = $("toInput").value;
    setPoint("from", t, true); setPoint("to", f, true);
    $("fromInput").value = tv; $("toInput").value = fv;
  };
  $("locBtn").onclick = () => {
    if (!navigator.geolocation) { setStatus("Location is not available in this browser.", true); return; }
    setStatus("Finding your location...");
    navigator.geolocation.getCurrentPosition(
      (pos) => { setPoint("from", { lat: pos.coords.latitude, lon: pos.coords.longitude, label: "My location" }); setStatus(""); },
      (err) => setStatus("Could not get your location: " + err.message, true),
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };
  $("tripForm").addEventListener("submit", (e) => { e.preventDefault(); runPlan(); });

  // test hook: set points by name without UI typing
  window.kolPlanner = {
    pickStop(which, name, mode) {
      const ok = (x) => !mode || x.modes.has(mode);
      const p = NET.places.find((x) => x.name.toLowerCase() === name.toLowerCase() && ok(x)) || matchPlaces(name).find(ok);
      if (!p) return null;
      setPoint(which, { lat: p.lat, lon: p.lon, label: p.name });
      return p.name;
    },
    setModes(list) { activeModes.clear(); list.forEach((m) => activeModes.add(m)); renderChips(); },
    setPoint(which, lat, lon, label) { setPoint(which, { lat, lon, label: label || "Test point" }); return true; },
    select(i) { selIdx = i; renderResults(); drawItin(itineraries[selIdx]); },
    geom() {
      const it = itineraries[selIdx];
      if (!it) return [];
      return it.legs.map((l) => { const g = legGeom(l); return (l.type === "walk" ? "walk" : NET.pats[l.pat].mode) + ":" + g.src + ":" + g.pts.length; });
    },
    geomPts(li) { const it = itineraries[selIdx]; return it ? legGeom(it.legs[li]).pts.map((p) => [+p[0].toFixed(5), +p[1].toFixed(5)]) : []; },
    base: () => baseName,
    osrmRequests: () => osrmCount,
    noWalk: () => [...NET.noWalk].map((s) => NET.stops[s].name),
    tune(o) { if (o.walkReluctance !== undefined) WALK_RELUCTANCE = o.walkReluctance; if (o.detourFree !== undefined) DETOUR_FREE = o.detourFree; if (o.detourPerKm !== undefined) DETOUR_MIN_PER_KM = o.detourPerKm; return [WALK_RELUCTANCE, DETOUR_FREE, DETOUR_MIN_PER_KM]; },
    scores: () => itineraries.map((it) => ({ total: Math.round(it.total), score: Math.round(scoreOf(it)), detour: Math.round(detourPenalty(it.legs)), pathKm: +(pathM(it.legs) / 1000).toFixed(1), sel: itineraries[selIdx] === it })),
    plan: runPlan,
    summary() {
      return itineraries.map((it) => ({
        total: Math.round(it.total), rides: it.rides,
        legs: it.legs.map((l) => l.type === "walk" ? "walk " + Math.round(l.min) : (pillName(NET.routes[NET.pats[l.pat].r]) + (l.alts ? "/" + l.alts.map((x) => altName(NET.routes[x])).join("/") : "") + " " + NET.stops[NET.pats[l.pat].stops[l.bPos]].name + ">" + NET.stops[NET.pats[l.pat].stops[l.aPos]].name + " " + Math.round(l.min) + "m+w" + Math.round(l.wait))),
      }));
    },
  };

  loadNetwork().then(() => {
    renderChips();
    setupAC("from");
    setupAC("to");
    $("goBtn").disabled = false;
    $("goBtn").textContent = "Get directions";
  }).catch((e) => {
    $("goBtn").textContent = "Could not load network";
    setStatus("Could not load the network data: " + e.message, true);
  });
})();
