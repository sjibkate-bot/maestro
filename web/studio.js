// Ingestion batches and Mapping Studio. Loaded before the main Maestro script; uses its
// helpers ($, esc, num, dt, api, loadAlerts, route) at call time.
(() => {
const ST = {
  AUTO: ["Matched", "var(--s1)"], SUGGESTED: ["Suggested", "#b07a00"], REVIEW: ["Review", "#b07a00"], UNMAPPED: ["No match", "var(--muted)"],
  ACCEPTED: ["✓ Accepted", "var(--good)"], IGNORED: ["Ignored", "var(--muted)"], EXTENSION: ["Extension", "#6250d6"],
};
const OBJ = {PUBLISHED: ["✓ Published", "good"], READY: ["Ready", ""], MAPPING_REQUIRED: ["● Mapping required", "warning"], DRIFT: ["▲ Columns changed", "critical"]};
const RUN = {PASSED: "good", CERTIFIED: "good", COMPLETED: "good", PARTIAL: "warning", PENDING_OWNER: "warning", SKIPPED: "", FAILED: "critical"};
const chip = (s, map) => { const [l, c] = map[s] || [s, ""]; return `<span class="tag ${c}" style="${c && c.startsWith("var") || c.startsWith("#") ? `color:${c}` : ""}">${esc(l)}</span>`; };
const send = (url, method, body) => fetch(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})})
  .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.status); return j; });
const who = () => localStorage.amxUser || "";
function needWho() {
  let u = who();
  if (!u) { u = (prompt("Your name or role (recorded with each decision)") || "").trim(); if (u) localStorage.amxUser = u; }
  return u;
}
function flash(msg, bad) {
  let t = $("#stMsg"); if (!t) return alert(msg);
  t.innerHTML = `<p style="color:${bad ? "var(--critical)" : "var(--good)"};margin:6px 0">${bad ? "✗ " : "✓ "}${esc(msg)}</p>`;
}
const head = (eyebrow, title, sub) => `<div class="titlebar"><div><div class="mono eyebrow"><a href="#config" style="text-decoration:none">Configuration</a> // ${eyebrow}</div><h1>${title}</h1>${sub ? `<div class="mono" style="color:var(--muted)">${sub}</div>` : ""}</div></div>`;

// ------------------------------------------------------------------ delete a batch or one table (confirmed, audited)
async function confirmLoadedDelete(kind, id, onDone) {
  const base = kind === "batch" ? `/api/batches/${id}` : `/api/objects/${id}`;
  const pv = await api(base + "/delete-preview"), dlg = $("#delDlg");
  $("#delTitle").textContent = kind === "batch" ? `Delete batch "${pv.name}"?` : `Delete "${pv.name}" from its batch?`;
  $("#delBody").innerHTML = `<p>This permanently removes what ${kind === "batch" ? "this batch" : "this table"} loaded:</p><ul>
    <li>${pv.tables} table(s) with ${num(pv.staged_rows, 0)} source rows${pv.unpublished_tables ? ` (${pv.unpublished_tables} not yet published)` : ""}</li>
    <li>${num(pv.records, 0)} published record(s) from ${pv.loads} load(s), and ${num(pv.quarantine_items, 0)} quarantine item(s)</li>
    <li>the original file(s), unless another load still uses them</li></ul>
    ${pv.products_affected.length ? `<p>Affected Gold products (certification withdrawn, must be renewed): <b>${esc(pv.products_affected.join(", "))}</b></p>` : ""}
    <p style="color:var(--ink2);font-size:13px">If an earlier load of the same records exists, it becomes the current version again. The deletion is kept in the audit log.</p>`;
  $("#delPhrase").textContent = pv.confirm_with; $("#delConfirm").value = ""; $("#delErr").textContent = "";
  $("#delUser").value = who() || localStorage.govUser || ""; $("#delReason").value = "";
  const check = () => $("#delGo").disabled = !($("#delConfirm").value.trim() === pv.confirm_with && $("#delUser").value.trim());
  $("#delConfirm").oninput = check; $("#delUser").oninput = check; check();
  $("#delCancel").onclick = () => dlg.close();
  $("#delGo").onclick = async () => {
    $("#delGo").disabled = true; $("#delGo").textContent = "Deleting…";
    try { await send(base + "/delete", "POST", {confirm: $("#delConfirm").value.trim(), user: $("#delUser").value.trim(), reason: $("#delReason").value});
      localStorage.amxUser = $("#delUser").value.trim(); dlg.close(); loadAlerts(); onDone && onDone(); }
    catch (e) { $("#delErr").textContent = e.message; check(); }
    finally { $("#delGo").textContent = "Delete permanently"; }
  };
  dlg.showModal(); $("#delConfirm").focus();
}
window.confirmLoadedDelete = confirmLoadedDelete;

