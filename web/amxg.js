// AMXG specific modules. Loaded before the main Maestro script; uses its helpers
// ($, esc, num, dt, api, css, el, hover, showTip, hideTip, whyEmpty) at call time.
(() => {
const ICON = {good: "✓", warning: "●", serious: "◆", critical: "▲", unknown: "–"};
const LBL = {good: "On track", warning: "Watch", serious: "Serious", critical: "Critical", unknown: "No data"};
const stTag = (s, label) => `<span class="tag ${s}">${ICON[s] || ""} ${esc(label || LBL[s] || s)}</span>`;
const post = (p, body) => fetch(p, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
  .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.status); return j; });
const head = (eyebrow, title, sub) => `<div class="titlebar"><div><div class="mono eyebrow"><a href="#amxg" style="text-decoration:none">AMXG</a> // ${esc(eyebrow)}</div><h1>${esc(title)}</h1>
  ${sub ? `<div class="mono" style="color:var(--muted)">${sub}</div>` : ""}</div></div>`;
const trustLine = t => t ? ` · data trust <a href="/governance#overview">${esc(t.label)}</a>` : "";
const statusColor = s => ({good: css("--good"), warning: css("--warning"), serious: css("--serious"), critical: css("--critical")}[s] || css("--grid"));
function inkOn(hex) {   // dark or light label on a fill
  const h = (hex || "#ccc").replace("#", ""); if (h.length < 6) return css("--ink");
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255).map(c => c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
  return .2126 * r + .7152 * g + .0722 * b > .32 ? "#14171c" : "#ffffff";
}
const who = () => localStorage.amxUser || "";

