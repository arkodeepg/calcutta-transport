/* Kolkata trip planner, proof of concept.
 * Loads data/network.json (built by scripts/build_web.py) and routes entirely in the browser.
 * Routing: round-based search (RAPTOR style) over route patterns with frequency-based boarding cost,
 * up to MAX_RIDES rides, with walking for access, egress and transfers.
 */
(function () {
  "use strict";

  // ---------- settings ----------
  const WALK_M_PER_MIN = 75;          // 4.5 km/h
  const WALK_DETOUR = 1.2;            // straight line to street distance
  const ACCESS_M = 1500;              // access and egress walk radius
  const TRANSFER_M = 600;             // stop to stop transfer walk radius
  const WAIT_CAP = 15;                // expected wait = half headway, capped
  const BOARD_PENALTY = 3;            // per boarding, minutes (preference, not counted as travel time)
  const MAX_RIDES = 4;
  const DIVERSITY_PENALTY = 20;       // minutes added per boarding of a route used by an earlier option
  const WALK_ONLY_MAX = 40;           // show a walk-only option up to this many minutes
  const KOLKATA_VIEWBOX = "88.05,22.85,88.65,22.30"; // left,top,right,bottom

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
  const NET = { stops: [], lat: null, lon: null, routes: [], pats: [], stopPats: [], foot: [], grid: new Map(), places: [] };
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
  const fmtMin = (x) => {
    const m = Math.max(1, Math.round(x));
    if (m < 60) return m + " min";
    return Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0") + " min";
  };
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
    NET.foot = Array.from({ length: n }, () => new Map());
    for (let s = 0; s < n; s++) {
      for (const [t, d] of nearbyStops(NET.lat[s], NET.lon[s], TRANSFER_M)) {
        if (t !== s) NET.foot[s].set(t, walkMin(d));
      }
    }
    for (const [a, b, m] of net.transfers) NET.foot[a].set(b, Math.max(m, NET.foot[a].get(b) || 0));
    // places for autocomplete: group same-name stops within 1 km
    const byName = new Map();
    NET.stops.forEach((s, i) => {
      const key = s.name.toLowerCase();
      if (!byName.has(key)) byName.set(key, []);
      const groups = byName.get(key);
      let g = groups.find((g) => distM(g.lat, g.lon, NET.lat[i], NET.lon[i]) < 1000);
      if (!g) { g = { name: s.name, lat: NET.lat[i], lon: NET.lon[i], modes: new Set(), n: 0 }; groups.push(g); }
      s.modes.forEach((m) => g.modes.add(m));
      g.n++;
    });
    for (const groups of byName.values()) for (const g of groups) NET.places.push(g);
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
    const access = nearbyStops(from.lat, from.lon, ACCESS_M);
    const egress = nearbyStops(to.lat, to.lon, ACCESS_M);
    let marked = new Set();
    for (const [s, d] of access) {
      arr[0][s] = walkMin(d);
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
          const v = arrR[k][s] + w;
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
        const v = arr[k][s] + walkMin(d);
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
          legs.unshift({ type: "walk", fromPoint: "from", toStop: s, m: distM(from.lat, from.lon, NET.lat[s], NET.lon[s]) });
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
  const rideRoutes = (legs) => legs.filter((l) => l.type === "ride").map((l) => NET.pats[l.pat].r);
  const signature = (legs) => legs.filter((l) => l.type === "ride").map((l) => NET.pats[l.pat].r + "@" + NET.pats[l.pat].stops[l.bPos] + ">" + NET.pats[l.pat].stops[l.aPos]).join("|");
  const stopSig = (legs) => legs.filter((l) => l.type === "ride").map((l) => { const p = NET.pats[l.pat]; return p.mode + "@" + p.stops[l.bPos] + ">" + p.stops[l.aPos]; }).join("|");
  const routeSig = (legs) => rideRoutes(legs).join("|");

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
    const directM = distM(from.lat, from.lon, to.lat, to.lon);
    const walkOnly = walkMin(directM);
    let list = cands.sort((a, b) => a.total - b.total);
    if (list.length) list = list.filter((c) => c.total <= list[0].total * 2 + 15);
    list = list.slice(0, 3);
    if (walkOnly <= WALK_ONLY_MAX || (!list.length && walkOnly <= 120)) {
      list.push({ legs: [{ type: "walk", fromPoint: "from", toPoint: "to", min: walkOnly }], total: walkOnly, rides: 0, walkOnly: true });
    }
    list.sort((a, b) => a.total - b.total);
    return list;
  }

  // ---------- map ----------
  const map = L.map("map", { zoomControl: true, preferCanvas: true }).setView([22.5726, 88.3639], 12);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
  }).addTo(map);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
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

  function drawItin(it) {
    routeLayer.clearLayers();
    if (!it) return;
    const bounds = [];
    const ptLL = (which) => [sel[which].lat, sel[which].lon];
    for (const l of it.legs) {
      if (l.type === "walk") {
        const a = l.fromPoint ? ptLL("from") : stopLL(l.fromStop);
        const b = l.toPoint ? ptLL("to") : stopLL(l.toStop);
        L.polyline([a, b], { color: "#555", weight: 4, dashArray: "4 8", opacity: 0.9 }).addTo(routeLayer);
        bounds.push(a, b);
      } else {
        const p = NET.pats[l.pat];
        const r = NET.routes[p.r];
        const pts = p.stops.slice(l.bPos, l.aPos + 1).map(stopLL);
        L.polyline(pts, { color: "#fff", weight: 9, opacity: 0.85 }).addTo(routeLayer);
        L.polyline(pts, { color: r.m === "metro" && r.lc ? r.lc : COLORS[r.m], weight: 5, opacity: 1 }).addTo(routeLayer);
        p.stops.slice(l.bPos, l.aPos + 1).forEach((s, i, a) => {
          const end = i === 0 || i === a.length - 1;
          L.circleMarker(stopLL(s), { radius: end ? 6 : 3, color: COLORS[r.m], weight: 2, fillColor: "#fff", fillOpacity: 1 })
            .bindTooltip(esc(NET.stops[s].name), { direction: "top" })
            .addTo(routeLayer);
        });
        bounds.push(...pts);
      }
    }
    if (bounds.length) map.fitBounds(L.latLngBounds(bounds).pad(0.15), { animate: false });
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
          return '<li class="leg walk"><div class="bar"></div><div><div class="lt">' + what + '</div><div class="ld">' + fmtMin(l.min) + (li === 0 ? "" : ", from " + esc(fromN)) + "</div></div></li>";
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
    const res = [];
    for (const p of NET.places) {
      const n = p.name.toLowerCase();
      const i = n.indexOf(q);
      if (i < 0) continue;
      const score = (i === 0 ? 0 : n[i - 1] === " " || n[i - 1] === "(" ? 1 : 2) * 1000 - p.modes.size * 20 - p.n + n.length;
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