async function publishTables(batchId, objectId, after) {
  const u = needWho(); if (!u) return null;
  return send(objectId ? `/api/objects/${objectId}/publish` : `/api/batches/${batchId}/publish`, "POST", {user: u}).then(r => { loadAlerts(); after && after(r); return r; });
}
window.publishTables = publishTables;

// ------------------------------------------------------------------ batch
async function viewBatch(v) {
  const id = location.hash.split("/")[1];
  const b = await api("/api/batches/" + id);
  const toDo = b.objects.filter(o => o.status !== "PUBLISHED"), ready = toDo.filter(o => o.status === "READY");
  v.innerHTML = `<div style="float:right;margin-top:8px"><button class="danger" id="delBatch">Delete batch…</button></div>` + head("Ingestion batch", esc(b.name).toUpperCase(), `Loaded ${dt(b.created_at)} by ${esc(b.created_by)} · ${b.objects.length} source object(s) · ${num(b.rows, 0)} rows · ${b.mode === "auto" ? "publish without review" : "review before publishing"}`) +
    `<div class="stats">${[["Sources", b.objects.length], ["Published", b.counts.PUBLISHED || 0], ["Ready", b.counts.READY || 0], ["Mapping required", (b.counts.MAPPING_REQUIRED || 0) + (b.counts.DRIFT || 0)]]
      .map(([l, n]) => `<div class="stat"><div class="l mono">${l}</div><div class="v">${n}</div></div>`).join("")}</div>
    ${(b.errors || []).length ? `<div class="panel" style="border-color:var(--critical)"><h2 class="mono">Files that could not be read</h2><ul>${b.errors.map(e => `<li>${esc(e.file)}: ${esc(e.error)}</li>`).join("")}</ul></div>` : ""}
    <div class="panel" style="padding:0;overflow:auto"><table><tr><th>Source object</th><th>Entity</th><th class="n">Confidence</th><th class="n">Rows</th><th class="n">Columns</th><th>Status</th><th>What is needed</th><th></th></tr>
    ${b.objects.map(o => `<tr><td><b>${esc(o.name)}</b>${o.direct ? `<div style="color:var(--muted);font-size:12px">read directly (${esc(o.direct)})</div>` : ""}${o.classification && o.classification.profile_reuse ? `<div style="color:var(--muted);font-size:12px">mapped by saved profile v${o.classification.profile_version}</div>` : ""}</td>
      <td>${esc(o.entity)}</td><td class="n">${o.direct ? "—" : num(o.confidence, 0) + "%"}</td><td class="n">${num(o.rows, 0)}</td><td class="n">${o.direct ? "—" : o.fields}</td>
      <td>${chip(o.status, OBJ)}</td><td style="font-size:12px;color:var(--ink2)">${o.status === "PUBLISHED" ? `${num((o.published || {}).records, 0)} records${o.published && o.published.grades ? " · " + Object.entries(o.published.grades).map(([g, n]) => `${n} ${g}`).join(", ") : ""}` : (o.gate || []).map(esc).join("<br>") || "Nothing: ready to publish"}</td>
      <td style="white-space:nowrap">${o.direct ? "" : `<a class="tag" style="color:var(--accent);text-decoration:none" href="#studio/${o._id}">${o.status === "PUBLISHED" ? "View mapping" : "Mapping Studio"}</a>`}
        ${o.status === "READY" ? `<button class="plain" data-pub="${o._id}" style="padding:3px 8px;font-size:10px">Publish</button>` : ""}
        <button class="plain" data-delobj="${o._id}" title="Delete this table and what it loaded" style="padding:3px 8px;font-size:10px;color:var(--critical)">Delete…</button></td></tr>`).join("")}</table></div>
    <div class="panel"><div class="row" style="justify-content:space-between"><div><h2 class="mono" style="margin:0">Validate, govern and publish</h2>
      <p style="color:var(--ink2);font-size:13px;margin:6px 0 0">${toDo.length ? `${ready.length} of ${toDo.length} unpublished object(s) are ready. Objects still needing mapping are held, not dropped.` : "Everything in this batch is published."}</p></div>
      <button id="pub" ${ready.length ? "" : "disabled"}>Validate, govern and publish</button></div><div id="stMsg"></div><div id="runs"></div></div>`;
  const runs = b.runs || [];
  $("#runs").innerHTML = runs.map((r, i) => `<details ${i === 0 ? "open" : ""} style="margin-top:10px"><summary><b>${r.status === "COMPLETED" ? "✓" : r.status === "FAILED" ? "✗" : "●"} Run ${dt(r.started_at)} ${new Date(r.started_at).toLocaleTimeString()}</b> by ${esc(r.requested_by)} · <span class="tag ${RUN[r.status] || ""}">${esc(r.status)}</span> · ${num(r.records || 0, 0)} records</summary>
    <table><tr><th>Stage</th><th>Result</th><th>What happened</th></tr>${(r.stages || []).map(s => `<tr><td class="mono">${esc(s.stage)}</td><td><span class="tag ${RUN[s.status] || ""}">${esc(s.status.replace("_", " "))}</span></td><td>${esc(s.detail)}
      ${(s.held || []).length ? `<ul style="margin:4px 0;color:var(--ink2)">${s.held.map(h => `<li>${esc(h.object)}: ${esc(h.reasons.join("; "))}</li>`).join("")}</ul>` : ""}</td></tr>`).join("")}</table>
    ${r.status !== "FAILED" ? `<p class="row" style="margin:8px 0 0"><a class="tag" style="color:var(--accent)" href="/governance#overview">Open data governance</a><a class="tag" style="color:var(--accent)" href="/supply">Open supply risk</a></p>` : ""}</details>`).join("");
  $("#delBatch").onclick = () => confirmLoadedDelete("batch", id, () => { location.hash = "#config"; });
  v.querySelectorAll("[data-delobj]").forEach(x => x.onclick = () => confirmLoadedDelete("object", x.dataset.delobj, () => viewBatch(v)));
  v.querySelectorAll("[data-pub]").forEach(x => x.onclick = async () => {
    x.disabled = true; x.textContent = "Publishing…";
    try { const r = await publishTables(id, x.dataset.pub); await viewBatch(v); flash(`Table ${r.status.toLowerCase()}: ${num(r.records || 0, 0)} records`, r.status === "FAILED"); }
    catch (e) { flash(e.message, true); x.disabled = false; x.textContent = "Publish"; } });
  $("#pub").onclick = async () => {
    const u = needWho(); if (!u) return;
    $("#pub").disabled = true; $("#stMsg").innerHTML = '<p style="color:var(--muted)">Validating, governing and publishing…</p>';
    try { const r = await send(`/api/batches/${id}/publish`, "POST", {user: u}); loadAlerts(); await viewBatch(v); flash(`Run ${r.status.toLowerCase()}: ${num(r.records || 0, 0)} records`, r.status === "FAILED"); }
    catch (e) { flash(e.message, true); $("#pub").disabled = false; }
  };
}