// small modal for actions that need a name (audited)
function askDialog(title, fields) {
  return new Promise(res => {
    const d = document.createElement("dialog"); d.style.borderColor = "var(--accent)";
    d.innerHTML = `<form method="dialog"><h2>${esc(title)}</h2>${fields.map(f => `<label for="ad_${f.k}">${esc(f.l)}</label>
      ${f.opts ? `<select id="ad_${f.k}">${f.opts.map(o => `<option ${o === f.v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`
        : `<input id="ad_${f.k}" value="${esc(f.v ?? "").replace("—", "")}" ${f.type ? `type="${f.type}"` : ""} ${f.req ? "required" : ""}>`}`).join("")}
      <div id="ad_err" style="color:var(--critical);margin-top:8px"></div>
      <div class="row" style="justify-content:flex-end;margin-top:16px"><button type="button" class="plain" id="ad_no">Cancel</button><button type="submit" id="ad_ok">Save</button></div></form>`;
    document.body.appendChild(d); d.showModal();
    d.querySelector("#ad_no").onclick = () => { d.close(); d.remove(); res(null); };
    d.querySelector("form").onsubmit = e => { e.preventDefault();
      const out = {}; for (const f of fields) out[f.k] = d.querySelector("#ad_" + f.k).value.trim();
      const miss = fields.find(f => f.req && !out[f.k]); if (miss) { d.querySelector("#ad_err").textContent = miss.l + " is required"; return; }
      if (out.user) localStorage.amxUser = out.user; d.close(); d.remove(); res(out); };
  });
}

// ------------------------------------------------------------------ hub
const CARDS = [
  ["AMX_01", "events", "Event management", "Monitor, track, and manage supply chain events in real time: faults, telemetry, schedule risk, issues, blocked work and shortages in one feed.", "#amxg-events", "LIVE"],
  ["AMX_02", "timelines", "Product specific timelines", "Track milestone schedules, induction dates, and delivery progress for individual product lines.", "#amxg-timelines", "LIVE"],
  ["AMX_03", "docks", "Dock overview", "Live status board for a single dock - skill schedule, NBTs, evidence, burn rate, and occupancy across the floor.", "#docks", "LIVE"],
  ["AMX_04", "tiered", "Tiered dashboards", "Drill from squadron-level summaries down to dock-level detail across the chain of command.", "#amxg-tiered", "LIVE"],
  ["AMX_05", "status", "Aircraft status report", "Detailed timeline and status tracking for aircraft across all weapon systems and platforms.", "#amxg-status", "LIVE"],
  ["AMX_06", "machine", "Machine health", "Shop-floor equipment status - uptime, MTBF, preventive maintenance, and the work each machine is holding up.", "#fleet", "LIVE"],
  ["CONCEPT", "dockboard", "Dockboard icicle", "The whole floor as one drillable icicle: aircraft to phase to skill to work package to operation, with recolour lenses for status, schedule, priority, parts, hours, and manning.", "#amxg-dockboard", "CONCEPT"],
  ["CONCEPT", "bom", "BOM icicle", "The indentured bill of materials as one drillable icicle: end item to assembly to sub-assembly to piece part, with availability / lead-time / cost / conformance lenses, worst-of rollup, a blocked-only filter, and single-asset zoom.", "#amxg-bom", "CONCEPT"],
  ["CONCEPT", "script", "Gate script", "The printed Excel repair-gate script made live: the gate's days across the top, skill-coded task rows, found work that splices in and re-flows the schedule, and a PMSS mirror that receives closures and scope advisories.", "#amxg-script", "CONCEPT"],
];
async function viewHub(v) {
  let h = {cards: {}}; try { h = await api("/api/amxg/hub"); } catch {}
  v.innerHTML = `<div class="titlebar"><div><div class="mono eyebrow">Operational module // AMXG</div><h1>AMXG SPECIFIC MODULES</h1>
    <div class="mono" style="color:var(--muted)">Aircraft Maintenance Cross-Group${h.as_of ? " · status as of " + dt(h.as_of) : ""}</div></div></div>
    <div class="grid4">${CARDS.map(([id, k, t, d, href, kind], i) => { const c = h.cards[k] || {};
      return `<a class="mod" href="${href}"><div class="ic">${i < 6 ? "0" + (i + 1) : "C" + (i - 5)}</div><span class="id mono">${id}</span>
      <h3>${esc(t)}</h3><p>${esc(d)}</p>
      <div class="row" style="margin-top:10px;gap:8px"><span class="badge ${kind === "LIVE" && c.live ? "live" : ""}" style="margin:0">${c.live === false ? "NO DATA" : kind}</span>
      ${c.live && c.value != null ? `<span class="mono" style="color:var(--ink2)">${num(c.value)} ${esc(c.label)}${c.critical ? ` · <span style="color:var(--critical)">▲ ${c.critical} critical</span>` : ""}</span>` : ""}</div></a>`; }).join("")}</div>
    <p class="mono" style="color:var(--muted);margin-top:14px">Concept screens run on the demo pack's synthetic work-task and BOM files until real ones are loaded in Configuration.</p>`;
}

// ------------------------------------------------------------------ AMX_01 events
async function viewEvents(v) {
  const e = await api("/api/amxg/events");
  const f = Object.fromEntries(new URLSearchParams(location.hash.split("?")[1] || ""));
  const state = f.state || "active", src = f.source || "", lvl = f.level || "";
  const rows = e.items.filter(i => (state === "all" || (state === "active" ? i.state !== "closed" : i.state === state)) && (!src || i.source === src) && (!lvl || i.level === lvl));
  const q = o => "#amxg-events?" + new URLSearchParams({state, source: src, level: lvl, ...o}).toString();
  const chip = (label, href, on) => `<a class="tag" href="${href}" style="text-decoration:none;${on ? "color:var(--accent);border-color:var(--accent)" : "color:var(--ink2)"}">${esc(label)}</a>`;
  v.innerHTML = head("AMX_01", "EVENT MANAGEMENT", `${e.open} open events · as of ${dt(e.as_of)}`) +
    `<div class="stats">${["critical", "serious", "warning"].map(l => `<a class="stat" href="${q({level: lvl === l ? "" : l})}" style="text-decoration:none;${lvl === l ? "border-color:var(--accent)" : ""}">
      <div class="l mono">${ICON[l]} ${l}</div><div class="v" style="color:var(--${l === "warning" ? "ink" : l})">${e.by_level[l] || 0}</div></a>`).join("")}
      <div class="stat"><div class="l mono">Sources</div><div style="font-size:12px;color:var(--ink2);margin-top:6px">${Object.entries(e.by_source).map(([k, n]) => `${esc(k)} ${n}`).join(" · ")}</div></div></div>
    <div class="row" style="margin-bottom:12px">${chip("Active", q({state: "active"}), state === "active")}${chip("Open", q({state: "open"}), state === "open")}${chip("Acknowledged", q({state: "acknowledged"}), state === "acknowledged")}${chip("Closed", q({state: "closed"}), state === "closed")}${chip("All", q({state: "all"}), state === "all")}
      <span style="width:16px"></span>${chip("Every source", q({source: ""}), !src)}${Object.keys(e.by_source).map(s => chip(s, q({source: s}), src === s)).join("")}</div>
    <div class="panel" style="padding:0"><table><tr><th>Level</th><th>Source</th><th>Asset / ref</th><th>Event</th><th>Logged</th><th>State</th><th></th></tr>
    ${rows.map(i => `<tr data-id="${i.id}"><td>${stTag(i.level, i.level.toUpperCase())}</td><td>${esc(i.source)}</td>
      <td>${i.category === "fleet" || i.category === "depot" ? `<a href="#amxg-status?asset=${encodeURIComponent(i.asset_id || "")}">${esc(i.asset_id)}</a>` : i.category === "supply" ? `<a href="/supply">${esc(i.asset_id)}</a>` : esc(i.asset_id)}</td>
      <td>${esc(i.title)}${i.detail ? `<div style="color:var(--muted)">${esc(i.detail)}</div>` : ""}${i.note ? `<div style="color:var(--ink2)">Note: ${esc(i.note)}</div>` : ""}</td>
      <td>${i.when ? dt(i.when) : "—"}</td>
      <td><span class="mono" style="font-size:11px">${esc(i.state)}</span>${i.assignee ? `<div style="color:var(--muted)">→ ${esc(i.assignee)}</div>` : ""}${i.updated_by ? `<div style="color:var(--muted);font-size:11px">${esc(i.updated_by)} ${dt(i.updated_at)}</div>` : ""}</td>
      <td style="white-space:nowrap">${i.state === "open" ? `<button class="plain" data-act="acknowledged">Acknowledge</button> ` : ""}${i.state !== "closed" ? `<button class="plain" data-act="closed">Close</button>` : `<button class="plain" data-act="open">Reopen</button>`}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">No events match</td></tr>`}</table></div>`;
  v.querySelectorAll("button[data-act]").forEach(b => b.onclick = async () => {
    const id = b.closest("tr").dataset.id, ev = e.items.find(x => x.id === id), act = b.dataset.act;
    const r = await askDialog(act === "acknowledged" ? "Acknowledge event" : act === "closed" ? "Close event" : "Reopen event",
      [{k: "user", l: "Your name or role (audited)", v: who(), req: true}, {k: "assignee", l: "Assign to (optional)", v: ev.assignee || ""}, {k: "note", l: "Note (optional)", v: ev.note || ""}]);
    if (!r) return; await post("/api/amxg/events/" + id, {state: act, ...r}); route();
  });
}

// ------------------------------------------------------------------ AMX_02 timelines (Gantt)
async function viewTimelines(v) {
  const kind = (location.hash.split("?")[1] || "").replace("filter=", "") || "all";
  const t = await api("/api/amxg/timelines?filter=" + kind);
  const chip = (k, l) => `<a class="tag" href="#amxg-timelines?filter=${k}" style="text-decoration:none;${kind === k ? "color:var(--accent);border-color:var(--accent)" : "color:var(--ink2)"}">${l} ${t.counts[k] || 0}</a>`;
  v.innerHTML = head("AMX_02", "PRODUCT SPECIFIC TIMELINES", `B-52H PDM · as of ${dt(t.as_of)}${trustLine(t.trust)}`) +
    `<div class="row" style="margin-bottom:12px">${chip("all", "All")}${chip("in_work", "In work")}${chip("delivered", "Sold / delivered")}${chip("incoming", "Incoming")}</div>
    <div class="viz" id="gantt"><div class="legend"><span><i class="key-rect" style="background:var(--s1)"></i>Gate</span><span><i class="key-rect" style="background:var(--s2)"></i>Queue</span>
    <span><i class="key-rect" style="border:1.5px dashed var(--ink2)"></i>Planned (not inducted)</span><span><span style="color:var(--s3)">◆</span> AMREP date</span><span>○ Projected / actual finish</span>
    <span style="color:var(--critical)">▲ Gate over its goal</span><span><i class="key-line" style="background:var(--ink2)"></i>As of</span></div></div><div id="gtab"></div>`;
  if (!t.rows.length) { $("#gantt").insertAdjacentHTML("beforeend", await whyEmpty(["asset_flow", "gate_event"], "aircraft timelines")); return; }
  const host = $("#gantt"), W = Math.max(host.clientWidth - 32, 700), rowH = 26, m = {t: 26, r: 20, b: 24, l: 90};
  const H = m.t + t.rows.length * rowH + m.b, iw = W - m.l - m.r;
  const t0 = new Date(t.range[0]).getTime() - 864e5 * 10, t1 = new Date(t.range[1]).getTime() + 864e5 * 10;
  const x = d => m.l + (new Date(d).getTime() - t0) / (t1 - t0) * iw;
  const svg = el("svg", {width: W, height: H, role: "img", "aria-label": "Aircraft gate timelines"});
  const d0 = new Date(t0); d0.setDate(1);
  for (let d = new Date(d0); d.getTime() < t1; d.setMonth(d.getMonth() + 1)) { if (d.getTime() < t0) continue;
    const xx = x(d); el("line", {x1: xx, x2: xx, y1: m.t - 4, y2: H - m.b, stroke: css("--grid")}, svg);
    if (d.getMonth() % 2 === 0) el("text", {x: xx + 3, y: m.t - 10}, svg).textContent = d.toLocaleDateString(undefined, {month: "short", year: "2-digit"}); }
  t.rows.forEach((r, i) => {
    const y = m.t + i * rowH, cy = y + rowH / 2;
    const lab = el("text", {x: m.l - 8, y: cy + 4, "text-anchor": "end", class: "lab", style: "cursor:pointer"}, svg); lab.textContent = r.asset_id;
    lab.addEventListener("click", () => location.hash = "#amxg-status?asset=" + encodeURIComponent(r.asset_id));
    if (!r.segments.length && r.start) {
      const x1 = x(r.start), x2 = x(r.amrep || r.finish || r.start);
      el("rect", {x: x1, y: y + 6, width: Math.max(x2 - x1, 4), height: rowH - 12, rx: 3, fill: "none", stroke: css("--ink2"), "stroke-dasharray": "4 3"}, svg);
      hover(el("rect", {x: x1, y: y + 4, width: Math.max(x2 - x1, 4), height: rowH - 8, fill: "transparent"}, svg), r.asset_id, [`Planned induction ${dt(r.start)}`, `AMREP ${dt(r.amrep)}`, r.location || ""]);
    }
    r.segments.forEach(s => {
      const x1 = x(s.start), x2 = Math.max(x(s.end), x1 + 2);
      const rc = el("rect", {x: x1, y: y + 5, width: x2 - x1 - 1, height: rowH - 10, rx: 2, fill: s.is_queue ? css("--s2") : css("--s1"), opacity: s.finished ? 1 : .75}, svg);
      if (s.over) el("text", {x: (x1 + x2) / 2, y: y + 4, "text-anchor": "middle", style: `fill:${css("--critical")};font-size:9px`}, svg).textContent = "▲";
      hover(rc, `${s.gate}`, [r.asset_id, `${dt(s.start)} → ${s.finished ? dt(s.end) : "in gate now"}`, `${s.days} days${s.goal != null ? ` of ${num(s.goal, 0)} goal` : ""}${s.over ? " · over goal" : ""}`, s.dock ? `Dock ${s.dock}` : "", s.implied ? "Finish implied by the next gate's start" : ""].filter(Boolean));
    });
    if (r.finish) { const fx = x(r.finish); el("circle", {cx: fx, cy, r: 7, fill: "none", stroke: r.late ? css("--critical") : css("--ink2"), "stroke-width": 1.5}, svg); }
    if (r.amrep) { const ax = x(r.amrep); el("path", {d: `M${ax},${cy - 5} L${ax + 5},${cy} L${ax},${cy + 5} L${ax - 5},${cy} Z`, fill: css("--s3")}, svg); }
    if (r.late) el("text", {x: W - m.r, y: cy + 4, "text-anchor": "end", style: `fill:${css("--critical")}`}, svg).textContent = "▲ late";
  });
  const tx = x(t.as_of); el("line", {x1: tx, x2: tx, y1: m.t - 4, y2: H - m.b, stroke: css("--ink2"), "stroke-width": 1.5}, svg);
  el("text", {x: tx, y: H - 6, class: "lab", "text-anchor": "middle"}, svg).textContent = "As of " + dt(t.as_of);
  host.appendChild(svg);
  $("#gtab").innerHTML = `<details><summary>Table view</summary><table><tr><th>Aircraft</th><th>Status</th><th>Start</th><th>Current gate</th><th>AMREP</th><th>Finish</th><th>Home base</th></tr>
    ${t.rows.map(r => `<tr><td>${esc(r.asset_id)}</td><td>${esc(r.status.replace("_", " "))}</td><td>${dt(r.start)}</td><td>${esc(r.current_gate)}</td><td>${dt(r.amrep)}</td><td>${dt(r.finish)}${r.late ? " ▲ late" : ""}</td><td>${esc(r.location)}</td></tr>`).join("")}</table></details>`;
}

// ------------------------------------------------------------------ AMX_04 tiered dashboards
function kpiTiles(n) {
  const k = n.kpis || {}, tiles = [];
  if (n.level === "Depot line") {
    tiles.push(["Aircraft in work", num(k.in_work, 0)], ["AMREP risk", num(k.amrep_risk, 0)], ["Last-6 flow days", num(k.flow_avg)],
               ["Gate days (sum) vs requirement", `${num(k.gate_avg_total)} / ${num(k.gate_req_total)}`],
               ["Gates over WIP plan", esc((k.over_plan_gates || []).join(", ") || "none")], ["Gates slower than requirement", esc((k.slow_gates || []).join(", ") || "none")]);
  } else if (n.level === "Dock") {
    tiles.push(["Aircraft", num(k.aircraft, 0)], ["AMREP risk", num(k.amrep_risk, 0)]);
  } else if (n.level === "Aircraft") {
    tiles.push(["Gate", esc(k.gate)], ["Day / goal", `${num(k.day, 0)} / ${num(k.day_goal, 0)}`], ["Flow / AMREP days", `${num(k.flow_days, 0)} / ${num(k.amrep_days, 0)}`],
               ["AMREP", dt(k.amrep_date)], ["Projected finish", dt(k.projected_finish)]);
  } else {
    tiles.push(["Produced / goal", `${num(k.produced, 0)} / ${num(k.goal, 0)}`], ["Attainment", k.attainment != null ? num(k.attainment * 100) + "%" : "—"],
               ["On time", k.on_time_pct != null ? num(k.on_time_pct) + "%" : "—"], ["Hours actual / target", `${num(k.hours_actual / 1000, 0)}k / ${num(k.hours_target / 1000, 0)}k`],
               ["Yield actual / target", k.yield_actual != null ? `${num(k.yield_actual * 100)}% / ${num(k.yield_target * 100)}%` : "—"]);
  }
  return `<div class="stats">${tiles.map(([l, val]) => `<div class="stat"><div class="l mono">${l}</div><div class="v" style="font-size:${String(val).length > 14 ? 15 : 20}px">${val}</div></div>`).join("")}</div>`;
}
function periodChart(host, periods) {
  const W = Math.min(Math.max(host.clientWidth - 32, 320), 560), H = 170, m = {t: 14, r: 10, b: 24, l: 30}, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const maxV = niceMax(Math.max(1, ...periods.map(p => Math.max(p.produced || 0, p.goal || 0))) * 1.1), y = v => m.t + ih - v / maxV * ih;
  const band = iw / periods.length, bw = Math.min(28, band * .5), svg = el("svg", {width: W, height: H, role: "img", "aria-label": "Produced vs goal by period"});
  for (let i = 0; i <= 2; i++) { const val = maxV * i / 2; el("line", {x1: m.l, x2: m.l + iw, y1: y(val), y2: y(val), stroke: i ? css("--grid") : css("--axis")}, svg); el("text", {x: m.l - 5, y: y(val) + 4, "text-anchor": "end"}, svg).textContent = num(val, 0); }
  periods.forEach((p, i) => { const cx = m.l + band * i + band / 2, top = y(p.produced || 0), h = y(0) - top, r = Math.min(4, h);
    if (h > 0) el("path", {d: `M${cx - bw / 2},${y(0)} V${top + r} Q${cx - bw / 2},${top} ${cx - bw / 2 + r},${top} H${cx + bw / 2 - r} Q${cx + bw / 2},${top} ${cx + bw / 2},${top + r} V${y(0)} Z`, fill: css("--s1")}, svg);
    if (p.goal != null) el("line", {x1: cx - bw / 2 - 5, x2: cx + bw / 2 + 5, y1: y(p.goal), y2: y(p.goal), stroke: css("--s3"), "stroke-width": 2, "stroke-linecap": "round"}, svg);
    el("text", {x: cx, y: H - 8, "text-anchor": "middle"}, svg).textContent = (p.period || "").replace(/^FY\d+ /, "");
    hover(el("rect", {x: cx - band / 2, y: m.t, width: band, height: ih, fill: "transparent"}, svg), `${num(p.produced, 0)} produced`, [p.period, `Goal ${num(p.goal, 0)}`]); });
  return svg;
}
async function viewTiered(v) {
  const t = await api("/api/amxg/tiered");
  if (!t.has_data) { v.innerHTML = head("AMX_04", "TIERED DASHBOARDS") + await whyEmpty(["production_summary", "issue", "asset_flow"], "production summaries"); return; }
  const path = decodeURIComponent((location.hash.split("?p=")[1] || "")).split(">").filter(Boolean);
  let node = t.root; const crumbs = [t.root];
  for (const id of path) { const nx = (node.children || []).find(c => c.id === id); if (!nx) break; node = nx; crumbs.push(nx); }
  const href = n => "#amxg-tiered?p=" + encodeURIComponent(crumbs.slice(1, crumbs.indexOf(n) + 1).map(c => c.id).join(">"));
  const childHref = c => "#amxg-tiered?p=" + encodeURIComponent([...crumbs.slice(1).map(x => x.id), c.id].join(">"));
  const kids = node.children || [];
  v.innerHTML = head("AMX_04", "TIERED DASHBOARDS", `as of ${dt(t.as_of)}${trustLine(t.trust)}`) +
    `<div class="mono" style="margin-bottom:14px">${crumbs.map((c, i) => i === crumbs.length - 1 ? `<b>${esc(c.name)}</b>` : `<a href="${href(c)}">${esc(c.name)}</a>`).join(" › ")}</div>
    <div class="row" style="justify-content:space-between;margin-bottom:10px"><h2 style="margin:0">${node.level === node.name ? esc(node.name) : esc(node.level) + ": " + esc(node.name)}</h2>${stTag(node.status)}</div>
    ${kpiTiles(node)}
    <div class="split"><div>${kids.length ? `<div class="mono" style="color:var(--muted);margin-bottom:8px">${esc(kids[0].level)} · ${kids.length} — select to drill down</div>
      <div class="grid4">${kids.map(c => { const k = c.kpis || {};
        const line = c.level === "Aircraft" ? `${esc(k.gate)} · day ${num(k.day, 0)}/${num(k.day_goal, 0)}` : c.level === "Dock" ? `${k.aircraft} aircraft` :
                     c.level === "Depot line" ? `${k.in_work} in work · ${k.amrep_risk} AMREP risk` : `${num(k.produced, 0)} / ${num(k.goal, 0)} produced${k.attainment != null ? ` (${num(k.attainment * 100, 0)}%)` : ""}`;
        return `<a class="mod" style="min-height:120px" href="${c.level === "Aircraft" ? "#amxg-status?asset=" + encodeURIComponent(c.name) : childHref(c)}"><span class="id mono">${esc(c.level)}</span>
        <h3 style="margin-top:4px">${esc(c.name)}</h3><p>${line}</p><div style="margin-top:10px">${stTag(c.status)} ${(c.issues || []).length ? `<span class="tag warning">● ${c.issues.length} issues</span>` : ""}</div>
        ${c.note ? `<p style="color:var(--muted);font-size:11px;margin-top:8px">${esc(c.note)}</p>` : ""}</a>`; }).join("")}</div>` : `<p class="empty">${esc(node.note || "Lowest level.")}</p>`}
      </div><div>${node.periods && node.periods.length ? `<div class="viz" id="pchart"><h3 style="font-size:14px">Produced by quarter</h3><div class="legend"><span><i class="key-rect" style="background:var(--s1)"></i>Produced</span><span><i class="key-line" style="background:var(--s3)"></i>Goal</span></div></div>` : ""}
      <div class="panel" style="margin-top:12px"><h2 class="mono">Issues at this level</h2>${(node.issues || []).length ? `<table>${node.issues.map(i => `<tr><td>${stTag(i.status === "Critical" ? "critical" : "serious", i.status)}</td><td>${esc(i.title)}<div style="color:var(--muted)">${esc(i.organization)}</div></td><td class="n" style="white-space:nowrap">${i.impact_days != null ? num(i.impact_days, 0) + " d" : ""}</td></tr>`).join("")}</table>` : `<p class="empty">No open issues</p>`}</div></div></div>`;
  if ($("#pchart")) $("#pchart").appendChild(periodChart($("#pchart"), node.periods));
}

// ------------------------------------------------------------------ AMX_05 aircraft status report
const SHORT = {"Queue to Pre Dock": "Q PD", "Pre Dock": "Pre Dock", "Queue to Inspection": "Q Insp", "Inspection": "Insp", "Queue to Repair": "Q Rep", "Repair": "Repair",
               "Queue to Restore": "Q Rest", "Restore": "Restore", "Queue to Post Dock": "Q Post", "Post Dock": "Post Dock"};
async function viewStatus(v) {
  const s = await api("/api/amxg/status");
  if (!s.rows.length) { v.innerHTML = head("AMX_05", "AIRCRAFT STATUS REPORT") + await whyEmpty(["asset_flow", "gate_event"], "aircraft"); return; }
  const sel = new URLSearchParams(location.hash.split("?")[1] || "").get("asset") || s.rows[0].asset_id;
  const a = s.rows.find(r => r.asset_id === sel) || s.rows[0];
  const cell = c => { if (!c) return `<td></td>`;
    const txt = c.state === "planned" ? "" : `${c.days}${c.goal ? "/" + num(c.goal, 0) : " d"}`;
    const style = {done: `background:color-mix(in srgb, var(--s1) 22%, transparent)`, done_over: `background:color-mix(in srgb, var(--s1) 22%, transparent);color:var(--critical);font-weight:600`,
                   in_work: `background:var(--s1);color:#fff;font-weight:600`, in_work_over: `background:var(--critical);color:#fff;font-weight:600`, planned: `border:1px dashed var(--line)`}[c.state];
    return `<td class="n" style="${style};white-space:nowrap" title="${esc(c.state.replace("_", " "))}${c.start ? " · " + dt(c.start) : ""}">${c.state.endsWith("over") ? "▲ " : ""}${txt}</td>`; };
  v.innerHTML = head("AMX_05", "AIRCRAFT STATUS REPORT", `B-52H · ${s.counts.in_work || 0} in work · ${s.counts.incoming || 0} incoming · ${s.counts.delivered || 0} delivered · as of ${dt(s.as_of)}${trustLine(s.trust)}`) +
    `<div class="legend" style="margin-bottom:8px"><span><i class="key-rect" style="background:color-mix(in srgb, var(--s1) 22%, transparent)"></i>Gate done (days/goal)</span><span><i class="key-rect" style="background:var(--s1)"></i>In gate now</span>
     <span><i class="key-rect" style="background:var(--critical)"></i>▲ In gate, over goal</span><span style="color:var(--critical)">▲ Finished over goal</span><span><i class="key-rect" style="border:1px dashed var(--line)"></i>Planned</span></div>
    <div class="split" style="grid-template-columns:2.4fr 1fr"><div class="panel" style="padding:0;overflow:auto"><table id="srt"><tr><th>Aircraft</th>${s.gates.map(g => `<th class="n" style="font-size:10px">${esc(SHORT[g] || g)}</th>`).join("")}<th>AMREP</th><th class="n">Margin</th><th>Health</th><th class="n">Blocked</th></tr>
    ${s.rows.map(r => `<tr data-a="${esc(r.asset_id)}" style="cursor:pointer;${r.asset_id === a.asset_id ? "outline:2px solid var(--accent);outline-offset:-2px" : ""}"><td style="white-space:nowrap"><b>${esc(r.asset_id)}</b><div style="color:var(--muted);font-size:11px">${esc(r.status.replace("_", " "))}</div></td>
      ${s.gates.map(g => cell(r.cells[g])).join("")}<td>${dt(r.amrep)}</td><td class="n" style="${r.late ? "color:var(--critical)" : ""}">${r.margin_days != null ? (r.late ? "▲ " : "") + r.margin_days + " d" : "—"}</td>
      <td>${r.band && r.band.code ? stTag(r.band.status, r.band.code) : "—"}</td><td class="n">${r.tasks.Blocked ? `<span style="color:var(--critical)">▲ ${r.tasks.Blocked}</span>` : ""}</td></tr>`).join("")}</table></div>
    <div class="panel"><div class="mono eyebrow">Selected aircraft</div><h2 style="font-size:24px;margin:4px 0">${esc(a.asset_id)}</h2>
      <div style="color:var(--ink2)">${esc(a.status.replace("_", " "))} · ${esc(a.location)}</div>
      <table style="margin-top:10px"><tr><td>AMREP</td><td>${dt(a.amrep)}</td></tr><tr><td>Projected / actual finish</td><td>${dt(a.finish)}</td></tr>
      <tr><td>Margin</td><td style="${a.late ? "color:var(--critical)" : ""}">${a.margin_days != null ? a.margin_days + " days" + (a.late ? " late" : "") : "—"}</td></tr>
      <tr><td>Flow / AMREP days</td><td>${num(a.flow_days, 0)} / ${num(a.amrep_days, 0)}</td></tr><tr><td>Mission capable</td><td>${a.mc_rate != null ? num(a.mc_rate) + "%" : "—"}</td></tr>
      <tr><td>Open faults</td><td>${a.open_faults}</td></tr><tr><td>Work tasks</td><td>${Object.entries(a.tasks).map(([k, n]) => `${esc(k)} ${n}`).join(" · ") || "—"}</td></tr>
      <tr><td>Waiting on parts</td><td>${a.blocked_parts.map(p => `<a href="#amxg-bom?asset=${encodeURIComponent(a.asset_id)}">${esc(p)}</a>`).join(", ") || "—"}</td></tr></table>
      <div class="mono" style="font-size:10px;margin-top:12px"><u>Notes</u></div><ul style="color:var(--ink2);padding-left:16px">${(a.notes.length ? a.notes : ["—"]).map(n => `<li>${esc(n)}</li>`).join("")}</ul>
      <div class="row" style="margin-top:10px">${Object.keys(a.tasks).length ? `<a class="tag" style="color:var(--accent)" href="#amxg-script?asset=${encodeURIComponent(a.asset_id)}">Gate script</a>` : ""}
      <a class="tag" style="color:var(--accent)" href="#fleet/${encodeURIComponent(a.asset_id)}">Machine health</a><a class="tag" style="color:var(--accent)" href="/governance#drill?asset=${encodeURIComponent(a.asset_id)}">Records & lineage</a></div></div></div>`;
  v.querySelectorAll("#srt tr[data-a]").forEach(tr => tr.onclick = () => location.hash = "#amxg-status?asset=" + encodeURIComponent(tr.dataset.a));
}

// ------------------------------------------------------------------ icicle (shared by the two concepts)
// lens = {key(leaf)->category, cats:{cat:{label,color,rank}}} for categorical (worst-of rollup = lowest rank)
//     or {value(leaf)->number, roll:"sum"|"max"|"min"|"ratio", bands:[[upTo,label,color]]} for numeric
function rollup(n, lens) {
  if (!n.children || !n.children.length) {
    n._v = lens.cats ? lens.key(n) : lens.value(n); n._num = lens.num ? lens.num(n) : null; n._den = lens.den ? lens.den(n) : null; return n;
  }
  n.children.forEach(c => rollup(c, lens));
  if (lens.cats) n._v = n.children.map(c => c._v).reduce((a, b) => (lens.cats[a]?.rank ?? 99) <= (lens.cats[b]?.rank ?? 99) ? a : b);
  else if (lens.roll === "ratio") { n._num = n.children.reduce((s, c) => s + (c._num || 0), 0); n._den = n.children.reduce((s, c) => s + (c._den || 0), 0); n._v = n._den ? n._num / n._den : null; }
  else { const vals = n.children.map(c => c._v).filter(x => x != null);
    n._v = !vals.length ? null : lens.roll === "sum" ? vals.reduce((a, b) => a + b, 0) : lens.roll === "min" ? Math.min(...vals) : Math.max(...vals); }
  return n;
}
function lensColor(lens, v) {
  if (lens.cats) return (lens.cats[v] || {}).color || css("--grid");
  if (v == null) return css("--grid");
  for (const [up, , c] of lens.bands) if (v <= up) return c;
  return lens.bands[lens.bands.length - 1][2];
}
function lensLabel(lens, v) {
  if (lens.cats) return (lens.cats[v] || {}).label || v;
  if (v == null) return "No data";
  for (const [up, l] of lens.bands) if (v <= up) return l;
  return lens.bands[lens.bands.length - 1][1];
}
function sizeOf(n, sizeFn) { if (!n.children || !n.children.length) return n._size = Math.max(sizeFn(n), 0.0001); return n._size = n.children.reduce((s, c) => s + sizeOf(c, sizeFn), 0); }
function depthOf(n) { return n.children && n.children.length ? 1 + Math.max(...n.children.map(depthOf)) : 1; }
function icicle(host, root, lens, {sizeFn = n => n.value || 1, onZoom, tipRows, labelOf = n => n.name, levelNames}) {
  rollup(root, lens); sizeOf(root, sizeFn);
  const W = Math.max(host.clientWidth - 32, 640), rows = depthOf(root), rh = 38, H = rows * rh + 4;
  const svg = el("svg", {width: W, height: H, role: "img", "aria-label": "Icicle chart"});
  const draw = (n, x0, w, d) => {
    if (w < 0.5) return;
    const y = d * rh, fill = lensColor(lens, n._v);
    const g = el("g", {style: "cursor:" + (n.children && n.children.length ? "zoom-in" : "default")}, svg);
    el("rect", {x: x0 + .5, y: y + .5, width: Math.max(w - 1, .5), height: rh - 1, rx: 2, fill, stroke: css("--surface"), "stroke-width": 1}, g);
    if (w > 46) { const t = el("text", {x: x0 + 6, y: y + 15, style: `fill:${inkOn(fill)};font-size:11px;font-weight:600`}, g);
      const s = labelOf(n), maxc = Math.floor((w - 10) / 6.2); t.textContent = s.length > maxc ? s.slice(0, Math.max(maxc - 1, 1)) + "…" : s;
      if (w > 90) { const t2 = el("text", {x: x0 + 6, y: y + 29, style: `fill:${inkOn(fill)};font-size:10px;opacity:.85`}, g); const l2 = lensLabel(lens, n._v) + ""; t2.textContent = l2.length > maxc ? l2.slice(0, maxc - 1) + "…" : l2; } }
    hover(g, labelOf(n), [(levelNames ? levelNames(n, d) : n.level) || "", `${lens.name}: ${lensLabel(lens, n._v)}${!lens.cats && n._v != null && lens.fmt ? " (" + lens.fmt(n._v) + ")" : ""}`, ...(tipRows ? tipRows(n) : []),
      n.children && n.children.length ? "Click to zoom" : ""].filter(Boolean));
    if (n.children && n.children.length && d > 0) g.addEventListener("click", () => onZoom(n));
    let cx = x0; (n.children || []).forEach(c => { const cw = w * c._size / n._size; draw(c, cx, cw, d + 1); cx += cw; });
  };
  draw(root, 0, W, 0);
  return svg;
}
function leavesOf(n) { return n.children && n.children.length ? n.children.flatMap(leavesOf) : [n]; }
function prune(n, keep) { if (!n.children || !n.children.length) return keep(n) ? n : null;
  const ch = n.children.map(c => prune(c, keep)).filter(Boolean); return ch.length ? {...n, children: ch} : null; }
function findPath(n, ids, i = 0) { if (i >= ids.length) return [n]; const c = (n.children || []).find(x => x.name === ids[i]); return c ? [n, ...findPath(c, ids, i + 1)] : [n]; }
const lensPicker = (lenses, cur, id) => `<div class="row" role="radiogroup" aria-label="Lens">${lenses.map(([k, l]) => `<label class="tag" style="cursor:pointer;${k === cur ? "color:var(--accent);border-color:var(--accent)" : "color:var(--ink2)"}"><input type="radio" name="${id}" value="${k}" ${k === cur ? "checked" : ""} style="display:none">${esc(l)}</label>`).join("")}</div>`;
const legendOf = lens => `<div class="legend">${lens.cats ? Object.values(lens.cats).sort((a, b) => a.rank - b.rank).map(c => `<span><i class="key-rect" style="background:${c.color}"></i>${esc(c.label)}</span>`).join("")
  : lens.bands.map(([, l, c]) => `<span><i class="key-rect" style="background:${c}"></i>${esc(l)}</span>`).join("")}<span style="color:var(--muted)">${esc(lens.rollNote || "")}</span></div>`;

// ------------------------------------------------------------------ concept: dockboard icicle
function dockLenses() {
  const N = css("--grid"), G = css("--good"), W = css("--warning"), S = css("--serious"), C = css("--critical"), B = css("--s1");
  return {
    status: {name: "Status", key: n => n.attrs.status, rollNote: "Parents show the most urgent state below them",
             cats: {"Blocked": {label: "▲ Blocked", color: C, rank: 0}, "In work": {label: "In work", color: B, rank: 1}, "Not started": {label: "Not started", color: N, rank: 2}, "Done": {label: "✓ Done", color: G, rank: 3}}},
    schedule: {name: "Schedule", key: n => n.attrs.schedule, rollNote: "Worst of the tasks below",
               cats: {late: {label: "▲ Late to script", color: C, rank: 0}, at_risk: {label: "● Should have started", color: W, rank: 1}, on_time: {label: "✓ On script", color: G, rank: 2}}},
    priority: {name: "Priority", key: n => n.attrs.priority, rollNote: "High = found work or an aircraft at AMREP risk",
               cats: {high: {label: "◆ High", color: S, rank: 0}, normal: {label: "Normal", color: N, rank: 1}}},
    parts: {name: "Parts", key: n => n.attrs.parts, rollNote: "Worst of the tasks below",
            cats: {short: {label: "▲ Waiting on part", color: C, rank: 0}, needs: {label: "● Needs a part", color: W, rank: 1}, none: {label: "No part", color: N, rank: 2}}},
    hours: {name: "Hours charged / planned", roll: "ratio", num: n => n.attrs.hours_actual, den: n => n.attrs.hours_planned, value: n => n.attrs.hours_planned ? n.attrs.hours_actual / n.attrs.hours_planned : null,
            fmt: v => num(v * 100, 0) + "%", rollNote: "Parents sum hours",
            bands: [[0.0001, "Not charged", N], [1.0, "Within plan", G], [1.1, "● Up to 10% over", W], [Infinity, "▲ Over 10%", C]]},
    manning: {name: "Skill backlog", roll: "max", value: n => n.attrs.manning, fmt: v => num(v * 100, 0) + "% of busiest skill", rollNote: "Remaining hours of the task's skill across the floor",
              bands: [[0.25, "Light", css("--s1-light")], [0.6, "Moderate", B], [Infinity, "◆ Heaviest skills", S]]},
  };
}
async function viewDockboard(v) {
  const d = await api("/api/amxg/dockboard");
  const qs = new URLSearchParams(location.hash.split("?")[1] || ""), lensKey = qs.get("lens") || "status", zoom = (qs.get("z") || "").split(">").filter(Boolean);
  v.innerHTML = head("Concept", "DOCKBOARD ICICLE", `${d.tasks} tasks · width = planned hours · as of ${dt(d.as_of)}${trustLine(d.trust)}`);
  if (!d.tasks) { v.insertAdjacentHTML("beforeend", await whyEmpty(["work_task"], "work tasks")); return; }
  const lenses = dockLenses(), lens = lenses[lensKey] || lenses.status;
  const chain = findPath(d.tree, zoom), focus = chain[chain.length - 1];
  const nav = o => "#amxg-dockboard?" + new URLSearchParams({lens: lensKey, z: zoom.join(">"), ...o}).toString();
  v.insertAdjacentHTML("beforeend", `<div class="row" style="justify-content:space-between;margin-bottom:8px">${lensPicker(Object.entries(lenses).map(([k, l]) => [k, l.name]), lensKey, "dlens")}</div>
    <div class="mono" style="margin-bottom:8px">${chain.map((n, i) => i === chain.length - 1 ? `<b>${esc(n.name)}</b>` : `<a href="${nav({z: zoom.slice(0, i).join(">")})}">${esc(n.name)}</a>`).join(" › ")}</div>
    <div class="viz" id="ice">${legendOf(lens)}</div>
    <div class="split" style="margin-top:14px"><details id="dtab"><summary>Table view (${leavesOf(focus).length} tasks)</summary></details>
    <div class="panel"><h2 class="mono">Skill backlog (remaining hours)</h2><table>${Object.entries(d.skill_load).map(([k, h]) => `<tr><td>${esc(k)}</td><td class="n">${num(h, 0)} h</td></tr>`).join("")}</table></div></div>`);
  v.querySelectorAll("input[name=dlens]").forEach(r => r.onchange = () => location.hash = nav({lens: r.value}));
  const levelNames = n => n.level;
  $("#ice").appendChild(icicle($("#ice"), focus, lens, {
    sizeFn: n => n.value || 1, levelNames,
    onZoom: n => { const p = findPathNames(d.tree, n); location.hash = nav({z: p.join(">")}); },
    tipRows: n => n.attrs ? [`${n.attrs.skill} · ${n.attrs.status}`, `${num(n.attrs.hours_actual, 0)} of ${num(n.attrs.hours_planned, 0)} h`, `Script days to ${n.attrs.planned_end} · gate day ${num(n.attrs.day, 0)}`, n.attrs.part_number ? "Part " + n.attrs.part_number : "", n.attrs.found ? "Found work" : ""].filter(Boolean)
                          : [`${leavesOf(n).length} tasks · ${num(n._size, 0)} planned h`]}));
  const rows = leavesOf(rollup(focus, lens));
  $("#dtab").insertAdjacentHTML("beforeend", `<table><tr><th>Task</th><th>Skill</th><th>Status</th><th>${esc(lens.name)}</th><th class="n">Hours</th><th>Part</th></tr>${rows.map(n => `<tr><td>${esc(n.name)}</td><td>${esc(n.attrs.skill)}</td><td>${esc(n.attrs.status)}</td><td>${esc(lensLabel(lens, n._v))}</td><td class="n">${num(n.attrs.hours_actual, 0)}/${num(n.attrs.hours_planned, 0)}</td><td>${esc(n.attrs.part_number)}</td></tr>`).join("")}</table>`);
}
function findPathNames(root, target, acc = []) { if (root === target) return acc; for (const c of root.children || []) { const r = findPathNames(c, target, [...acc, c.name]); if (r) return r; } return null; }

// ------------------------------------------------------------------ concept: BOM icicle
function bomLenses() {
  const N = css("--grid"), G = css("--good"), W = css("--warning"), S = css("--serious"), C = css("--critical");
  return {
    availability: {name: "Availability", key: n => n.attrs.availability, rollNote: "Worst-of rollup: an assembly is only as available as its worst part",
                   cats: {blocked: {label: "▲ Blocking work", color: C, rank: 0}, short: {label: "◆ Under 14 days cover", color: S, rank: 1}, ok: {label: "✓ Available", color: G, rank: 2}, unknown: {label: "No stock data", color: N, rank: 3}}},
    lead_time: {name: "Supplier lead time", roll: "max", value: n => n.attrs.lead_time, fmt: v => num(v, 0) + " days", rollNote: "Longest lead time below",
                bands: [[30, "Up to 30 days", G], [60, "● 31-60 days", W], [Infinity, "▲ Over 60 days", C]]},
    cost: {name: "Extended cost", roll: "sum", value: n => n.attrs.ext_cost, fmt: v => "$" + num(v / 1000, 0) + "k", rollNote: "Unit cost × quantity per parent, summed",
           bands: [[25000, "Under $25k", css("--s1-light")], [250000, "$25k-250k", css("--s1")], [Infinity, "◆ Over $250k", S]]},
    conformance: {name: "Supplier conformance", roll: "min", value: n => n.attrs.conformance, fmt: v => num(v * 100, 0) + "% on time", rollNote: "Supplier on-time delivery; worst supplier below",
                  bands: [[0.85, "▲ Under 85%", C], [0.95, "● 85-95%", W], [Infinity, "✓ 95% or better", G]]},
  };
}
async function viewBom(v) {
  const qs = new URLSearchParams(location.hash.split("?")[1] || ""), lensKey = qs.get("lens") || "availability", asset = qs.get("asset") || "",
        blocked = qs.get("blocked") === "1", zoom = (qs.get("z") || "").split(">").filter(Boolean);
  const b = await api("/api/amxg/bom" + (asset ? "?asset=" + encodeURIComponent(asset) : ""));
  v.innerHTML = head("Concept", "BOM ICICLE", b.has_data ? `Indentured bill of materials · width = part count${trustLine(b.trust)}` : "");
  if (!b.has_data) { v.insertAdjacentHTML("beforeend", await whyEmpty(["bom_line"], "bill of materials")); return; }
  const lenses = bomLenses(), lens = lenses[lensKey] || lenses.availability;
  const nav = o => "#amxg-bom?" + new URLSearchParams({lens: lensKey, asset, blocked: blocked ? "1" : "", z: zoom.join(">"), ...o}).toString();
  let tree = b.tree;
  if (asset) tree = prune(tree, n => (n.attrs.needed_by || []).length) || {...tree, children: []};
  if (blocked) tree = prune(tree, n => ["blocked", "short"].includes(n.attrs.availability)) || {...tree, children: []};
  const chain = findPath(tree, zoom), focus = chain[chain.length - 1];
  v.insertAdjacentHTML("beforeend", `<div class="row" style="justify-content:space-between;margin-bottom:8px">${lensPicker(Object.entries(lenses).map(([k, l]) => [k, l.name]), lensKey, "blens")}
    <div class="row"><label class="mono" style="cursor:pointer"><input type="checkbox" id="bonly" ${blocked ? "checked" : ""}> Blocked / short only</label>
    <label class="mono" for="basset">Single asset</label><select id="basset"><option value="">Whole BOM</option>${b.assets.map(a => `<option ${a === asset ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></div></div>
    <div class="mono" style="margin-bottom:8px">${chain.map((n, i) => i === chain.length - 1 ? `<b>${esc(n.description || n.name)}</b>` : `<a href="${nav({z: zoom.slice(0, i).join(">")})}">${esc(n.description || n.name)}</a>`).join(" › ")}
    ${asset ? ` <span class="tag" style="color:var(--accent)">Parts open work on ${esc(asset)} needs</span>` : ""}</div>
    <div class="viz" id="bice">${legendOf(lens)}</div><details id="btab" open><summary>Parts (table view)</summary></details>`);
  v.querySelectorAll("input[name=blens]").forEach(r => r.onchange = () => location.hash = nav({lens: r.value}));
  $("#bonly").onchange = e => location.hash = nav({blocked: e.target.checked ? "1" : "", z: ""});
  $("#basset").onchange = e => location.hash = nav({asset: e.target.value, z: ""});
  if (!focus.children || !focus.children.length) { $("#bice").insertAdjacentHTML("beforeend", `<p class="empty">No parts match these filters.</p>`); return; }
  $("#bice").appendChild(icicle($("#bice"), focus, lens, {
    labelOf: n => n.attrs ? n.name : (n.description || n.name), levelNames: (n, d) => n.attrs ? "Piece part" : ["End item", "System", "Assembly", "Sub-assembly"][n.level] || "Assembly",
    onZoom: n => { const p = findPathNames(tree, n); location.hash = nav({z: p.join(">")}); },
    tipRows: n => n.attrs ? [n.description, `Qty ${n.qty} · ${n.attrs.supplier || "no supplier"}${n.attrs.sole_sourced ? " (sole source)" : ""}`,
      `Cover ${n.attrs.days_cover != null ? num(n.attrs.days_cover) + " days" : "—"} · on hand ${num(n.attrs.on_hand, 0)}`,
      n.attrs.needed_by.length ? "Needed by " + n.attrs.needed_by.map(x => `${x.asset_id} (${x.status})`).join(", ") : ""].filter(Boolean)
      : [`${leavesOf(n).length} piece parts`]}));
  const rows = leavesOf(rollup(focus, lens));
  $("#btab").insertAdjacentHTML("beforeend", `<table><tr><th>Part</th><th>Description</th><th class="n">Qty</th><th>Availability</th><th class="n">Cover (days)</th><th class="n">Lead time</th><th class="n">Ext. cost</th><th class="n">Supplier on-time</th><th>Needed by</th></tr>
    ${rows.map(n => `<tr><td>${esc(n.name)}</td><td>${esc(n.description)}</td><td class="n">${n.qty}</td><td>${esc(lenses.availability.cats[n.attrs.availability]?.label)}</td><td class="n">${num(n.attrs.days_cover)}</td><td class="n">${num(n.attrs.lead_time, 0)}</td><td class="n">${n.attrs.ext_cost != null ? "$" + num(n.attrs.ext_cost, 0) : "—"}</td><td class="n">${n.attrs.conformance != null ? num(n.attrs.conformance * 100, 0) + "%" : "—"}</td>
      <td>${n.attrs.needed_by.map(x => `<a href="#amxg-script?asset=${encodeURIComponent(x.asset_id)}">${esc(x.asset_id)}</a>${x.status === "Blocked" ? " ▲" : ""}`).join(", ") || "—"}</td></tr>`).join("")}</table>`);
}

// ------------------------------------------------------------------ concept: gate script
async function viewScript(v) {
  const qs = new URLSearchParams(location.hash.split("?")[1] || "");
  let asset = qs.get("asset"), gate = qs.get("gate") || "";
  if (!asset) { const d = await api("/api/amxg/dockboard"); const first = (d.tree.children || []).flatMap(x => x.children || [])[0]; asset = first ? first.name : ""; }
  v.innerHTML = head("Concept", "GATE SCRIPT");
  if (!asset) { v.insertAdjacentHTML("beforeend", await whyEmpty(["work_task"], "work tasks")); return; }
  const g = await api(`/api/amxg/script/${encodeURIComponent(asset)}${gate ? "?gate=" + encodeURIComponent(gate) : ""}`);
  const nav = o => "#amxg-script?" + new URLSearchParams({asset: g.asset_id, gate: g.gate || "", ...o}).toString();
  v.querySelector(".titlebar .mono:last-child")?.remove();
  v.querySelector(".titlebar > div").insertAdjacentHTML("beforeend", `<div class="mono" style="color:var(--muted)">${esc(g.asset_id)} · ${esc(g.gate)}${g.day != null ? ` · day ${num(g.day, 0)}` : ""}${g.goal_days != null ? ` of ${num(g.goal_days, 0)} goal` : ""}</div>`);
  const days = Math.max(g.goal_days || 0, g.reflowed_end, 10);
  v.insertAdjacentHTML("beforeend", `<div class="row" style="margin-bottom:12px"><label class="mono" for="sa">Aircraft</label><select id="sa">${g.assets.map(a => `<option ${a === g.asset_id ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
    <label class="mono" for="sg">Gate</label><select id="sg">${g.gates.map(x => `<option ${x === g.gate ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
    <button id="addfw">+ Found work</button>
    <span class="tag ${g.over_goal ? "critical" : "good"}">${g.over_goal ? "▲" : "✓"} Script ends day ${g.reflowed_end}${g.reflowed_end !== g.planned_end ? ` (was ${g.planned_end})` : ""}${g.goal_days != null ? ` · goal ${num(g.goal_days, 0)}` : ""}</span></div>
    <div class="split" style="grid-template-columns:3fr 1fr"><div><div class="viz" id="sviz"><div class="legend">
      <span><i class="key-rect" style="background:var(--good)"></i>✓ Done</span><span><i class="key-rect" style="background:var(--s1)"></i>In work</span><span><i class="key-rect" style="background:var(--grid)"></i>Not started</span>
      <span><i class="key-rect" style="background:var(--critical)"></i>▲ Blocked</span><span><i class="key-rect" style="background:var(--s2)"></i>Found work</span><span><i class="key-rect" style="border:1.5px dashed var(--ink2)"></i>Original slot (re-flowed)</span>
      <span><i class="key-line" style="background:var(--ink2)"></i>Today</span><span><i class="key-line" style="background:var(--s3)"></i>Gate goal</span></div></div>
      <details><summary>Table view</summary><table><tr><th>Skill</th><th>Work package</th><th>Task</th><th class="n">Days</th><th class="n">Hours</th><th>Status</th><th>Part</th><th></th></tr>
      ${g.rows.map(r => `<tr><td>${esc(r.skill)}</td><td>${esc(r.work_package)}</td><td>${esc(r.task)}${r.found ? " (found work)" : ""}</td><td class="n">${r.new_start}-${r.end}${r.shift ? ` (+${r.shift})` : ""}</td><td class="n">${num(r.hours_actual, 0)}/${num(r.hours_planned, 0)}</td><td>${esc(r.status)}${r.closed_by ? " by " + esc(r.closed_by) : ""}</td><td>${esc(r.part_number)}</td>
        <td>${r.status !== "Done" ? `<button class="plain" data-close="${esc(r.task_id)}">Close</button>` : ""}</td></tr>`).join("")}</table></details></div>
    <div class="panel"><h2 class="mono">PMSS mirror</h2><p style="color:var(--muted);font-size:12px;margin-top:-6px">${g.pmss_connected ? "Connected" : "PMSS is not connected: this mirror records the closures and scope advisories that would be sent."}</p>
      ${g.mirror.length ? g.mirror.map(m => `<div style="border-left:2px solid ${m.kind === "closure" ? "var(--good)" : "var(--s2)"};padding:4px 8px;margin-bottom:8px"><div class="mono" style="font-size:10px;color:var(--muted)">${m.kind === "closure" ? "CLOSURE" : "SCOPE ADVISORY"} · ${esc(m.user)}${m.at ? " · " + new Date(m.at).toLocaleString() : ""}</div>${esc(m.message)}</div>`).join("") : `<p class="empty">Nothing sent yet</p>`}</div></div>`);
  $("#sa").onchange = e => location.hash = "#amxg-script?asset=" + encodeURIComponent(e.target.value);
  $("#sg").onchange = e => location.hash = nav({gate: e.target.value});
  // chart: rows grouped by skill, days across the top
  const host = $("#sviz"), W = Math.max(host.clientWidth - 32, 640), m = {t: 28, r: 10, b: 8, l: 250}, rh = 22;
  const rows = [...g.rows].sort((a, b) => a.skill.localeCompare(b.skill) || a.new_start - b.new_start);
  const H = m.t + rows.length * rh + m.b, iw = W - m.l - m.r, dx = iw / days, x = d => m.l + (d - 1) * dx;
  const svg = el("svg", {width: W, height: H, role: "img", "aria-label": `Gate script for ${g.asset_id} ${g.gate}`});
  const step = days > 60 ? 5 : days > 30 ? 2 : 1;
  for (let d = 1; d <= days; d++) { if ((d - 1) % step) continue; el("line", {x1: x(d), x2: x(d), y1: m.t - 4, y2: H - m.b, stroke: css("--grid")}, svg); el("text", {x: x(d) + 2, y: m.t - 8}, svg).textContent = d; }
  let prev = null;
  rows.forEach((r, i) => { const y = m.t + i * rh;
    if (r.skill !== prev) { if (i) el("line", {x1: 0, x2: W, y1: y, y2: y, stroke: css("--axis")}, svg); prev = r.skill; }
    const code = el("text", {x: 4, y: y + 15, class: "lab", style: "font-family:var(--mono)"}, svg); code.textContent = r.skill;
    const lt = el("text", {x: 34, y: y + 15}, svg); const s = `${r.work_package} · ${r.task}`; lt.textContent = s.length > 36 ? s.slice(0, 35) + "…" : s;
    if (r.shift) el("rect", {x: x(r.start), y: y + 4, width: r.days * dx - 1, height: rh - 8, rx: 2, fill: "none", stroke: css("--ink2"), "stroke-dasharray": "3 2"}, svg);
    const fill = r.found ? css("--s2") : {Done: css("--good"), "In work": css("--s1"), Blocked: css("--critical")}[r.status] || css("--grid");
    const bar = el("rect", {x: x(r.new_start), y: y + 4, width: Math.max(r.days * dx - 1, 2), height: rh - 8, rx: 2, fill, stroke: r.found && r.status === "Blocked" ? css("--critical") : "none", "stroke-width": 2}, svg);
    if (r.status === "Blocked") el("text", {x: x(r.new_start) + r.days * dx + 3, y: y + 15, style: `fill:${css("--critical")}`}, svg).textContent = "▲ " + (r.part_number || "blocked");
    hover(bar, r.task, [`${r.skill} ${r.skill_name} · ${r.work_package}`, `Days ${r.new_start}-${r.end}${r.shift ? ` (pushed ${r.shift} days by found work)` : ""}`, `${r.status}${r.found ? " · found work" : ""}`, `${num(r.hours_actual, 0)} of ${num(r.hours_planned, 0)} h`, r.part_number ? "Part " + r.part_number : ""].filter(Boolean));
  });
  if (g.goal_days) { const gx = x(g.goal_days + 1); el("line", {x1: gx, x2: gx, y1: m.t - 4, y2: H - m.b, stroke: css("--s3"), "stroke-width": 2}, svg); }
  if (g.day != null) { const tx = x(g.day + .5); el("line", {x1: tx, x2: tx, y1: m.t - 4, y2: H - m.b, stroke: css("--ink2"), "stroke-width": 1.5}, svg); el("text", {x: tx + 3, y: H - m.b - 3, class: "lab"}, svg).textContent = "Today"; }
  host.appendChild(svg);
  v.querySelectorAll("button[data-close]").forEach(b => b.onclick = async () => {
    const r = await askDialog("Close task (sends a closure to the PMSS mirror)", [{k: "user", l: "Your name or role (audited)", v: who(), req: true}]);
    if (!r) return; await post(`/api/amxg/script/${encodeURIComponent(g.asset_id)}/close`, {task_id: b.dataset.close, user: r.user}); route(); });
  $("#addfw").onclick = async () => {
    const wps = [...new Set(g.rows.map(r => r.work_package))];
    const r = await askDialog("Add found work", [{k: "task", l: "Task", req: true}, {k: "work_package", l: "Splice into work package", opts: wps, v: wps[0]},
      {k: "skill", l: "Skill code", opts: Object.keys(g.skills).length ? [...new Set([...Object.keys(g.skills), "AA", "AB", "AE", "AG", "AH", "AM", "AS", "BA", "BS"])] : ["AA"], v: "AA"},
      {k: "start_day", l: "Start day", type: "number", v: g.day != null ? Math.round(g.day) : 1, req: true}, {k: "days", l: "Duration (days)", type: "number", v: 5, req: true},
      {k: "hours", l: "Planned hours", type: "number", v: 40}, {k: "part_number", l: "Part number (optional)"}, {k: "user", l: "Your name or role (audited)", v: who(), req: true}]);
    if (!r) return;
    try { await post(`/api/amxg/script/${encodeURIComponent(g.asset_id)}/found-work`, {...r, gate: g.gate}); route(); } catch (e) { alert(e.message); }
  };
}

window.AMXG_VIEWS = {amxg: viewHub, "amxg-events": viewEvents, "amxg-timelines": viewTimelines, "amxg-tiered": viewTiered,
                     "amxg-status": viewStatus, "amxg-dockboard": viewDockboard, "amxg-bom": viewBom, "amxg-script": viewScript};
})();
