const $ = (s) => document.querySelector(s);
const state = { config: {}, jobs: [], library: { refs: [], prompts: [], graphs: [] } };
let selected = [];          // ref filenames in picture order
let graphId = null;

/* ------------------------------------------------------------------ helpers */
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", !!bad);
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 4200);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const mmss = (s) => {
  s = Math.max(0, Math.round(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/* --------------------------------------------------------------- graph pick */
function pickGraph(n) {
  const cands = (state.library.graphs || [])
    .filter((g) => typeof g.refs === "number" && g.refs >= n)
    .sort((a, b) => a.refs - b.refs);
  return cands.length ? cands[0] : null;
}

function applyLook() {
  const mode = ($("#graphMode") || {}).value || "anchored";
  const quality = ($("#quality") || {}).value || "standard";
  const g = $("#graphSel");
  if (g) {
    const ids = (state.library.graphs || []).map((x) => x.id);
    let wanted;
    if (mode === "free") wanted = "graph-h3-stock-5ref-sparse";
    else if (selected.length <= 2 && ids.includes("graph-h3-stock-2ref")) wanted = "graph-h3-stock-2ref";
    else wanted = "graph-h3-stock-5ref-guide";
    if (ids.includes(wanted)) g.value = wanted;
  }
  const st = $("#steps");
  if (st) st.value = quality === "fast" ? 4 : 6;
  refreshGraphHint();
}

function refreshGraphHint() {
  const chosen = $("#graphSel") && $("#graphSel").value;
  const g = chosen ? (state.library.graphs || []).find((x) => x.id === chosen) : pickGraph(Math.max(1, selected.length));
  graphId = (chosen ? chosen : (g ? g.id : null));
  const hint = $("#graphHint");
  if (!g) {
    hint.textContent = `no graph with ${selected.length || 1}+ reference slots in assets/graphs`;
    hint.style.color = "var(--bad)";
    return;
  }
  const extra = g.refs - selected.length;
  hint.style.color = "var(--dim)";
  hint.textContent = `graph ${g.id} · ${g.refs} slot${g.refs > 1 ? "s" : ""}` +
    (extra > 0 ? ` (${extra} unused)` : "");
}

/* ------------------------------------------------------------------- render */
function renderRefs() {
  const box = $("#refs");
  box.innerHTML = "";
  const all = state.library.refs || [];

  const add = document.createElement("div");
  add.className = "ref add";
  add.innerHTML = `<div class="plus">＋</div><div class="lbl">add images</div>`;
  add.onclick = () => $("#upload").click();
  box.appendChild(add);

  if (!all.length) {
    const note = document.createElement("div");
    note.className = "refEmpty";
    note.textContent = "No images yet — add a face, outfit, or scene style with ＋.";
    box.appendChild(note);
  }

  all.forEach((name) => {
    const i = selected.indexOf(name);
    const el = document.createElement("div");
    el.className = "ref" + (i >= 0 ? " sel" : "");
    el.innerHTML = `<img src="/assets/refs/${encodeURIComponent(name)}" loading="lazy" alt="">
      <span class="ord">${i + 1}</span>
      <button class="del" title="remove from library">✕</button>
      <span class="nm">${name}</span>`;
    el.onclick = () => {
      const k = selected.indexOf(name);
      if (k >= 0) selected.splice(k, 1);
      else selected.push(name);
      renderRefs();
    };
    el.querySelector(".del").onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Remove ${name} from the library?`)) return;
      try {
        await api(`/api/refs/${encodeURIComponent(name)}`, { method: "DELETE" });
        selected = selected.filter((n) => n !== name);
        await loadState(true);
        renderRefs();
      } catch (e) { toast(e.message, true); }
    };
    box.appendChild(el);
  });
  $("#refCount").textContent = all.length ? `· ${selected.length}/${all.length} selected` : "";
  refreshGraphHint();
}

function statusChip(j) {
  return `<span class="state ${j.status}">${j.status.replace("_", " ")}</span>`;
}

function elapsed(j) {
  if (j.status === "COMPLETED") {
    return j.executionTime ? `${(j.executionTime / 1000).toFixed(0)}s render` : "done";
  }
  const t0 = j.submittedAt || j.createdAt;
  if (!t0) return "";
  const base = `${mmss(Date.now() / 1000 - t0)} elapsed`;
  if (j.status === "IN_PROGRESS" && j.delayTime) return `${base} · queued ${mmss(j.delayTime / 1000)}`;
  if (j.status === "IN_QUEUE" && j.delayTime) return `in queue ${mmss(j.delayTime / 1000)}`;
  return base;
}

function renderJobs() {
  const box = $("#jobs");
  if (!state.jobs.length) {
    box.innerHTML = '<div class="empty">nothing queued yet</div>';
    return;
  }
  const groups = {};
  for (const j of state.jobs) (groups[j.group || j.name] ||= []).push(j);
  const names = Object.keys(groups).sort((a, b) =>
    Math.max(...groups[b].map((j) => j.createdAt)) - Math.max(...groups[a].map((j) => j.createdAt)));

  box.innerHTML = "";
  for (const g of names) {
    const list = groups[g].sort((a, b) => a.seed - b.seed);
    const done = list.filter((j) => j.status === "COMPLETED" && j.file);
    const head = document.createElement("div");
    head.className = "grouphead";
    head.innerHTML = `<span>${g}</span><span class="line"></span>` +
      (done.length > 1 ? `<button class="mini" data-cmp="${g}">Compare ${done.length}</button>` : "");
    box.appendChild(head);
    for (const j of list) {
      const el = document.createElement("div");
      el.className = "job";
      el.innerHTML = `
        <div>
          <div class="t">${j.name} <span style="color:var(--dim);font-weight:400">seed ${j.seed}</span></div>
          <div class="m">${j.seconds}s · ${j.videoSize} · ${j.refs.length} ref${j.refs.length > 1 ? "s" : ""}` +
            (j.payloadMB ? ` · ${j.payloadMB} MB` : "") +
            (j.bytes ? ` · ${(j.bytes / 1048576).toFixed(1)} MB out` : "") +
            (j.batchParked ? ` · <span style="color:var(--accent)">endpoint parked</span>` : "") +
            `<br>${j.error ? `<span style="color:var(--bad)">${j.error}</span>` : elapsed(j)}</div>
        </div>
        <div class="acts">
          ${j.status === "COMPLETED" && j.file
            ? `<a class="mini" href="/media/${encodeURIComponent(j.file)}" target="_blank">Play</a>
               <a class="mini" href="/download/${encodeURIComponent(j.file)}">Save</a>`
            : ""}
          <button class="mini" data-det="${j.id}">Details</button>
          ${["IN_QUEUE", "IN_PROGRESS", "RUNNING", "UPLOADING"].includes(j.status)
            ? `<button class="mini" data-cancel="${j.id}">Cancel</button>` : ""}
          ${["COMPLETED", "FAILED", "CANCELLED", "ERROR", "TIMED_OUT"].includes(j.status)
            ? `<button class="mini" data-del="${j.id}">✕</button>` : ""}
          ${statusChip(j)}
        </div>`;
      box.appendChild(el);
    }
  }
  box.querySelectorAll("[data-cmp]").forEach((b) => (b.onclick = () => openCompare(b.dataset.cmp)));
  box.querySelectorAll("[data-cancel]").forEach((b) => (b.onclick = () => cancelJob(b.dataset.cancel)));
  box.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => delJob(b.dataset.del)));
  box.querySelectorAll("[data-det]").forEach((b) => (b.onclick = () => openDetails(b.dataset.det)));
}

/* ------------------------------------------------------------- job details */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString() : "—");
const fmtDur = (ms) => (ms ? `${(ms / 1000).toFixed(1)} s` : "—");

async function openDetails(id) {
  let j;
  try {
    j = await api(`/api/jobs/${id}`);
  } catch (e) {
    return toast(e.message, true);
  }
  $("#modalTitle").textContent = `${j.name} · ${j.status}`;
  const rows = [
    ["Seed", j.seed],
    ["Length", `${j.seconds} s`],
    ["Resolution", j.videoSize],
    ["Graph", j.graph],
    ["References", `${(j.refs || []).length}`],
    ["Payload", j.payloadMB ? `${j.payloadMB} MB` : "—"],
    ["Queue delay", fmtDur(j.delayTime)],
    ["Execution", fmtDur(j.executionTime)],
    ["Output", j.file ? `${j.file}${j.bytes ? ` · ${(j.bytes / 1048576).toFixed(2)} MB` : ""}` : "—"],
    ["Runpod job", j.runpodId || "—"],
    ["Created", fmtTime(j.createdAt)],
    ["Finished", fmtTime(j.completedAt)],
  ];
  const refs = (j.refs || []).map((r, i) => `
    <figure class="refthumb">
      <img src="/assets/refs/${encodeURIComponent(r)}" alt="">
      <figcaption>${i + 1}. ${esc(r)}</figcaption>
    </figure>`).join("");

  $("#cmp").innerHTML = `
    <div class="detail">
      <dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      ${j.error ? `<div class="dberr">${esc(j.error)}</div>` : ""}
      <h3>Prompt</h3>
      <pre class="code">${esc(j.prompt || "(not stored)")}</pre>
      <h3>References</h3>
      <div class="refthumbs">${refs || "<span class='hint'>none</span>"}</div>
      <h3>Input sent to the worker
        <button class="mini" id="copyInput" style="margin-left:10px">Copy JSON</button>
      </h3>
      <pre class="code" id="inputJson">${esc(JSON.stringify(j.input, null, 1))}</pre>
    </div>`;
  $("#modal").classList.add("open");
  const copy = $("#copyInput");
  if (copy) {
    copy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(JSON.stringify(j.input, null, 1));
        toast("input copied");
      } catch (e) { toast("clipboard blocked by the browser", true); }
    };
  }
}

let OPT_SIG = "";

function renderOptions() {
  const o = state.options || { loras: [], models: [], defaults: {} };
  const sig = (o.models || []).map((m) => m.id).join("|") + "::" + (o.loras || []).map((l) => l.id).join("|");
  if (sig === OPT_SIG) return;
  OPT_SIG = sig;
  const fill = (sel, items, blank) => {
    const keep = sel.value;
    sel.innerHTML = `<option value="">${blank}</option>` +
      (items || []).filter((x) => x.id).map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
    if ([...sel.options].some((op) => op.value === keep)) sel.value = keep;
  };
  const FEATURED = ["graph-h3-stock-2ref", "graph-h3-stock-5ref", "graph-h3-stock-5ref-guide", "graph-h3-stock-5ref-sparse", "graph-h3-stock-5ref-guide3"];
  const gsel = $("#graphSel");
  if (gsel) {
    const avail = (state.library.graphs || []).filter((g) => FEATURED.includes(g.id));
    const sig = avail.map((g) => g.id).join("|");
    if (gsel.dataset.sig !== sig) {
      gsel.dataset.sig = sig;
      gsel.innerHTML = avail.map((g) => `<option value="${g.id}">${g.id.replace("graph-h3-stock-", "").replace(/-/g, " ")}</option>`).join("");
      applyLook();
    }
  }
  fill($("#model"), o.models, "graph default");
  fill($("#lora"), o.loras, "none — base checkpoint");
  fill($("#lora2"), o.loras, "none");
  const d = o.defaults || {};
  if (!$("#model").dataset.init) {
    $("#model").value = d.model || "";
    $("#lora").value = d.lora || "";
    $("#lora2").value = d.lora2 || "";
    if (d.steps) $("#steps").value = d.steps;
    if (d.loraStrength) $("#loraStrength").value = d.loraStrength;
    if (d.refImageSize) $("#refSize").value = d.refImageSize;
    $("#model").dataset.init = "1";
  }
}

const PRESETS = {
  fast: { model: "", lora: "turbo_ref2v_4step.safetensors",
          lora2: "", steps: 4, refSize: "match" },
  balanced: { model: "", lora: "turbo_ref2v_4step.safetensors",
              lora2: "h3-realism-people-t2v-i2v-r2v.safetensors",
              steps: 4, refSize: "max", lora2Strength: 0.6 },
  quality: { model: "", lora: "h3-realism-people-t2v-i2v-r2v.safetensors",
             lora2: "", steps: 8, refSize: "max", loraStrength: 0.8 },
};

function applyPreset(name) {
  const p = PRESETS[name];
  if (!p) return;
  $("#model").value = p.model || "";
  $("#lora").value = p.lora || "";
  $("#lora2").value = p.lora2 || "";
  $("#steps").value = p.steps;
  $("#refSize").value = p.refSize;
  if (p.loraStrength !== undefined) $("#loraStrength").value = p.loraStrength;
  if (p.lora2Strength !== undefined) $("#lora2Strength").value = p.lora2Strength;
}

function renderStats() {
  const el = $("#queueStats");
  if (!el) return;
  const st = state.stats || {};
  if (!st.clips) { el.textContent = ""; return; }
  const mm = (ms) => `${(ms / 60000).toFixed(1)} min`;
  el.textContent = `${st.clips} clips · median ${mm(st.medianExecMs)} · ${(st.totalExecS / 60).toFixed(0)} min GPU · ~$${st.estCost} at $${st.rate}/hr`;
}

function renderBatches() {
  const box = $("#batches");
  const list = (state.batches || []).slice().reverse();
  if (!list.length) {
    box.innerHTML = '<div class="empty">no batches</div>';
    return;
  }
  box.innerHTML = list.map((b) => `
    <div class="job">
      <div>
        <div class="t">${esc(b.name)} <span style="color:var(--dim);font-weight:400">${b.requestTotal || b.takes} requests</span></div>
        <div class="m">seed ${b.baseSeed} · done ${b.requestCompleted || 0}/${b.requestTotal || b.takes}` +
          ` · failed ${b.requestFailed || 0}` +
          (b.saved ? ` · ${b.saved} saved` : "") +
          (b.error ? ` · <span style="color:var(--bad)">${esc(b.error)}</span>` : "") + `</div>
      </div>
      <div class="acts">
        ${b.status === "ERROR" ? `<button class="mini" data-bretry="${b.localId}">Retry</button>` : ""}
        ${["DRAFT", "FINALIZED", "QUEUED"].includes(b.status)
          ? `<button class="mini" data-bcancel="${b.localId}">Cancel</button>` : ""}
        <button class="mini" data-bdel="${b.localId}">✕</button>
        <span class="state ${b.status}">${b.status}</span>
      </div>
    </div>`).join("");
  box.querySelectorAll("[data-bcancel]").forEach((el) => (el.onclick = () => cancelBatch(el.dataset.bcancel)));
  box.querySelectorAll("[data-bdel]").forEach((el) => (el.onclick = () => deleteBatch(el.dataset.bdel)));
  box.querySelectorAll("[data-bretry]").forEach((el) => (el.onclick = () => retryBatch(el.dataset.bretry)));
}

async function deleteBatch(localId) {
  if (!confirm("Remove this batch record? Results already imported stay on disk.")) return;
  try {
    await api(`/api/batches/${localId}`, { method: "DELETE" });
    loadState(true);
  } catch (e) { toast(e.message, true); }
}

async function retryBatch(localId) {
  try {
    await api(`/api/batches/${localId}/retry`, { method: "POST" });
    toast("batch re-queued");
    loadState(true);
  } catch (e) { toast(e.message, true); }
}

async function cancelBatch(localId) {
  try {
    await api(`/api/batches/${localId}/cancel`, { method: "POST" });
    toast("batch cancelled — queued requests stop, in-flight ones still bill");
    loadState(true);
  } catch (e) { toast(e.message, true); }
}

async function showAvailability() {
  $("#modalTitle").textContent = "GPU availability";
  $("#cmp").innerHTML = '<div class="hint">asking Runpod…</div>';
  $("#modal").classList.add("open");
  try {
    const a = await api("/api/availability");
    if (!a.gpus || !a.gpus.length) {
      $("#cmp").innerHTML = `<div class="hint">The catalogue returned nothing (it does that).<br>
        Endpoint data centres: <b>${(a.dataCenters || []).join(", ") || "—"}</b><br>
        Pools: <b>${(a.pools || []).join(", ") || "—"}</b><br>
        Queue: ${a.health?.jobs?.inQueue ?? "?"} · running ${a.health?.jobs?.inProgress ?? "?"}</div>`;
      return;
    }
    const rows = [];
    for (const g of a.gpus) {
      for (const d of g.dataCenters) {
        rows.push(`<tr>
          <td>${esc(d.id)}</td>
          <td>${esc(g.name)}</td>
          <td class="av-${String(d.availability).toLowerCase()}">${esc(d.availability)}</td>
          <td>${g.hourly ? "$" + g.hourly.toFixed(2) + "/hr" : "—"}</td></tr>`);
      }
    }
    $("#cmp").innerHTML = `
      <div class="hint" style="margin-bottom:10px">
        Endpoint data centres: <b>${(a.dataCenters || []).join(", ")}</b> ·
        queue ${a.health?.jobs?.inQueue ?? 0} · running ${a.health?.jobs?.inProgress ?? 0}
      </div>
      <table class="avail">
        <thead><tr><th>Data centre</th><th>GPU</th><th>Stock</th><th>Price</th></tr></thead>
        <tbody>${rows.join("") || '<tr><td colspan="4">no rows</td></tr>'}</tbody>
      </table>`;
  } catch (e) {
    $("#cmp").innerHTML = `<div class="dberr">${esc(e.message)}</div>`;
  }
}

function renderRoute() {
  const el = $("#routeTo"), btn = $("#go"), c = state.config || {};
  if (!el) return;
  if ((c.backend || "runpod") === "comfyui") {
    const v = VAST || {};
    const st = (v.status || "").toLowerCase();
    const running = /running|loading/.test(st);
    const port = (v.ports || {})["8189"];
    el.innerHTML = `<b>Rendering on Vast.ai</b> · ` +
      (v.configured ? `machine ${v.id} · ${v.status || "unknown"}` : "no machine connected yet") +
      (v.host && port ? ` · ${v.host}:${port}` : "") +
      (running || !v.configured ? "" : `<br><span style="color:var(--warn)">Start the machine first, or open Setup.</span>`);
    el.className = "route " + (running ? "on" : "off");
    if (btn) btn.textContent = "Render on Vast.ai";
  } else {
    el.innerHTML = `<b>Rendering on Runpod</b> · ` +
      (c.endpointId ? `endpoint ${c.endpointId}` : "not connected yet — open Setup") +
      (c.autoPark ? "<br><span style='color:var(--warn)'>Auto-pause is on: the endpoint will pause after each batch.</span>" : "");
    el.className = "route " + (c.endpointId ? "on" : "off");
    if (btn) btn.textContent = "Render on Runpod";
  }
}

function renderPills() {
  const c = state.config;
  const ep = $("#epPill");
  if ((c.backend || "runpod") === "comfyui") {
    const host = (c.comfyUrl || "").replace(/^https?:\/\//, "");
    ep.textContent = host ? `ComfyUI ${host}` : "ComfyUI — not set";
    ep.className = "pill " + (host ? "on" : "off");
  } else {
    ep.textContent = c.endpointId ? `Runpod ${c.endpointId}` : "Runpod — not set";
    ep.className = "pill " + (c.endpointId ? "on" : "off");
  }
  const ks = $("#keyState");
  if (ks) ks.textContent = c.hasKey ? "A key is saved on this computer." : "No key saved yet.";
  const park = $("#park");
  if (park && document.activeElement !== park) park.value = c.autoPark ? "0" : "keep";
}

function renderLibrary() {
  const sel = $("#promptLib");
  sel.innerHTML = '<option value="">— load from library —</option>' +
    (state.library.prompts || []).map((p) => `<option value="${p.id}">${p.title}</option>`).join("");
  renderRefs();
}

/* ------------------------------------------------------------------ actions */
async function loadState(silent) {
  try {
    const s = await api("/api/state");
    Object.assign(state, s);
    if (!silent) {
      renderLibrary();
      fillSettings();
    }
    renderOptions();   // self-heals when the box comes up: rebuilds if the list changed
    renderPills();
    renderSetup();
    renderJobs();
    renderBatches();
    renderStats();
    renderRoute();
    if (!silent) loadVast();
  } catch (e) {
    if (!silent) toast(e.message, true);
  }
}

async function loadPrompt(id) {
  try {
    const txt = await (await fetch(`/assets/prompts/${id}.txt`)).text();
    $("#prompt").value = txt;
    $("#pchars").textContent = txt.length;
    const m = id.match(/scene[-_]?(\d+)/i);
    if (m) $("#name").value = id.replace(/^scene[-_]?/i, "scene-");
  } catch (e) {
    toast("could not load that prompt", true);
  }
}

async function submit() {
  const prompt = $("#prompt").value.trim();
  if (!prompt) return toast("prompt is empty", true);
  if (!selected.length) return toast("pick at least one reference", true);
  if (!graphId) return toast("no suitable graph", true);
  const btn = $("#go");
  btn.disabled = true;
  btn.textContent = "queueing…";
  const mode = $("#mode").value;
  try {
    const body = {
      prompt,
      refs: selected,
      graph: $("#graphSel") && $("#graphSel").value ? $("#graphSel").value : graphId,
      seconds: Number($("#seconds").value),
      videoSize: $("#size").value,
      seed: Number($("#seed").value),
      takes: Number($("#takes").value),
      name: $("#name").value,
      lora: $("#lora") ? $("#lora").value : "turbo_ref2v_4step.safetensors",
      loraStrength: Number($("#loraStrength").value),
      lora2: $("#lora2").value,
      lora2Strength: Number($("#lora2Strength").value),
      model: $("#model").value,
      steps: Number($("#steps").value),
      enhance: $("#enhance").checked,
      upscale: $("#upscale").checked,
      rife: $("#rife").checked,
      refImageSize: $("#refSize").value,
    };
    if (mode === "batch") {
      const r = await api("/api/batch", { method: "POST", body: JSON.stringify(body) });
      toast(`batch queued · ${r.requests} requests · best effort`);
    } else {
      const r = await api("/api/submit", { method: "POST", body: JSON.stringify(body) });
      toast(`${r.created.length} job${r.created.length > 1 ? "s" : ""} queued`);
    }
    $("#seed").value = Number($("#seed").value) + Number($("#takes").value);
    await loadState(true);
    if (window.matchMedia("(max-width: 900px)").matches) setTab(mode === "batch" ? "batches" : "queue");
  } catch (e) {
    toast(e.message, true);
    if (/paused/i.test(e.message)) {
      $("#settings").style.display = "block";
      loadEndpoint();
    }
  } finally {
    btn.disabled = false;
    btn.textContent = state.config.backend === "comfyui" ? "Render on Vast.ai" : "Render on Runpod";
  }
}

async function cancelJob(id) {
  try {
    await api(`/api/jobs/${id}/cancel`, { method: "POST" });
    toast("cancelled");
    loadState(true);
  } catch (e) { toast(e.message, true); }
}

async function delJob(id) {
  try {
    await api(`/api/jobs/${id}`, { method: "DELETE" });
    loadState(true);
  } catch (e) { toast(e.message, true); }
}

function openCompare(group) {
  const vids = state.jobs.filter((j) => j.group === group && j.status === "COMPLETED" && j.file);
  $("#modalTitle").textContent = `${group} · ${vids.length} takes`;
  $("#cmp").innerHTML = vids.map((j) => `
    <figure>
      <video src="/media/${encodeURIComponent(j.file)}" controls preload="metadata"></video>
      <figcaption><span>${j.name}</span><span>seed ${j.seed}</span></figcaption>
    </figure>`).join("");
  $("#modal").classList.add("open");
}

async function health() {
  const p = $("#epPill");
  p.textContent = "worker …";
  p.className = "pill";
  try {
    const h = await api("/api/health");
    if (h.error) throw new Error(h.error);
    const w = h.workers || {};
    const n = (w.running || 0) + (w.idle || 0);
    p.textContent = n ? `worker ${w.running ? "running" : "idle"}` : "worker cold";
    p.className = "pill " + (n ? "on" : "");
    const q = h.jobs || {};
    toast(`queue ${q.inQueue || 0} · running ${q.inProgress || 0} · done ${q.completed || 0}`);
  } catch (e) {
    p.textContent = "worker ?";
    p.className = "pill off";
    toast(e.message, true);
  }
}

/* ---------------------------------------------------------------- settings */
function fillSettings() {
  $("#endpoint").value = state.config.endpointId || "";
  $("#dsize").value = state.config.defaultVideoSize || "1344x768";
  $("#poll").value = state.config.pollSeconds || 4;
  $("#apikey").value = "";
  $("#vastKey").value = "";
  $("#vastInst").value = state.config.vastInstanceId || "";
  $("#vastRate").value = state.config.vastRate || 1.2;
  $("#autoStop").value = state.config.autoStopMin ?? 10;
  const backend = state.config.backend || "runpod";
  $("#backend").value = backend;
  $("#comfyUrl").value = state.config.comfyUrl || "";
  $("#comfyRow").style.display = backend === "comfyui" ? "block" : "none";
}

let VAST = null;
async function loadVast() {
  try {
    VAST = await api("/api/vast");
  } catch (e) {
    VAST = { error: e.message };
  }
  const btn = $("#vastToggle");
  const st = (VAST.status || "").toLowerCase();
  const running = /running|loading|starting/.test(st);
  btn.textContent = running ? "Stop Vast" : "Start Vast";
  btn.disabled = !VAST.configured;
  btn.classList.toggle("danger", running);
  const bits = [];
  if (!VAST.configured) bits.push("Vast not configured — add API key + instance id in Settings");
  else {
    bits.push(`instance ${VAST.id}`);
    bits.push(st || "unknown");
    if (VAST.gpu) bits.push(VAST.gpu);
    if (VAST.rate) bits.push(`$${Number(VAST.rate).toFixed(2)}/hr`);
    if (VAST.error) bits.push(VAST.error);
  }
  $("#targetState").textContent = bits.join(" · ");
  const vb = $("#vastBtn");
  if (vb) {
    const label = !VAST.configured ? "Vast —" : (running ? "Vast ● running" : `Vast ○ ${st || "stopped"}`);
    vb.textContent = label;
    vb.className = "pill act " + (running ? "on" : (VAST.configured ? "off" : ""));
    vb.disabled = !VAST.configured;
  }
  renderRoute();
}

async function vastToggle() {
  const running = /running|loading|starting/.test((VAST?.status || "").toLowerCase());
  const btn = $("#vastToggle");
  btn.disabled = true;
  btn.textContent = running ? "stopping…" : "starting…";
  try {
    await api("/api/vast/action", { method: "POST", body: JSON.stringify({ action: running ? "stop" : "start" }) });
    toast(running ? "Vast stopping" : "Vast starting — provisioning can take minutes");
  } catch (e) {
    toast(e.message, true);
  }
  await loadVast();
}

async function loadEndpoint() {
  const out = $("#wstate");
  out.textContent = "…";
  try {
    const e = await api("/api/endpoint");
    const w = e.workers || {};
    $("#wmax").value = w.max ?? 0;
    out.textContent = `max ${w.max} · min ${w.min} · idle ${w.idleTimeout}s` + (w.max === 0 ? " · PAUSED" : "");
    out.style.color = w.max === 0 ? "var(--bad)" : "var(--dim)";
  } catch (e) {
    out.textContent = e.message;
    out.style.color = "var(--bad)";
  }
}

async function applyWorkers() {
  const n = Number($("#wmax").value);
  const btn = $("#applyW");
  btn.disabled = true;
  try {
    const r = await api("/api/endpoint/workers", { method: "POST", body: JSON.stringify({ max: n }) });
    toast(n === 0 ? "endpoint paused (max 0)" : `workers.max = ${n}`);
    $("#wstate").textContent = `max ${r.workers.max} · min ${r.workers.min} · idle ${r.workers.idleTimeout}s` +
      (r.workers.max === 0 ? " · PAUSED" : "");
    $("#wstate").style.color = n === 0 ? "var(--bad)" : "var(--dim)";
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function saveSettings() {
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify({
        endpointId: $("#endpoint").value.trim(),
        apiKey: $("#apikey").value.trim(),
        defaultVideoSize: $("#dsize").value,
        defaultSeconds: Number($("#seconds").value) || 10,
        pollSeconds: Number($("#poll").value) || 4,
        backend: $("#backend").value,
        comfyUrl: $("#comfyUrl").value.trim(),
        vastApiKey: $("#vastKey").value.trim(),
        vastInstanceId: $("#vastInst").value.trim(),
        vastRate: Number($("#vastRate").value) || 1.2,
        autoStopMin: Number($("#autoStop").value) || 0,
      }),
    });
    toast("saved");
    $("#apikey").value = "";
    await loadState(true);
  } catch (e) { toast(e.message, true); }
}

async function uploadFiles(files) {
  for (const f of files) {
    const b64 = await new Promise((res) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.readAsDataURL(f);
    });
    try {
      const out = await api("/api/upload-ref", {
        method: "POST",
        body: JSON.stringify({ name: f.name, b64 }),
      });
      selected.push(out.name);
    } catch (err) { toast(`${f.name}: ${err.message}`, true); }
  }
  await loadState(true);
  renderRefs();
  toast(`${files.length} image${files.length > 1 ? "s" : ""} added`);
}

async function savePrompt() {
  const text = $("#prompt").value.trim();
  if (!text) return toast("nothing to save", true);
  const name = window.prompt("Save prompt as", $("#name").value || "clip-01");
  if (!name) return;
  try {
    const r = await api("/api/prompts", { method: "POST", body: JSON.stringify({ id: name, text }) });
    await loadState(true);
    renderLibrary();
    $("#promptLib").value = r.id;
    toast(`saved ${r.id}`);
  } catch (e) { toast(e.message, true); }
}

async function deletePrompt() {
  const id = $("#promptLib").value;
  if (!id) return toast("pick a saved prompt first", true);
  if (!confirm(`Delete prompt "${id}"?`)) return;
  try {
    await api(`/api/prompts/${encodeURIComponent(id)}`, { method: "DELETE" });
    $("#prompt").value = "";
    $("#pchars").textContent = "0";
    await loadState(true);
    renderLibrary();
    $("#promptLib").value = "";
    toast("deleted");
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------------- setup */
let SETUP_PROVIDER = "runpod";
let SETUP_SHOWN = false;

function selectSetupProvider(name) {
  SETUP_PROVIDER = name === "vast" ? "vast" : "runpod";
  document.querySelectorAll(".providerCard").forEach((b) => {
    b.classList.toggle("sel", b.dataset.provider === SETUP_PROVIDER);
  });
  $("#setupRunpod").hidden = SETUP_PROVIDER !== "runpod";
  $("#setupVast").hidden = SETUP_PROVIDER !== "vast";
}

function showSetup(force) {
  const cfg = state.config || {};
  SETUP_PROVIDER = (cfg.backend || "runpod") === "comfyui" ? "vast" : "runpod";
  $("#setupEndpoint").value = cfg.endpointId || "";
  $("#setupKey").value = "";
  $("#setupVastKey").value = "";
  $("#setupVastInst").value = cfg.vastInstanceId || "";
  $("#setupComfyUrl").value = cfg.comfyUrl || "";
  $("#setupNote").hidden = true;
  $("#setupClose").hidden = !force;
  selectSetupProvider(SETUP_PROVIDER);
  $("#setup").classList.add("open");
  document.body.classList.add("setup-open");
}

function closeSetup() {
  $("#setup").classList.remove("open");
  document.body.classList.remove("setup-open");
}

function renderSetup() {
  const s = state.setup || {};
  const banner = $("#setupBanner");
  if (banner) banner.hidden = !(s.done && !s.ready);
  if (s.needed && !SETUP_SHOWN) {
    SETUP_SHOWN = true;
    showSetup(false);
  }
}

async function saveSetup() {
  const note = $("#setupNote");
  note.hidden = true;
  const body = {
    setupDone: true,
    setupSkipped: false,
    backend: SETUP_PROVIDER === "vast" ? "comfyui" : "runpod",
  };
  if (SETUP_PROVIDER === "runpod") {
    const endpointId = $("#setupEndpoint").value.trim();
    const apiKey = $("#setupKey").value.trim();
    if (!endpointId || !apiKey) {
      note.textContent = "Add both the endpoint ID and the API key. Don't have them yet? Choose “Skip for now” and come back later.";
      note.hidden = false;
      return;
    }
    body.endpointId = endpointId;
    body.apiKey = apiKey;
  } else {
    const vastApiKey = $("#setupVastKey").value.trim();
    const vastInstanceId = $("#setupVastInst").value.trim();
    const comfyUrl = $("#setupComfyUrl").value.trim();
    if (!vastApiKey) {
      note.textContent = "Add your Vast.ai API key. Don't have one yet? Choose “Skip for now” and come back later.";
      note.hidden = false;
      return;
    }
    if (!vastInstanceId && !comfyUrl) {
      note.textContent = "Add the Vast.ai machine ID, or a ComfyUI address if you run ComfyUI elsewhere.";
      note.hidden = false;
      return;
    }
    body.vastApiKey = vastApiKey;
    body.vastInstanceId = vastInstanceId;
    body.comfyUrl = comfyUrl;
  }
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    closeSetup();
    await loadState(false);
    toast(SETUP_PROVIDER === "runpod"
      ? "Runpod connected. Add a reference image and render your first clip."
      : "Vast.ai connected. Add a reference image and render your first clip.");
  } catch (e) {
    note.textContent = e.message;
    note.hidden = false;
  }
}

async function skipSetup() {
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify({ setupDone: true, setupSkipped: true }) });
  } catch (e) {
    /* the local UI can still be used; the banner will return on the next load */
  }
  closeSetup();
  await loadState(false);
  toast("Setup skipped. You can finish it any time with the Setup button.");
}

/* -------------------------------------------------------------------- tabs */
function setTab(name) {
  document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + name));
  document.querySelectorAll("#tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  window.scrollTo(0, 0);
  if (name !== "create") loadState(true);
}
document.querySelectorAll("#tabbar button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));

/* -------------------------------------------------------------------- wire */
$("#settingsBtn").onclick = () => {
  const s = $("#settings");
  const open = s.style.display === "none";
  s.style.display = open ? "block" : "none";
  if (open) loadEndpoint();
};
$("#setupBtn").onclick = () => showSetup(true);
$("#setupBannerBtn").onclick = () => showSetup(true);
$("#setupAgain").onclick = () => {
  $("#settings").style.display = "none";
  showSetup(true);
};
$("#setupClose").onclick = closeSetup;
$("#setupSkip").onclick = skipSetup;
$("#setupSave").onclick = saveSetup;
document.querySelectorAll(".providerCard").forEach((b) => {
  b.onclick = () => selectSetupProvider(b.dataset.provider);
});
$("#clearVastKey").onclick = async () => {
  await api("/api/config", { method: "POST", body: JSON.stringify({ clearVastKey: true }) });
  toast("Vast.ai key forgotten");
  loadState(false);
};
$("#applyW").onclick = applyWorkers;
$("#healthBtn").onclick = health;
$("#availBtn").onclick = showAvailability;
$("#healthBtn2").onclick = health;
$("#availBtn2").onclick = showAvailability;
$("#saveCfg").onclick = saveSettings;
$("#clearKey").onclick = async () => {
  await api("/api/config", { method: "POST", body: JSON.stringify({ clearKey: true }) });
  toast("key forgotten");
  loadState(true);
};
$("#go").onclick = submit;
$("#park").onchange = async (e) => {
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify({ autoPark: e.target.value === "0" }) });
    toast(e.target.value === "0"
      ? "will park the endpoint when the batch finishes"
      : "endpoint will be left as it is");
  } catch (err) { toast(err.message, true); }
};
$("#refresh").onclick = () => loadState(false);
$("#clearRefs").onclick = () => { selected = []; renderRefs(); };
$("#newPrompt").onclick = () => {
  $("#prompt").value = "";
  $("#pchars").textContent = "0";
  $("#promptLib").value = "";
  $("#prompt").focus();
};
$("#savePrompt").onclick = savePrompt;
$("#delPrompt").onclick = deletePrompt;
$("#promptLib").onchange = (e) => e.target.value && loadPrompt(e.target.value);
$("#lora").onchange = () => {
  // the turbo LoRA is distilled for 4 steps; the base checkpoint needs more
  $("#steps").value = $("#lora").value ? 4 : 8;
};
$("#preset").onchange = (e) => applyPreset(e.target.value);
$("#graphSel").onchange = () => refreshGraphHint();
$("#graphMode").onchange = () => applyLook();
$("#quality").onchange = () => applyLook();
$("#backend").onchange = async () => {
  $("#comfyRow").style.display = "block";
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify({ backend: $("#backend").value }) });
  } catch (e) { toast(e.message, true); }
  OPT_SIG = "";
  await loadState(false);
};
$("#vastToggle").onclick = vastToggle;
$("#vastBtn").onclick = vastToggle;
$("#prompt").oninput = (e) => ($("#pchars").textContent = e.target.value.length);
$("#closeModal").onclick = () => $("#modal").classList.remove("open");
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.remove("open"); };
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit();
  if (e.key === "Escape") $("#modal").classList.remove("open");
});

$("#upload").onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (files.length) await uploadFiles(files);
};

/* drag & drop onto the reference grid */
{
  const box = $("#refs");
  ["dragenter", "dragover"].forEach((ev) =>
    box.addEventListener(ev, (e) => { e.preventDefault(); box.classList.add("drop"); }));
  ["dragleave", "drop"].forEach((ev) =>
    box.addEventListener(ev, (e) => { e.preventDefault(); box.classList.remove("drop"); }));
  box.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) uploadFiles(files);
    else toast("drop image files", true);
  });
}

/* -------------------------------------------------------------------- boot */
loadState(false);
let _tick = 0;
setInterval(() => {
  loadState(true);
  if (++_tick % 4 === 0) loadVast();   // re-read Vast state every ~20 s
}, 5000);
setInterval(() => { if (state.jobs.some((j) => j.status === "IN_PROGRESS" || j.status === "IN_QUEUE")) renderJobs(); }, 1000);