// ------------------------------------------------------------------ Mapping Studio
async function viewStudio(v) {
  const id = location.hash.split("/")[1];
  const o = await api("/api/objects/" + id);
  const locked = !!o.published;
  const fields = o.entity_fields, req = new Set(fields.filter(f => f.required).map(f => f.field));
  const opt = (sel) => `<option value="">—</option>` + fields.map(f => `<option value="${f.field}" ${f.field === sel ? "selected" : ""}>${f.field}${f.required ? " *" : ""}</option>`).join("");
  const counts = o.fields.reduce((a, f) => (a[f.status] = (a[f.status] || 0) + 1, a), {});
  v.innerHTML = head("Mapping Studio", esc(o.name).toUpperCase(), `Batch <a href="#batch/${o.batch_id}">${esc(o.batch_id.slice(0, 8))}</a> · ${num(o.rows, 0)} rows · ${o.columns} columns`) +
    `<div class="split" style="grid-template-columns:2fr 1fr"><div class="panel">
      <div class="row" style="justify-content:space-between"><div><div class="mono" style="color:var(--muted)">Classified as</div>
        <div class="row" style="margin-top:4px"><select id="ent" ${locked ? "disabled" : ""} aria-label="Entity">${o.entities.map(e => `<option ${e === o.entity ? "selected" : ""}>${e}</option>`).join("")}</select>
        <span class="mono">${num(o.confidence, 0)}% confidence</span></div>
        ${(o.alternatives || []).length ? `<div style="color:var(--muted);font-size:12px;margin-top:4px">Alternatives: ${o.alternatives.map(a => `${esc(a.entity)} ${a.confidence}%`).join(" · ")}</div>` : ""}
        ${o.classification && o.classification.profile_reuse ? `<div style="color:var(--ink2);font-size:12px;margin-top:4px">Mapped by saved profile v${o.classification.profile_version}</div>` : ""}</div>
        <div>${chip(o.status, OBJ)}</div></div>
      ${o.drift ? `<div class="panel" style="border-color:var(--critical);margin:12px 0 0;padding:12px"><b>▲ Columns changed since profile v${o.drift.profile_version}.</b> Added: ${esc(o.drift.added.join(", ") || "none")}. Removed: ${esc(o.drift.removed.join(", ") || "none")}.
        Earlier decisions are kept for the columns that remain. Review the new columns, then save a new profile version. The active profile was not changed.</div>` : ""}
      <div style="margin-top:12px">${o.gate.length ? `<b>Before publishing:</b><ul style="margin:4px 0">${o.gate.map(g => `<li>${esc(g)}</li>`).join("")}</ul>` : locked ? `<b style="color:var(--good)">✓ Published ${dt(o.published.at)} by ${esc(o.published.by)}: ${num(o.published.records, 0)} records.</b>` : `<b style="color:var(--good)">✓ Ready to publish.</b> Save the profile so the next file with these columns maps automatically.`}</div>
      <div id="stMsg"></div></div>
      <div class="panel"><h2 class="mono">Decisions</h2>${Object.entries(counts).map(([k, n]) => `<div class="row" style="justify-content:space-between"><span>${chip(k, ST)}</span><b>${n}</b></div>`).join("")}
        <p style="color:var(--muted);font-size:12px">Accept puts the value in a Maestro field of this entity. Ignore keeps it only in the original file (Bronze). Extension keeps it on the record under a key you name. Columns with no match and no decision are kept as attributes.</p>
        ${locked ? "" : `<div class="row"><button class="plain" id="hi">Accept all ≥ 90%</button><button id="prof">Save &amp; activate profile</button></div>`}
        ${o.profile ? `<p style="font-size:12px;color:var(--ink2)">Profile v${o.profile.version} ${esc(o.profile.status)} · ${esc(o.profile.activated_by || "")} ${dt(o.profile.activated_at)}</p>` : ""}</div></div>
    <div class="panel" style="padding:0;overflow:auto"><table id="ftab"><tr><th>Source column</th><th>Profile</th><th>Maestro field <span style="font-weight:400">(* required)</span></th><th class="n">Confidence</th><th>Status</th><th>Decision</th></tr>
    ${o.fields.map(f => `<tr data-col="${esc(f.name)}" style="${f.status === "IGNORED" ? "opacity:.55" : ""}">
      <td><b>${esc(f.name)}</b><div style="color:var(--muted);font-size:11px">${esc((f.samples || []).join(" • "))}</div></td>
      <td style="font-size:12px;white-space:nowrap">${esc(f.type)}<div style="color:var(--muted)">${num(f.null_pct, 0)}% empty · ${num(f.unique_pct, 0)}% distinct</div></td>
      <td>${f.status === "EXTENSION" ? `<span style="color:#6250d6">ext: ${esc(f.extension_key)}</span>` : `<select class="cf" ${locked ? "disabled" : ""} aria-label="Maestro field for ${esc(f.name)}">${opt(f.canonical_field)}</select>`}
        <div style="color:var(--muted);font-size:11px">${esc(f.why || "")}${(f.candidates || []).length > 1 ? " · also: " + f.candidates.slice(1, 3).map(c => `${c.field} ${c.score}%`).join(", ") : ""}</div></td>
      <td class="n">${num(f.confidence, 0)}%</td><td>${chip(f.status, ST)}${f.decided_by ? `<div style="color:var(--muted);font-size:11px">${esc(f.decided_by)}</div>` : ""}</td>
      <td style="white-space:nowrap">${locked ? "" : `<button class="plain" data-a="ACCEPTED">Accept</button> <button class="plain" data-a="IGNORED">Ignore</button> <button class="plain" data-a="EXTENSION">Extension</button>${["ACCEPTED", "IGNORED", "EXTENSION"].includes(f.status) ? ` <button class="plain" data-a="RESET" title="Back to the suggestion">↺</button>` : ""}`}</td></tr>`).join("")}</table></div>
    <details class="panel"><summary class="mono">Sample rows (${o.sample_rows.length})</summary><div style="overflow:auto"><table><tr>${o.fields.map(f => `<th>${esc(f.name)}</th>`).join("")}</tr>
      ${o.sample_rows.map(r => `<tr>${o.fields.map(f => `<td>${esc(r[f.name])}</td>`).join("")}</tr>`).join("")}</table></div></details>
    <details class="panel"><summary class="mono">Decision log (${o.decisions.length})</summary><table><tr><th>When</th><th>Who</th><th>Column</th><th>Action</th><th>Result</th></tr>
      ${o.decisions.map(d => `<tr><td>${new Date(d.at).toLocaleString()}</td><td>${esc(d.user)}</td><td>${esc(d.field || "—")}</td><td>${esc(d.action)}</td><td>${esc(d.canonical_field || d.extension_key || d.entity || (d.version ? "v" + d.version : ""))}</td></tr>`).join("")}</table></details>`;
  if (locked) return;
  const act = async (col, action, extra) => {
    const u = needWho(); if (!u) return;
    try { await send(`/api/objects/${id}/fields`, "PATCH", {column: col, action, user: u, ...extra}); await viewStudio(v);
      flash(`${col}: ${action === "RESET" ? "back to suggestion" : action.toLowerCase()}`); }
    catch (e) { flash(e.message, true); }
  };
  v.querySelectorAll("#ftab tr[data-col]").forEach(tr => tr.querySelectorAll("button[data-a]").forEach(b => b.onclick = () => {
    const col = tr.dataset.col, a = b.dataset.a;
    if (a === "ACCEPTED") return act(col, a, {canonical_field: tr.querySelector(".cf") ? tr.querySelector(".cf").value : null});
    if (a === "EXTENSION") { const k = prompt(`Extension key for "${col}"`, col.toLowerCase().replace(/[^a-z0-9]+/g, "_")); if (k === null) return; return act(col, a, {extension_key: k}); }
    return act(col, a);
  }));
  v.querySelectorAll("#ftab .cf").forEach(s => s.onchange = () => act(s.closest("tr").dataset.col, "ACCEPTED", {canonical_field: s.value || null}));
  $("#ent").onchange = async () => { const u = needWho(); if (!u) return;
    try { await send(`/api/objects/${id}/entity`, "PATCH", {entity: $("#ent").value, user: u}); await viewStudio(v); flash("Entity changed: suggestions recalculated"); } catch (e) { flash(e.message, true); } };
  $("#hi").onclick = async () => { const u = needWho(); if (!u) return;
    try { const r = await send(`/api/objects/${id}/accept-high-confidence`, "POST", {user: u}); await viewStudio(v); flash(`Accepted ${r.fields.filter(f => f.status === "ACCEPTED").length} field(s)`); } catch (e) { flash(e.message, true); } };
  $("#prof").onclick = async () => { const u = needWho(); if (!u) return;
    try { const r = await send(`/api/objects/${id}/profile`, "POST", {user: u}); await viewStudio(v); flash(`Profile v${r.profile.version} active: the next file with these columns maps automatically`); } catch (e) { flash(e.message, true); } };
}

window.STUDIO_VIEWS = {batch: viewBatch, studio: viewStudio};
})();
