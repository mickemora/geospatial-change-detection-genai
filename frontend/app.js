(() => {
  "use strict";

  const API = ((window.APP_CONFIG && window.APP_CONFIG.apiUrl) || "").replace(/\/$/, "");
  const STEPS = ["QUEUED", "SEARCHING", "DETECTING", "REPORTING", "COMPLETE"];
  const POLL_MS = 4000;

  // Well-documented civilian construction projects (approximate extents).
  const SAMPLES = [
    { label: "Gigafactory Texas, Austin, TX", bbox: [-97.645, 30.200, -97.595, 30.245],
      before: "2020-05-01", after: "2022-06-01" },
    { label: "Western Sydney International Airport, NSW", bbox: [150.69, -33.92, 150.75, -33.87],
      before: "2018-06-01", after: "2023-06-01" },
  ];

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- Map ----------------------------------------------------------------
  const map = L.map("map", { zoomControl: false }).setView([30.2, -97.6], 4);
  L.control.zoom({ position: "topright" }).addTo(map);
  const basemap = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Imagery © Esri, Maxar, Earthstar Geographics | Analysis: Copernicus Sentinel-2" }
  ).addTo(map);

  let aoi = null;          // [minLon, minLat, maxLon, maxLat]
  let aoiRect = null;
  let drawing = null;      // { first: LatLng, preview: Rectangle }
  let pollTimer = null;
  const layers = { before: null, after: null, change: null };

  function setAoi(bbox) {
    aoi = bbox.map((v) => Number(v.toFixed(5)));
    const bounds = [[aoi[1], aoi[0]], [aoi[3], aoi[2]]];
    if (aoiRect) aoiRect.setBounds(bounds);
    else aoiRect = L.rectangle(bounds, { color: "#ff7a45", weight: 2, fill: false, dashArray: "6 4" }).addTo(map);
    const [w, h] = sizeKm(aoi);
    $("aoi-size").textContent = `${w.toFixed(1)} × ${h.toFixed(1)} km`;
    updateRunState();
  }

  function sizeKm([minLon, minLat, maxLon, maxLat]) {
    const mid = ((minLat + maxLat) / 2) * Math.PI / 180;
    return [(maxLon - minLon) * 111.32 * Math.cos(mid), (maxLat - minLat) * 110.57];
  }

  function toBbox(a, b) {
    return [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)];
  }

  // Two-click rectangle drawing (works the same with mouse and touch).
  $("draw").addEventListener("click", () => {
    if (drawing) return stopDrawing();
    drawing = { first: null, preview: null };
    $("draw").classList.add("active");
    map.getContainer().style.cursor = "crosshair";
  });

  function stopDrawing() {
    if (drawing && drawing.preview) drawing.preview.remove();
    drawing = null;
    $("draw").classList.remove("active");
    map.getContainer().style.cursor = "";
  }

  map.on("click", (e) => {
    if (!drawing) return;
    if (!drawing.first) {
      drawing.first = e.latlng;
      drawing.preview = L.rectangle([e.latlng, e.latlng], { color: "#ff7a45", weight: 1, fillOpacity: 0.08 }).addTo(map);
      return;
    }
    setAoi(toBbox(drawing.first, e.latlng));
    stopDrawing();
  });

  map.on("mousemove", (e) => {
    if (drawing && drawing.first) drawing.preview.setBounds([drawing.first, e.latlng]);
  });

  $("use-view").addEventListener("click", () => {
    const b = map.getBounds();
    setAoi([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
  });

  // ---- Form -----------------------------------------------------------------
  SAMPLES.forEach((s, i) => $("sample").add(new Option(s.label, String(i))));
  $("sample").addEventListener("change", (e) => {
    const s = SAMPLES[Number(e.target.value)];
    if (!s) return;
    setAoi(s.bbox);
    $("before").value = s.before;
    $("after").value = s.after;
    $("label").value = s.label;
    map.fitBounds([[s.bbox[1], s.bbox[0]], [s.bbox[3], s.bbox[2]]], { padding: [40, 40] });
    updateRunState();
  });

  ["before", "after"].forEach((id) => $(id).addEventListener("input", updateRunState));
  $("after").max = new Date().toISOString().slice(0, 10);

  function updateRunState() {
    let problem = "";
    if (aoi) {
      const [w, h] = sizeKm(aoi);
      if (Math.min(w, h) < 0.5) problem = "Area is too small (min 0.5 km per side).";
      else if (Math.max(w, h) > 15) problem = "Area is too large (max 15 km per side). Zoom in or draw a smaller box.";
    }
    $("error").textContent = problem;
    $("run").disabled = !aoi || !$("before").value || !$("after").value || !!problem || !API;
    if (!API) $("error").textContent = "API URL missing: config.js is written at deploy time.";
  }

  $("run").addEventListener("click", async () => {
    $("run").disabled = true;
    $("error").textContent = "";
    try {
      const res = await api("/analyses", {
        method: "POST",
        body: JSON.stringify({ bbox: aoi, before_date: $("before").value, after_date: $("after").value, label: $("label").value }),
      });
      track(res.analysis_id);
      loadRecent();
    } catch (err) {
      $("error").textContent = err.message;
      updateRunState();
    }
  });

  $("collapse").addEventListener("click", () => {
    const collapsed = $("panel").classList.toggle("collapsed");
    $("collapse").textContent = collapsed ? "+" : "–";
  });

  // ---- API ----------------------------------------------------------------
  async function api(path, options = {}) {
    const res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...options });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
    return body;
  }

  function track(id) {
    clearTimeout(pollTimer);
    clearResult();
    $("progress").classList.remove("hidden");
    const poll = async () => {
      try {
        const a = await api(`/analyses/${encodeURIComponent(id)}`);
        renderSteps(a.status);
        if (a.bbox && (!aoi || aoi.join() !== a.bbox.join())) setAoi(a.bbox);
        if (a.status === "COMPLETE") { renderResult(a); updateRunState(); loadRecent(); return; }
        if (a.status === "FAILED") { $("error").textContent = failureMessage(a.error_message); updateRunState(); loadRecent(); return; }
        pollTimer = setTimeout(poll, POLL_MS);
      } catch (err) {
        $("error").textContent = err.message;
        pollTimer = setTimeout(poll, POLL_MS * 2);
      }
    };
    renderSteps("QUEUED");
    poll();
  }

  function failureMessage(raw) {
    try {
      const err = JSON.parse(raw);
      const cause = JSON.parse(err.Cause || "{}");
      return `Analysis failed: ${cause.errorMessage || cause.StoppedReason || err.Error || "unknown error"}`;
    } catch {
      return "Analysis failed.";
    }
  }

  let lastStep = 0;
  function renderSteps(status) {
    const failed = status === "FAILED";
    const idx = failed ? lastStep : STEPS.indexOf(status);
    if (!failed) lastStep = Math.max(idx, 0);
    document.querySelectorAll("#steps li").forEach((li, i) => {
      li.className = "";
      if (i < idx || status === "COMPLETE") li.classList.add("done");
      else if (i === idx) li.classList.add(failed ? "failed" : "active");
    });
  }

  // ---- Results --------------------------------------------------------------
  function clearResult() {
    Object.keys(layers).forEach((k) => { if (layers[k]) { layers[k].remove(); layers[k] = null; } });
    $("result").classList.add("hidden");
  }

  function renderResult(a) {
    clearResult();
    const stats = a.stats || {};
    const bounds = stats.overlay_bounds || [[a.bbox[1], a.bbox[0]], [a.bbox[3], a.bbox[2]]];
    layers.before = L.imageOverlay(a.images.before, bounds);
    layers.after = L.imageOverlay(a.images.after, bounds);
    layers.change = L.imageOverlay(a.images.change, bounds, { opacity: Number($("opacity").value) }).addTo(map);
    showLayer("after");
    map.fitBounds(bounds, { padding: [40, 40] });

    $("metrics").innerHTML = [
      [fmt(stats.changed_area_km2, 2) + " km²", "changed area"],
      [fmt(stats.changed_pct_of_valid, 1) + "%", "of clear area"],
      [Math.round((stats.valid_fraction || 0) * 100) + "%", "cloud-free"],
    ].map(([v, l]) => `<div class="metric"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("");

    $("report").innerHTML = renderReport(a.report || {}, stats);
    const sc = a.scenes || {};
    $("scenes").innerHTML = ["before", "after"].filter((k) => sc[k]).map((k) =>
      `${k.toUpperCase()}: ${esc(sc[k].id)} · ${esc((sc[k].datetime || "").slice(0, 10))} · ${esc(fmt(sc[k].cloud_cover, 1))}% cloud`
    ).join("<br>");
    $("result").classList.remove("hidden");
  }

  function renderReport(r, stats) {
    const conf = String(r.overall_confidence || "").toLowerCase();
    const list = (items) => (items && items.length) ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "";
    const observations = (r.observations || []).map((o) =>
      `<li><b>${esc(o.change)}</b> <span class="badge ${esc(String(o.confidence || "").toLowerCase())}">${esc(o.confidence)}</span><br>` +
      `<span class="muted">${esc(o.location)}</span> — ${esc(o.likely_explanation)}</li>`).join("");
    return `
      <h3>${esc(r.headline)}</h3>
      ${conf ? `<p><span class="badge ${esc(conf)}">${esc(conf)} confidence</span>${stats.low_confidence ? ' <span class="badge low">limited clear pixels</span>' : ""}</p>` : ""}
      <p>${esc(r.summary)}</p>
      ${observations ? `<h2>Observations</h2><ul>${observations}</ul>` : ""}
      ${r.possible_false_positives && r.possible_false_positives.length ? `<h2>Possible false positives</h2>${list(r.possible_false_positives)}` : ""}
      ${r.recommended_follow_up && r.recommended_follow_up.length ? `<h2>Recommended follow-up</h2>${list(r.recommended_follow_up)}` : ""}
      <p class="muted" style="font-size:11px">AI-generated from 10 m imagery and spectral statistics. Verify before relying on it.</p>`;
  }

  function showLayer(name) {
    document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.layer === name));
    ["before", "after"].forEach((k) => layers[k] && layers[k].remove());
    if (name === "before" || name === "after") layers[name] && layers[name].addTo(map);
    if (layers.change) layers.change.bringToFront();
  }

  document.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => showLayer(b.dataset.layer)));
  $("opacity").addEventListener("input", (e) => layers.change && layers.change.setOpacity(Number(e.target.value)));

  const fmt = (v, d) => (typeof v === "number" ? v.toFixed(d) : "–");

  // ---- Recent -------------------------------------------------------------
  async function loadRecent() {
    if (!API) { $("recent").innerHTML = '<li class="muted">Not connected to an API.</li>'; return; }
    try {
      const { analyses } = await api("/analyses");
      $("recent").innerHTML = analyses.length ? analyses.map((a) => `
        <li data-id="${esc(a.analysis_id)}">
          <span class="recent__title">${esc(a.label || a.headline || "Untitled area")}</span>
          <span class="recent__meta">${esc(a.before_date)} → ${esc(a.after_date)} · ${esc(a.status)}</span>
        </li>`).join("") : '<li class="muted">No analyses yet.</li>';
      $("recent").querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => track(li.dataset.id)));
    } catch {
      $("recent").innerHTML = '<li class="muted">Could not load analyses.</li>';
    }
  }

  updateRunState();
  loadRecent();
})();
