// Map tab: a draggable 3D globe (d3-geo orthographic projection on a canvas). Countries are shaded by how many of
// your matching jobs are there; clicking one lists its live roles and its biggest tech employers, with links.
// Loaded only when the Map tab opens. Untrusted text is only ever set via textContent (helpers from app.js).
"use strict";
(() => {
  const ALIASES = { usa: "840", "united states": "840", us: "840", uk: "826", "united kingdom": "826", "great britain": "826",
    uae: "784", "united arab emirates": "784", czechia: "203", "czech republic": "203", "south korea": "410", korea: "410",
    "saudi arabia": "682", russia: "643", vietnam: "704", "hong kong": "344", taiwan: "158", iran: "364", netherlands: "528",
    "the netherlands": "528", turkey: "792", "türkiye": "792", india: "356" };
  // Industries most likely to hire DevOps/SRE/platform engineers come first.
  const INDUSTRY_RANK = { cloud: 0, software: 1, "IT services": 2, internet: 3, semiconductors: 4, "e-commerce": 5, fintech: 6,
    "video games": 7, telecom: 8, consulting: 9, "banking & finance": 10 };
  let state = null;

  const norm = (s) => String(s || "").toLowerCase().replace(/\b(inc|llc|ltd|limited|pvt|private|corp|corporation|gmbh|group|plc|s\.?a\.?|ag|technologies|technology|india)\b/g, "")
    .replace(/[^a-z0-9]+/g, " ").trim();
  const flag = (iso2) => (iso2 && /^[A-Z]{2}$/.test(iso2) ? String.fromCodePoint(...[...iso2].map((c) => 0x1f1a5 + c.charCodeAt(0))) : "🌐");
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function countryKeyFor(job, byName) {
    if (job.region === "india") return "356";
    const c = String(job.country || "").toLowerCase().trim();
    return ALIASES[c] || byName.get(c) || null;
  }

  async function mount(host, ctx) {
    unmount();
    const { el, extLink, fmtInt } = ctx;
    const topo = await (await fetch("vendor/countries-50m.json")).json();
    const features = topojson.feature(topo, topo.objects.countries).features;
    const keyOf = (f) => (f.id != null && f.id !== "" ? String(f.id) : f.properties.name);
    const world = ctx.world.countries || {};
    const nameOf = (k, f) => world[k]?.name || f?.properties?.name || k;
    const byName = new Map();
    for (const f of features) byName.set(nameOf(keyOf(f), f).toLowerCase(), keyOf(f));
    for (const [k, v] of Object.entries(world)) { byName.set(String(v.name || "").toLowerCase(), k); if (v.iso2) byName.set(v.iso2.toLowerCase(), k); }

    // Your data per country: open matching jobs and applications.
    const stats = new Map();
    for (const j of ctx.jobs) {
      const k = countryKeyFor(j, byName);
      if (!k) continue;
      const s = stats.get(k) || { open: [], applied: 0 };
      if (ctx.statusOf(j) === "new") s.open.push(j); else s.applied += 1;
      stats.set(k, s);
    }
    const maxOpen = Math.max(1, ...[...stats.values()].map((s) => s.open.length));

    // ---------------------------------------------------------------- layout
    const canvas = el("canvas", { class: "globe", role: "img", "aria-label": "Globe of countries; use the country search to choose one with the keyboard" });
    const tip = el("div", { class: "globe-tip hidden" });
    const search = el("input", { type: "search", class: "search", list: "jr-countries", placeholder: "Find a country…", "aria-label": "Find a country" });
    const options = el("datalist", { id: "jr-countries" }, features.map((f) => nameOf(keyOf(f), f)).sort().map((n) => el("option", { value: n })));
    const legend = el("div", { class: "legend globe-legend" }, el("span", {}, el("i", { class: "lg hot" }), "many matching jobs"),
      el("span", {}, el("i", { class: "lg warm" }), "some"), el("span", {}, el("i", { class: "lg land" }), "none yet"));
    const panel = el("div", { class: "country-panel" });
    host.replaceChildren(el("div", { class: "map-layout" },
      el("div", { class: "globe-wrap" }, el("div", { class: "globe-tools" }, search, options,
        el("button", { class: "btn small ghost", type: "button", onclick: () => { select(null); spin = true; } }, "Reset")), canvas, tip, legend),
      panel));

    // ---------------------------------------------------------------- globe
    const g = canvas.getContext("2d");
    const projection = d3.geoOrthographic().clipAngle(90).precision(0.6).rotate([-78, -20]);
    const path = d3.geoPath(projection, g);
    const graticule = d3.geoGraticule10();
    let size = 0, scale = 1, selected = null, hovered = null, spin = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0, anim = null;
    const colors = {};
    function readColors() { for (const n of ["--ocean", "--ocean-2", "--land", "--land-line", "--heat-1", "--heat-2", "--heat-3", "--selected", "--ink"]) colors[n] = css(n); }
    function resize() {
      const w = Math.max(200, Math.min(canvas.parentElement.clientWidth - 28, 760)), dpr = Math.min(2, devicePixelRatio || 1); // minus padding
      size = w;
      canvas.width = w * dpr; canvas.height = w * dpr;
      canvas.style.width = `${w}px`; canvas.style.height = `${w}px`;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      projection.translate([w / 2, w / 2]).scale((w / 2 - 6) * scale);
      draw();
    }
    function heat(k) {
      const n = stats.get(k)?.open.length || 0;
      if (!n) return colors["--land"];
      const t = Math.sqrt(n / maxOpen);
      return t > 0.66 ? colors["--heat-3"] : t > 0.33 ? colors["--heat-2"] : colors["--heat-1"];
    }
    function draw() {
      g.clearRect(0, 0, size, size);
      const r = projection.scale(), [cx, cy] = projection.translate();
      const grad = g.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
      grad.addColorStop(0, colors["--ocean-2"]); grad.addColorStop(1, colors["--ocean"]);
      g.beginPath(); path({ type: "Sphere" }); g.fillStyle = grad; g.fill();
      g.beginPath(); path(graticule); g.strokeStyle = "rgba(255,255,255,.07)"; g.lineWidth = 0.6; g.stroke();
      for (const f of features) {
        const k = keyOf(f);
        g.beginPath(); path(f);
        g.fillStyle = k === selected ? colors["--selected"] : heat(k);
        g.fill();
        g.strokeStyle = k === selected ? "#fff" : k === hovered ? "rgba(255,255,255,.85)" : colors["--land-line"];
        g.lineWidth = k === selected ? 1.6 : k === hovered ? 1.1 : 0.4;
        g.stroke();
      }
      g.beginPath(); path({ type: "Sphere" }); g.strokeStyle = "rgba(255,255,255,.18)"; g.lineWidth = 1; g.stroke();
    }
    function loop() {
      if (anim) {
        const k = Math.min(1, (performance.now() - anim.t0) / anim.ms), e = 1 - (1 - k) ** 3;
        projection.rotate([anim.from[0] + (anim.to[0] - anim.from[0]) * e, anim.from[1] + (anim.to[1] - anim.from[1]) * e]);
        if (k === 1) anim = null;
        draw();
      } else if (spin && !dragging) {
        const [l, p] = projection.rotate();
        projection.rotate([l + 0.12, p]);
        draw();
      }
      raf = requestAnimationFrame(loop);
    }
    function featureAt(evt) {
      const rect = canvas.getBoundingClientRect();
      const ll = projection.invert([evt.clientX - rect.left, evt.clientY - rect.top]);
      if (!ll || d3.geoDistance(ll, [-projection.rotate()[0], -projection.rotate()[1]]) > Math.PI / 2) return null;
      return features.find((f) => d3.geoContains(f, ll)) || null;
    }
    let dragging = false, moved = 0, last = null;
    canvas.addEventListener("pointerdown", (e) => { dragging = true; moved = 0; last = [e.clientX, e.clientY]; spin = false; anim = null; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      if (dragging && last) {
        const dx = e.clientX - last[0], dy = e.clientY - last[1];
        moved += Math.abs(dx) + Math.abs(dy);
        last = [e.clientX, e.clientY];
        const [l, p] = projection.rotate(), k = 70 / projection.scale();
        projection.rotate([l + dx * k, Math.max(-85, Math.min(85, p - dy * k))]);
        draw();
        return;
      }
      const f = featureAt(e);
      const k = f ? keyOf(f) : null;
      if (k !== hovered) { hovered = k; draw(); }
      if (f) {
        const s = stats.get(k);
        tip.textContent = `${flag(world[k]?.iso2)} ${nameOf(k, f)}${s?.open.length ? ` · ${s.open.length} matching job${s.open.length > 1 ? "s" : ""}` : ""}`;
        const rect = canvas.getBoundingClientRect();
        tip.style.left = `${e.clientX - rect.left + 14}px`; tip.style.top = `${e.clientY - rect.top + 10}px`;
        tip.classList.remove("hidden");
      } else tip.classList.add("hidden");
    });
    const end = (e) => {
      if (dragging && moved < 5) { const f = featureAt(e); if (f) select(keyOf(f)); }
      dragging = false; last = null;
    };
    canvas.addEventListener("pointerup", end);
    canvas.addEventListener("pointerleave", () => { hovered = null; tip.classList.add("hidden"); draw(); });
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      scale = Math.max(0.8, Math.min(8, scale * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      projection.scale((size / 2 - 6) * scale);
      draw();
    }, { passive: false });
    search.addEventListener("change", () => {
      const k = byName.get(search.value.trim().toLowerCase());
      if (k) select(k);
    });
    function flyTo(k) {
      const f = features.find((x) => keyOf(x) === k);
      if (!f) return;
      const [lon, lat] = d3.geoCentroid(f), from = projection.rotate();
      let to0 = -lon;
      while (to0 - from[0] > 180) to0 -= 360;
      while (to0 - from[0] < -180) to0 += 360;
      anim = { from: [from[0], from[1]], to: [to0, Math.max(-60, Math.min(60, -lat))], t0: performance.now(), ms: 750 };
      const area = d3.geoArea(f);
      scale = area < 0.0005 ? 4 : area < 0.005 ? 2.4 : area < 0.03 ? 1.5 : 1;
      projection.scale((size / 2 - 6) * scale);
    }
    function select(k) {
      selected = k;
      spin = false;
      if (k) flyTo(k); else { scale = 1; projection.scale((size / 2 - 6) * scale); }
      renderPanel();
      draw();
    }

    // ---------------------------------------------------------------- side panel
    function renderPanel() {
      if (!selected) {
        const top = [...stats.entries()].filter(([, s]) => s.open.length).sort((a, b) => b[1].open.length - a[1].open.length).slice(0, 12);
        panel.replaceChildren(el("h2", {}, "Where your jobs are"),
          el("p", { class: "sub" }, "Click a country on the globe, or pick one in the search box. Every country is listed, even the tiny ones."),
          top.length ? el("ul", { class: "country-rank" }, top.map(([k, s]) => el("li", {},
            el("button", { type: "button", onclick: () => select(k) }, `${flag(world[k]?.iso2)} ${world[k]?.name || k}`),
            el("span", { class: "count-pill" }, `${s.open.length} open`)))) : el("div", { class: "empty" }, "No matching jobs with a known country yet."));
        return;
      }
      const c = world[selected] || { name: nameOf(selected, features.find((f) => keyOf(f) === selected)), companies: [] };
      const s = stats.get(selected) || { open: [], applied: 0 };
      // Live roles grouped by company.
      const byCo = new Map();
      for (const j of s.open) { const k = norm(j.company); const x = byCo.get(k) || { name: j.company, jobs: [] }; x.jobs.push(j); byCo.set(k, x); }
      const groups = [...byCo.values()].sort((a, b) => Math.max(...b.jobs.map((j) => j.prio || 0)) - Math.max(...a.jobs.map((j) => j.prio || 0)));
      const hiring = new Set(byCo.keys());
      const employers = [...(c.companies || [])].sort((a, b) =>
        (hiring.has(norm(b.name)) - hiring.has(norm(a.name))) || ((INDUSTRY_RANK[a.industry] ?? 11) - (INDUSTRY_RANK[b.industry] ?? 11)) || ((b.employees || 0) - (a.employees || 0)));
      const country = c.name || "";
      panel.replaceChildren(
        el("div", { class: "cp-head" }, el("span", { class: "cp-flag" }, flag(c.iso2)), el("div", {}, el("h2", {}, country),
          el("div", { class: "sub" }, `${fmtInt(s.open.length)} matching open jobs · ${fmtInt(s.applied)} applications · ${fmtInt((c.companies || []).length)} major employers`))),
        el("h3", {}, "Hiring for your roles now"),
        groups.length ? el("ul", { class: "co-list" }, groups.slice(0, 15).map((gp) => el("li", {},
          el("div", { class: "co-name" }, gp.name, el("span", { class: "chip have" }, `${gp.jobs.length} role${gp.jobs.length > 1 ? "s" : ""}`)),
          el("div", { class: "co-jobs" }, gp.jobs.slice(0, 4).map((j) => el("button", { type: "button", class: "btn small ghost", onclick: () => ctx.openJob(j) },
            `${j.title}${j.score != null ? ` · fit ${Number(j.score).toFixed(1)}` : ""}`)))))) :
          el("p", { class: "sub" }, "No live matching roles here right now. The employers below are where to look."),
        el("h3", {}, "Top employers for DevOps / cloud roles"),
        el("p", { class: "sub" }, "Largest companies in software, cloud, IT services, internet, telecom, fintech and banking (Wikidata). Tech first; ✓ = hiring for your roles now."),
        employers.length ? el("ul", { class: "co-list" }, employers.map((e) => el("li", {},
          el("div", { class: "co-name" }, hiring.has(norm(e.name)) ? el("span", { class: "chip have" }, "✓ hiring") : null, e.name,
            el("span", { class: "chip" }, e.industry), e.employees ? el("span", { class: "sub" }, `${fmtInt(e.employees)} people`) : null),
          el("div", { class: "co-links" }, extLink("Website ↗", e.website, "btn small"),
            extLink("Careers search ↗", `https://www.google.com/search?q=${encodeURIComponent(`${e.name} careers DevOps OR SRE OR cloud ${country}`)}`, "btn small ghost"),
            extLink("Jobs on LinkedIn ↗", `https://www.linkedin.com/jobs/search/?keywords=${encodeURIComponent(`${e.name} DevOps`)}&location=${encodeURIComponent(country)}`, "btn small ghost"))))) :
          el("div", { class: "empty" }, "No large tech employers recorded for this country."),
        el("p", { class: "sub" }, "Links open normal pages in your browser; nothing is automated on any site."));
    }

    readColors();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas.parentElement);
    resize();
    renderPanel();
    raf = requestAnimationFrame(loop);
    state = { stop: () => { cancelAnimationFrame(raf); ro.disconnect(); } };
  }
  function unmount() { state?.stop(); state = null; }

  window.JRMap = { mount, unmount };
})();
