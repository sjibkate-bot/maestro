// Admin console (M_12) and "my account". Loaded before the main Maestro script; uses its
// helpers ($, esc, dt, api, ME) at call time.
(() => {
const send = (url, method, body) => fetch(url, {method, headers: {"Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined})
  .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.status); return j; });
const head = (title, sub) => `<div class="titlebar"><div><div class="mono eyebrow">M_12 // Admin console</div><h1>${title}</h1>${sub ? `<div class="mono" style="color:var(--muted)">${sub}</div>` : ""}</div></div>`;
const STATUS = {ACTIVE: "good", PENDING: "warning", DISABLED: "", REJECTED: "critical"};
const TABS = [["requests", "Access requests"], ["users", "Users"], ["groups", "Groups & modules"], ["resets", "Passcode resets"], ["log", "Sign-in log"], ["modules", "Industry & modules"]];

async function viewAdmin(v) {
  if (!(ME.modules || []).includes("admin")) { v.innerHTML = head("ADMIN CONSOLE") + '<p class="empty">Administrators only.</p>'; return; }
  const tab = (location.hash.split("/")[1] || "requests");
  const [users, gr, resets] = await Promise.all([api("/api/admin/users"), api("/api/admin/groups"), api("/api/admin/reset-requests")]);
  const pending = users.filter(u => u.status === "PENDING");
  v.innerHTML = head("ADMIN CONSOLE", `${users.filter(u => u.status === "ACTIVE").length} active users · ${pending.length} waiting for approval · ${resets.filter(r => r.status === "REQUESTED").length} passcode reset request(s)`) +
    `<div class="row" style="margin-bottom:14px">${TABS.map(([k, l]) => `<a class="tag" href="#admin/${k}" style="text-decoration:none;${k === tab ? "color:var(--accent);border-color:var(--accent)" : "color:var(--ink2)"}">${l}${k === "requests" && pending.length ? ` · ${pending.length}` : ""}${k === "resets" && resets.length ? ` · ${resets.length}` : ""}</a>`).join("")}</div>
    <div id="aMsg"></div><div id="aBody"></div>`;
  const msg = (t, bad) => $("#aMsg").innerHTML = `<p style="color:${bad ? "var(--critical)" : "var(--good)"}">${bad ? "✗ " : "✓ "}${esc(t)}</p>`;
  const groupBoxes = (sel, name) => gr.groups.map(g => `<label style="display:inline-flex;gap:4px;margin:2px 8px 2px 0;font-size:12px"><input type="checkbox" name="${name}" value="${esc(g.name)}" ${sel.includes(g.name) ? "checked" : ""}>${esc(g.name)}</label>`).join("");
  const B = $("#aBody");

  if (tab === "requests") {
    B.innerHTML = pending.length ? pending.map(u => `<div class="panel" data-u="${esc(u.username)}"><div class="row" style="justify-content:space-between">
        <div><b style="font-size:16px">${esc(u.username)}</b> · ${esc(u.email)} · ${esc(u.department)}<div style="color:var(--muted);font-size:12px">requested ${new Date(u.requested_at || u.created_at).toLocaleString()}</div></div>
        <div class="row"><select class="role"><option value="USER">User</option><option value="ADMIN">Administrator</option></select>
          <button class="ok-btn">Approve</button><button class="plain rej" style="color:var(--critical)">Reject</button></div></div>
        <div style="margin-top:8px"><span class="mono" style="font-size:10px">Groups:</span> ${groupBoxes([], "g-" + u.username)}</div></div>`).join("")
      : '<p class="empty">No access requests waiting.</p>';
    B.querySelectorAll("[data-u]").forEach(p => {
      const u = p.dataset.u;
      p.querySelector(".ok-btn").onclick = async () => {
        const groups = [...p.querySelectorAll(`input[name="g-${CSS.escape(u)}"]:checked`)].map(x => x.value), role = p.querySelector(".role").value;
        if (!groups.length && role !== "ADMIN") return msg("Choose at least one group, so the user can see something", true);
        try { await send(`/api/admin/users/${encodeURIComponent(u)}/approve`, "POST", {groups, role}); await viewAdmin(v); msg(`${u} approved`); } catch (e) { msg(e.message, true); } };
      p.querySelector(".rej").onclick = async () => { const reason = prompt(`Reason for rejecting ${u} (recorded)`, ""); if (reason === null) return;
        try { await send(`/api/admin/users/${encodeURIComponent(u)}/reject`, "POST", {reason}); await viewAdmin(v); msg(`${u} rejected`); } catch (e) { msg(e.message, true); } };
    });
  }

  if (tab === "users") {
    B.innerHTML = `<div class="panel" style="padding:0;overflow:auto"><table><tr><th>User</th><th>Department</th><th>Status</th><th>Role</th><th>Groups</th><th>Last sign-in</th><th></th></tr>
      ${users.filter(u => u.status !== "PENDING").map(u => `<tr data-u="${esc(u.username)}"><td><b>${esc(u.username)}</b><div style="color:var(--muted);font-size:12px">${esc(u.email)}</div></td>
        <td>${esc(u.department)}</td><td><span class="tag ${STATUS[u.status] || ""}">${esc(u.status)}</span>${u.locked_until && new Date(u.locked_until) > new Date() ? ' <span class="tag critical">LOCKED</span>' : ""}</td>
        <td><select class="role" ${u.status === "REJECTED" ? "disabled" : ""}><option ${u.role === "USER" ? "selected" : ""} value="USER">User</option><option ${u.role === "ADMIN" ? "selected" : ""} value="ADMIN">Administrator</option></select></td>
        <td style="min-width:260px">${groupBoxes(u.groups || [], "g-" + u.username)}</td><td>${u.last_login ? new Date(u.last_login).toLocaleString() : "never"}</td>
        <td style="white-space:nowrap"><button class="plain save" style="padding:3px 8px;font-size:10px">Save</button>
          ${u.status === "ACTIVE" ? `<button class="plain dis" style="padding:3px 8px;font-size:10px;color:var(--critical)">Disable</button>` : u.status === "DISABLED" || u.status === "REJECTED" ? `<button class="plain ena" style="padding:3px 8px;font-size:10px">Enable</button>` : ""}
          <button class="plain code" style="padding:3px 8px;font-size:10px" title="Issue a one-time reset code">Reset code</button></td></tr>`).join("")}</table></div>`;
    B.querySelectorAll("tr[data-u]").forEach(tr => {
      const u = tr.dataset.u, patch = async body => { try { await send(`/api/admin/users/${encodeURIComponent(u)}`, "PATCH", body); await viewAdmin(v); msg(`${u} updated`); } catch (e) { msg(e.message, true); } };
      tr.querySelector(".save").onclick = () => patch({role: tr.querySelector(".role").value, groups: [...tr.querySelectorAll(`input[name="g-${CSS.escape(u)}"]:checked`)].map(x => x.value)});
      const d = tr.querySelector(".dis"), e = tr.querySelector(".ena");
      if (d) d.onclick = () => confirm(`Disable ${u}? Their sessions end now.`) && patch({status: "DISABLED"});
      if (e) e.onclick = () => patch({status: "ACTIVE"});
      tr.querySelector(".code").onclick = () => issueCode(u);
    });
  }

  if (tab === "groups") {
    const mods = gr.modules;
    B.innerHTML = `<p style="color:var(--ink2)">A user sees the modules of all their groups. Administrators see everything and the admin console. Access is enforced on the server, not only hidden in the menu.</p>
      <div class="panel" style="padding:0;overflow:auto"><table><tr><th>Group</th><th class="n">Members</th>${Object.values(mods).map(l => `<th style="font-size:11px">${esc(l)}</th>`).join("")}<th></th></tr>
      ${gr.groups.map(g => `<tr data-g="${esc(g.name)}"><td><b>${esc(g.name)}</b><div style="color:var(--muted);font-size:12px">${esc(g.description)}</div></td><td class="n">${g.members}</td>
        ${Object.keys(mods).map(m => `<td style="text-align:center"><input type="checkbox" value="${m}" aria-label="${esc(g.name)}: ${esc(mods[m])}" ${g.modules.includes(m) ? "checked" : ""}></td>`).join("")}
        <td style="white-space:nowrap"><button class="plain save" style="padding:3px 8px;font-size:10px">Save</button>${g.members ? "" : ` <button class="plain del" style="padding:3px 8px;font-size:10px;color:var(--critical)">Delete</button>`}</td></tr>`).join("")}</table></div>
      <div class="panel"><h2 class="mono">New group</h2><div class="row"><input id="ng" placeholder="Group name"><input id="nd" placeholder="Description" style="flex:1"><button id="nadd">Create</button></div></div>`;
    B.querySelectorAll("tr[data-g]").forEach(tr => {
      const g = tr.dataset.g, cur = gr.groups.find(x => x.name === g);
      tr.querySelector(".save").onclick = async () => { try { await send(`/api/admin/groups/${encodeURIComponent(g)}`, "PUT", {description: cur.description, modules: [...tr.querySelectorAll("input:checked")].map(x => x.value)}); await viewAdmin(v); msg(`${g} saved`); } catch (e) { msg(e.message, true); } };
      const d = tr.querySelector(".del"); if (d) d.onclick = async () => { if (!confirm(`Delete group ${g}?`)) return; try { await send(`/api/admin/groups/${encodeURIComponent(g)}`, "DELETE"); await viewAdmin(v); msg(`${g} deleted`); } catch (e) { msg(e.message, true); } };
    });
    $("#nadd").onclick = async () => { const n = $("#ng").value.trim(); if (!n) return; try { await send(`/api/admin/groups/${encodeURIComponent(n)}`, "PUT", {description: $("#nd").value, modules: []}); await viewAdmin(v); msg(`${n} created: tick its modules and save`); } catch (e) { msg(e.message, true); } };
  }

  if (tab === "resets") {
    B.innerHTML = `<p style="color:var(--ink2)">Check that the request is genuine (for example by phone), then issue a one-time code and give it to the user. Codes are valid for 24 hours and work once. The app sends no email.</p>` +
      (resets.length ? `<div class="panel" style="padding:0"><table><tr><th>User</th><th>Requested</th><th>Status</th><th></th></tr>${resets.map(r => `<tr><td><b>${esc(r.username)}</b></td><td>${r.requested_at ? new Date(r.requested_at).toLocaleString() : "—"}</td>
        <td>${r.status === "ISSUED" ? `code issued by ${esc(r.issued_by)}, expires ${new Date(r.expires_at).toLocaleString()}` : "waiting"}</td><td><button class="plain" data-code="${esc(r.username)}" style="padding:3px 8px;font-size:10px">${r.status === "ISSUED" ? "Issue new code" : "Issue code"}</button></td></tr>`).join("")}</table></div>`
        : '<p class="empty">No reset requests.</p>');
    B.querySelectorAll("[data-code]").forEach(b => b.onclick = () => issueCode(b.dataset.code));
  }

  if (tab === "log") {
    const L = await api("/api/admin/sign-in-log");
    B.innerHTML = `<div class="panel" style="padding:0;overflow:auto"><table><tr><th>When</th><th>Event</th><th>User</th><th>Result</th><th>Detail</th><th>From</th></tr>
      ${L.map(x => `<tr><td>${new Date(x.at).toLocaleString()}</td><td>${esc(x.event.replaceAll("_", " "))}</td><td>${esc(x.username)}</td>
        <td>${x.ok ? '<span class="tag good">OK</span>' : '<span class="tag critical">FAILED</span>'}</td><td>${esc(x.detail)}</td><td style="font-size:11px;color:var(--muted)">${esc(x.ip)}</td></tr>`).join("") || '<tr><td colspan=6 class="empty">No events</td></tr>'}</table></div>`;
  }

  if (tab === "modules") {
    const [P, M] = await Promise.all([api("/api/packs"), api("/api/maestro/modules")]);
    const yes = b => b ? '<span class="tag good">yes</span>' : '<span class="tag">no</span>';
    B.innerHTML = `<div class="panel"><h2 class="mono">Industry packs</h2>
      <p style="color:var(--ink2);margin-top:0">A pack adds the data types an industry uses (with their column names) and gives the Gold data products and owners in Governance that industry's names. Enabling a pack can't be undone from here.</p>
      <table><tr><th>Pack</th><th>Adds data types</th><th>Renames in Governance</th><th>Status</th><th></th></tr>
      ${P.map(p => `<tr><td><b>${esc(p.name)}</b><div style="font-size:12px;color:var(--muted)">${esc(p.description)}</div></td>
        <td style="font-size:12px">${p.entities.map(e => esc(e.label || e.name)).join(", ") || "—"}</td>
        <td style="font-size:12px">${p.governance_labels.length ? p.governance_labels.length + " product(s)" : "—"}</td>
        <td>${p.enabled ? `<span class="tag good">ENABLED</span><div style="font-size:11px;color:var(--muted)">${esc(p.enabled_by)} · ${new Date(p.enabled_at).toLocaleDateString()}</div>` : '<span class="tag">off</span>'}</td>
        <td>${p.enabled ? "" : `<button class="ghost" data-pack="${esc(p.id)}">Enable</button>`}</td></tr>`).join("")}</table></div>
      <div class="panel"><h2 class="mono">Data-driven modules</h2>
      <p style="color:var(--ink2);margin-top:0"><b>Auto</b> shows a module only when its kind of data is loaded, so a car maker doesn't see aircraft screens. <b>On</b> or <b>Off</b> overrides that for everyone. Who may open a module is still set by <a href="#admin/groups">groups</a>.</p>
      <table><tr><th>Module</th><th>Data loaded</th><th>Shown now</th><th>Setting</th></tr>
      ${Object.entries(M).map(([k, m]) => `<tr><td><b>${esc(m.label)}</b><div style="font-size:11px;color:var(--muted)">needs ${m.entities.map(esc).join(", ")}</div></td><td>${yes(m.has_data)}</td><td>${yes(m.visible)}</td>
        <td><select data-mod="${k}">${["auto", "on", "off"].map(x => `<option value="${x}" ${m.mode === x ? "selected" : ""}>${x === "auto" ? "Auto (follow data)" : x === "on" ? "Always on" : "Always off"}</option>`).join("")}</select></td></tr>`).join("")}</table>
      <button id="modSave" style="margin-top:10px">Save module settings</button></div>`;
    B.querySelectorAll("[data-pack]").forEach(b => b.onclick = async () => {
      if (!confirm("Enable this industry pack? It adds its data types and renames the Gold products in Governance.")) return;
      try { await send(`/api/packs/${b.dataset.pack}/enable`, "POST", {user: ME.username || localStorage.amxUser || "admin"}); await viewAdmin(v); msg("Pack enabled"); }
      catch (e) { msg(e.message, true); } });
    $("#modSave").onclick = async () => {
      const body = Object.fromEntries([...B.querySelectorAll("[data-mod]")].map(x => [x.dataset.mod, x.value]));
      try { await send("/api/admin/modules", "PUT", body); location.reload(); } catch (e) { msg(e.message, true); } };
  }

  async function issueCode(u) {
    if (!confirm(`Issue a one-time passcode reset code for ${u}? Any earlier code stops working.`)) return;
    try { const r = await send(`/api/admin/users/${encodeURIComponent(u)}/reset-code`, "POST");
      $("#aMsg").innerHTML = `<div class="panel" style="border-color:var(--accent)"><b>Reset code for ${esc(u)}:</b> <span class="mono" style="font-size:20px;letter-spacing:.15em;user-select:all">${esc(r.code)}</span>
        <p style="margin:6px 0 0;color:var(--ink2)">Give it to the user in person or by phone. They enter it at <b>Reset passcode → Step 2</b>. Valid ${r.expires_in_hours} hours, once. It is not shown again.</p></div>`;
      window.scrollTo(0, 0);
    } catch (e) { msg(e.message, true); }
  }
}

async function viewAccount(v) {
  v.innerHTML = head("MY ACCOUNT", `${esc(ME.username || "sign-in off")} · ${esc(ME.role || "")}`) +
    (ME.must_change ? `<div class="panel" style="border-color:var(--warning)"><b>Change the passcode you were given before continuing.</b></div>` : "") +
    `<div class="split"><div class="panel"><h2 class="mono">Change passcode</h2>
      <form id="cp"><label class="mono" for="c0">Current passcode</label><input id="c0" type="password" autocomplete="current-password" style="width:100%">
      <label class="mono" for="c1" style="display:block;margin-top:8px">New passcode</label><input id="c1" type="password" autocomplete="new-password" style="width:100%">
      <label class="mono" for="c2" style="display:block;margin-top:8px">Confirm new passcode</label><input id="c2" type="password" autocomplete="new-password" style="width:100%">
      <p style="color:var(--muted);font-size:12px">At least 10 characters, with letters and digits.</p><button>Change passcode</button><div id="cpMsg"></div></form></div>
    <div class="panel"><h2 class="mono">Access</h2><p><b>Groups:</b> ${esc((ME.groups || []).join(", ") || "—")}</p>
      <p><b>Modules:</b></p><ul>${(ME.modules || []).map(m => `<li>${esc((ME.module_labels || {})[m] || (m === "admin" ? "Admin console" : m))}</li>`).join("")}</ul></div></div>`;
  $("#cp").onsubmit = async e => { e.preventDefault();
    if ($("#c1").value !== $("#c2").value) { $("#cpMsg").innerHTML = '<p style="color:var(--critical)">Passcodes do not match</p>'; return; }
    try { await send("/api/auth/change-passcode", "POST", {current: $("#c0").value, passcode: $("#c1").value}); ME.must_change = false;
      $("#cpMsg").innerHTML = '<p style="color:var(--good)">✓ Passcode changed</p>'; e.target.reset(); }
    catch (err) { $("#cpMsg").innerHTML = `<p style="color:var(--critical)">${esc(err.message)}</p>`; } };
}

window.ADMIN_VIEWS = {admin: viewAdmin, account: viewAccount};
})();
