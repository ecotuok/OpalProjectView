"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const KIND_ORDER = ["customization", "product", "investigation", "unknown"];
const KIND_LABEL = { customization: "Customization", product: "Product", investigation: "Investigation", unknown: "Other" };

const state = {
  lib: null,
  project: null,        // current project object (or null for workspace docs)
  docPath: null,
  kinds: new Set(),     // active kind filters (empty = all)
  search: "",
};

// ---------- helpers ----------
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = (epoch) => epoch ? new Date(epoch * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
const api = (u) => fetch(u).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });

// ---------- boot ----------
init();
async function init() {
  try { initTheme(); initFont(); } catch (e) { console.error(e); }
  try { if (localStorage.opalFocus === "1") document.body.classList.add("focus"); } catch (e) {}
  try { bindChrome(); } catch (e) { console.error("bindChrome:", e); }
  try {
    state.lib = await api("/api/library");
  } catch (e) {
    return showFatal("Could not reach the Opal server.\n" + (e.message || e));
  }
  try {
    if (state.lib.error === "no_root" || !state.lib.root) { renderNoRoot(); return; }
    renderSidebar();
    renderRootFoot();
    deepLink();
    startPoll();
  } catch (e) {
    showFatal("Couldn't render the library.\n" + (e.stack || e.message || e));
  }
}

// live-reload the open file when it changes on disk (cheap mtime poll)
function startPoll() {
  setInterval(async () => {
    if (!state.docPath || !state.pollProject) return;
    let r;
    try {
      r = await api(`/api/mtime?project=${encodeURIComponent(state.pollProject)}&path=${encodeURIComponent(state.docPath)}`);
    } catch (e) { return; }
    if (r.mtime && state.pollMtime && r.mtime > state.pollMtime) {
      const top = $("#reader").scrollTop;
      await openDoc(state.pollProject, state.docPath);
      $("#reader").scrollTop = top;       // keep your place across a live reload
    }
  }, 2000);
}

function showFatal(msg) {
  const el = $("#project-list");
  if (el) {
    el.innerHTML = `<div class="noroot"><p>Opal hit a problem.</p>
      <pre style="white-space:pre-wrap;text-align:left;font-size:11px;color:var(--muted)">${esc(msg)}</pre>
      <button class="ctl" id="set-root">Choose folder…</button></div>`;
    const b = $("#set-root"); if (b) b.onclick = setRootFlow;
  }
}

function deepLink() {
  const p = new URLSearchParams(location.search);
  const dp = p.get("project"), dd = p.get("doc");
  if (dp === "_workspace" && dd) { openWorkspace(dd, dd); return; }
  if (dp) {
    const proj = state.lib.projects.find(x => x.id === dp);
    if (proj) { openProject(proj.id); if (dd) setTimeout(() => openDoc(proj.id, dd), 0); }
  }
}

function renderRootFoot() {
  const el = $("#root-path");
  el.innerHTML = `<span class="muted">folder</span> ${esc(state.lib.root)} ` +
    `<button class="root-edit" title="Change projects folder">change</button>`;
  el.querySelector(".root-edit").onclick = setRootFlow;
}

function renderNoRoot() {
  $("#workspace-list").innerHTML = "";
  $("#kind-filters").innerHTML = "";
  $("#project-list").innerHTML = `<div class="noroot">
    <p>Couldn't find your projects folder.</p>
    <p class="muted">Point Opal at your Codittle <code>…/projects</code> directory.</p>
    <button class="ctl" id="set-root">Choose folder…</button></div>`;
  $("#set-root").onclick = setRootFlow;
  $("#root-path").textContent = state.lib.detected ? "detected: " + state.lib.detected : "";
}

async function setRootFlow() {
  const cur = state.lib.root || state.lib.detected || "";
  const v = prompt("Path to your projects folder (the Codittle …/projects directory):", cur);
  if (!v) return;
  try {
    const r = await fetch("/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root: v }),
    });
    if (!r.ok) { alert("Couldn't set folder: " + (await r.text())); return; }
  } catch (e) { alert("Couldn't set folder: " + e.message); return; }
  state.lib = await api("/api/library");
  state.project = null; state.docPath = null;
  $("#doc-wrap").hidden = true; $("#welcome").hidden = false;
  $("#manifest-card").innerHTML = ""; $("#toc").innerHTML = "";
  if (state.lib.error === "no_root" || !state.lib.root) { renderNoRoot(); return; }
  renderSidebar(); renderRootFoot();
}

// ---------- sidebar ----------
function renderSidebar() {
  const ws = state.lib.workspace.map(w =>
    `<button class="ws-item" data-doc="${esc(w.id)}">${esc(w.label)}</button>`).join("");
  $("#workspace-list").innerHTML = ws;
  $$("#workspace-list .ws-item").forEach(b =>
    b.onclick = () => openWorkspace(b.dataset.doc, b.textContent));

  const present = KIND_ORDER.filter(k => state.lib.projects.some(p => p.kind === k));
  $("#kind-filters").innerHTML = present.map(k =>
    `<span class="chip" data-kind="${k}">${KIND_LABEL[k]}</span>`).join("");
  $$("#kind-filters .chip").forEach(c => c.onclick = () => {
    const k = c.dataset.kind;
    state.kinds.has(k) ? state.kinds.delete(k) : state.kinds.add(k);
    c.classList.toggle("on");
    renderProjects();
  });

  renderProjects();
}

function matches(p) {
  if (state.kinds.size && !state.kinds.has(p.kind)) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  const hay = [p.title, p.id, p.summary, p.kind, p.status, p.ticket,
    p.env?.ip, p.env?.label, ...(p.docs || []).map(d => d.label)].join(" ").toLowerCase();
  return hay.includes(q);
}

function renderProjects() {
  const list = $("#project-list");
  const groups = {};
  state.lib.projects.filter(matches).forEach(p => (groups[p.kind] ||= []).push(p));
  let html = "";
  for (const k of KIND_ORDER) {
    const items = groups[k];
    if (!items) continue;
    html += `<div class="kindgroup-label">${KIND_LABEL[k]} · ${items.length}</div>`;
    for (const p of items) html += projectRow(p);
  }
  list.innerHTML = html || `<p class="muted" style="padding:10px">No projects match.</p>`;
  $$(".proj", list).forEach(el => el.onclick = () => openProject(el.dataset.id));
  $$(".proj-open", list).forEach(b => b.onclick = (e) => { e.stopPropagation(); newWindow(b.dataset.id, ""); });
  if (state.project) $(`.proj[data-id="${CSS.escape(state.project.id)}"]`, list)?.classList.add("active");
}

function projectRow(p) {
  const env = p.env || {};
  const unmapped = env.source === "none" || !env.ip || env.ip === "<unmapped>";
  const envTxt = unmapped ? "unmapped" : (env.label && env.label !== "<UNKNOWN>" ? env.label : env.ip);
  const st = String(p.status || "").toLowerCase();
  return `<div class="proj" data-id="${esc(p.id)}" data-kind="${esc(p.kind)}" tabindex="0">
    <button class="proj-open" data-id="${esc(p.id)}" title="Open in a new window">⧉</button>
    <div class="proj-title">${esc(p.title)}</div>
    <div class="proj-meta">
      <span class="dot"></span>
      <span class="st-${esc(st)}">${esc(p.status)}</span>
      <span class="proj-env ${unmapped ? "unmapped" : ""}">${esc(envTxt)}</span>
    </div></div>`;
}

function newWindow(project, doc) {
  const q = new URLSearchParams();
  if (project) q.set("project", project);
  if (doc) q.set("doc", doc);
  fetch("/api/open?" + q.toString(), { method: "POST" }).catch(() => {});
}

function newWindowCurrent() {
  let proj = "", doc = "";
  if (state.project) { proj = state.project.id; doc = state.docPath || ""; }
  else if (state.docPath) { proj = "_workspace"; doc = state.docPath; }
  newWindow(proj, doc);
}

// ---------- open project / docs ----------
function openProject(id) {
  const p = state.lib.projects.find(x => x.id === id);
  if (!p) return;
  state.project = p;
  $("#reader").dataset.kind = p.kind;
  renderManifestCard(p);
  $$(".proj").forEach(el => el.classList.toggle("active", el.dataset.id === id));
  const brief = p.docs.find(d => d.group === "brief") || p.docs.find(d => d.kind === "md") || p.docs[0];
  if (brief) openDoc(p.id, brief.id); else showManifestDoc(p);
}

function openWorkspace(docId, label) {
  state.project = null;
  $("#reader").dataset.kind = "unknown";
  $("#manifest-card").innerHTML = "";
  $$(".proj").forEach(el => el.classList.remove("active"));
  openDoc("_workspace", docId, label);
}

async function openDoc(project, path, kicker) {
  state.docPath = path;
  let data;
  try {
    data = await api(`/api/doc?project=${encodeURIComponent(project)}&path=${encodeURIComponent(path)}`);
  } catch (e) {
    $("#doc").innerHTML = `<p class="muted">Could not open ${esc(path)} (${esc(e.message)})</p>`;
    return;
  }
  state.pollProject = project;            // live-reload baseline
  state.pollMtime = data.mtime || 0;
  $("#welcome").hidden = true;
  $("#doc-wrap").hidden = false;
  $("#doc").dataset.doctype = data.doctype || "markdown";
  renderDocHead(project, path, data, kicker);
  setTitle(state.project ? state.project.title : data.title);
  $("#doc").innerHTML = data.html;
  bindMdLinks($("#doc"));
  buildToc(data.toc);
  renderDocFoot(project, path);
  $("#reader").scrollTop = 0;
  updateProgress();
  highlightTabs();
}

function renderDocHead(project, path, data, kicker) {
  const p = state.project;
  const docMeta = p?.docs.find(d => d.id === path);
  const tabs = p ? p.docs.map(d =>
    `<span class="tab ${d.id === path ? "active" : ""}" data-doc="${esc(d.id)}" data-kind="${esc(d.kind)}">${esc(d.label)}</span>`
  ).join("") : "";
  const isCode = data.doctype === "code";
  const rt = isCode ? (data.lines ? `${data.lines.toLocaleString()} lines` : "")
    : (data.reading_time ? `${data.reading_time} min read` : "");
  const words = data.words ? `${data.words.toLocaleString()} words` : "";
  const upd = data.mtime ? `updated ${fmtDate(data.mtime)}` : "";
  const kick = kicker || docMeta?.label ||
    (isCode ? (path.split("/").slice(0, -1).join("/") || "Source")
      : (project === "_workspace" ? "Workspace" : ""));
  $("#doc-head").innerHTML = `
    ${tabs ? `<div class="doc-tabs">${tabs}</div>` : ""}
    <div class="doc-kicker">${esc(kick)}</div>
    <h1 class="doc-h1">${esc(data.title)}</h1>
    <div class="doc-sub">${[rt, words, upd].filter(Boolean).map(esc).join(" · ")}</div>`;
  $$("#doc-head .tab").forEach(t => t.onclick = () => {
    if (t.dataset.kind === "manifest") showManifestDoc(state.project);
    else openDoc(state.project.id, t.dataset.doc);
  });
}

function renderDocFoot(project, path) {
  const p = state.project;
  if (!p) { $("#doc-foot").innerHTML = ""; return; }
  const mdDocs = p.docs;
  const i = mdDocs.findIndex(d => d.id === path);
  if (i < 0) { $("#doc-foot").innerHTML = ""; return; }   // a source file, not a project doc
  const prev = mdDocs[i - 1], next = mdDocs[i + 1];
  const btn = (d, dir) => d ? `<button data-doc="${esc(d.id)}" data-kind="${esc(d.kind)}">
      <span class="nf-label">${dir}</span>${esc(d.label)}</button>` : "<span></span>";
  $("#doc-foot").innerHTML = btn(prev, "← Previous") + btn(next, "Next →");
  $$("#doc-foot button").forEach(b => b.onclick = () => {
    if (b.dataset.kind === "manifest") showManifestDoc(p);
    else openDoc(p.id, b.dataset.doc);
  });
}

function highlightTabs() {
  $$("#doc-head .tab").forEach(t => t.classList.toggle("active", t.dataset.doc === state.docPath));
}

// ---------- manifest (rail card + full doc view) ----------
function renderManifestCard(p) {
  const env = p.env || {}, ld = p.last_deploy, deps = p.dependencies || {};
  const unmapped = env.source === "none";
  const envVal = unmapped ? `<span class="badge warn">unmapped</span>`
    : `<span class="mf-v mono">${esc(env.label && env.label !== "<UNKNOWN>" ? env.label + " · " : "")}${esc(env.ip || "")}</span>`;
  const depBlock = (label, arr) => (arr && arr.length)
    ? `<div class="mf-section"><h5>${label}</h5><div class="tags">${arr.map(x => `<span class="tag dep">${esc(typeof x === "string" ? x : JSON.stringify(x))}</span>`).join("")}</div></div>` : "";
  $("#manifest-card").innerHTML = `
    <div class="mf-title">Manifest <button class="mf-edit" id="env-edit" title="Set / change environment">edit env</button></div>
    <div class="mf-row"><span class="mf-k">Kind</span><span class="mf-v"><span class="badge">${esc(p.kind)}</span></span></div>
    <div class="mf-row"><span class="mf-k">Status</span><span class="mf-v st-${esc(String(p.status).toLowerCase())}">${esc(p.status)}</span></div>
    ${p.ticket && !/^<|^$/.test(p.ticket) ? `<div class="mf-row"><span class="mf-k">Ticket</span><span class="mf-v">${esc(p.ticket)}</span></div>` : ""}
    <div class="mf-row"><span class="mf-k">Env</span><span class="mf-v">${envVal}${env.source ? ` <span class="src-tag">${esc(env.source)}</span>` : ""}</span></div>
    ${env.bnk_run && env.bnk_run !== "<unknown>" ? `<div class="mf-row"><span class="mf-k">bnk.run</span><span class="mf-v mono">${esc(env.bnk_run)}</span></div>` : ""}
    ${env.note ? `<div class="mf-row"><span class="mf-k">Note</span><span class="mf-v">${esc(env.note)}</span></div>` : ""}
    ${ld && ld.file ? `<div class="mf-row"><span class="mf-k">Deploy</span><span class="mf-v mono">${esc((ld.file || "").split("/").pop())} v${esc(ld.version)}</span></div>` : ""}
    <div id="env-form" hidden></div>
    ${renderSources(p)}
    ${depBlock("Depends · routines", deps.routines)}
    ${depBlock("Depends · files", deps.files)}
    ${depBlock("Depends · params", deps.params)}`;
  $$("#manifest-card .tag.src").forEach(b => b.onclick = () => openDoc(state.project.id, b.dataset.path));
  const eb = $("#env-edit"); if (eb) eb.onclick = () => toggleEnvForm(p);
}

const SRC_LABEL = { routines: "Routines", versions: "Versions", files: "Files", params: "Params", includes: "Includes", java: "Java", docs: "Docs", other: "Other" };
const SRC_ORDER = ["routines", "versions", "files", "params", "includes", "java", "docs", "other"];
function renderSources(p) {
  const src = p.sources || [];
  if (!src.length) return "";
  const byG = {};
  src.forEach(s => (byG[s.group] ||= []).push(s));
  let h = "";
  for (const g of SRC_ORDER) {
    const items = byG[g]; if (!items) continue;
    h += `<div class="mf-section"><h5>${SRC_LABEL[g]} · ${items.length}</h5><div class="tags">` +
      items.map(s => `<button class="tag src ${s.tracked ? "" : "untracked"}" data-path="${esc(s.path)}" title="${esc(s.path)}${s.tracked ? "" : " · untracked (not deployed via Codittle)"}">${esc(s.name)}</button>`).join("") +
      `</div></div>`;
  }
  return h;
}

function toggleEnvForm(p) {
  const f = $("#env-form");
  if (!f) return;
  if (!f.hidden) { f.hidden = true; f.innerHTML = ""; return; }
  const e = p.env || {};
  const val = (x) => x && !/^<|unmapped|unknown/i.test(x) ? esc(x) : "";
  f.hidden = false;
  f.innerHTML = `<div class="envform">
    <label>Label<input id="ef-label" value="${val(e.label)}" placeholder="UAT-KE-130"></label>
    <label>IP<input id="ef-ip" value="${val(e.ip)}" placeholder="192.0.2.10"></label>
    <label>bnk.run<input id="ef-bnk" value="${val(e.bnk_run)}" placeholder="/t24/inst/bnk/bnk.run"></label>
    <label>Note<input id="ef-note" value="${esc(e.note || "")}" placeholder="why / when it changed"></label>
    <div class="envform-btns"><button id="ef-save" class="ctl on">Save</button><button id="ef-cancel" class="ctl">Cancel</button></div>
    <p class="muted" style="font-size:11px;margin:7px 0 0">Saved to <code>_ctx/project.yml</code> &amp; the index — used across the context.</p></div>`;
  $("#ef-cancel").onclick = () => toggleEnvForm(p);
  $("#ef-save").onclick = () => saveEnv(p.id);
}

async function saveEnv(project) {
  const ip = $("#ef-ip").value.trim();
  if (!ip) { alert("IP is required."); return; }
  const body = { project, ip, label: $("#ef-label").value.trim(), bnk_run: $("#ef-bnk").value.trim(), note: $("#ef-note").value.trim() };
  try {
    const r = await fetch("/api/env", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { alert("Save failed: " + (await r.text())); return; }
    const fresh = await r.json();
    const idx = state.lib.projects.findIndex(x => x.id === project);
    if (idx >= 0) state.lib.projects[idx] = fresh;
    state.project = fresh;
    renderManifestCard(fresh);
    renderProjects();
  } catch (e) { alert("Save failed: " + e.message); }
}

function showManifestDoc(p) {
  if (!p) return;
  state.docPath = "_ctx/project.yml";
  state.pollProject = null;               // structured view — no live file reload
  $("#welcome").hidden = true;
  $("#doc-wrap").hidden = false;
  const env = p.env || {};
  const summary = p.summary ? `<p class="lead">${esc(p.summary)}</p>` : "";
  $("#doc-head").innerHTML = `
    <div class="doc-tabs">${p.docs.map(d => `<span class="tab ${d.kind === "manifest" ? "active" : ""}" data-doc="${esc(d.id)}" data-kind="${esc(d.kind)}">${esc(d.label)}</span>`).join("")}</div>
    <div class="doc-kicker">Manifest</div>
    <h1 class="doc-h1">${esc(p.title)}</h1>
    <div class="doc-sub">${esc(p.id)}</div>`;
  $$("#doc-head .tab").forEach(t => t.onclick = () => {
    if (t.dataset.kind === "manifest") showManifestDoc(p);
    else openDoc(p.id, t.dataset.doc);
  });
  setTitle(p.title);
  $("#doc").innerHTML = summary + manifestTableHtml(p);
  $("#doc-foot").innerHTML = "";
  buildToc([]);
  $("#reader").scrollTop = 0; updateProgress();
}

function manifestTableHtml(p) {
  const env = p.env || {}, a = p.artifacts || {}, d = p.dependencies || {}, ld = p.last_deploy || {};
  const row = (k, v) => v ? `<tr><th>${esc(k)}</th><td>${v}</td></tr>` : "";
  const tags = (arr) => (arr && arr.length) ? arr.map(x => `<span class="tag">${esc(typeof x === "string" ? x : JSON.stringify(x))}</span>`).join(" ") : "";
  return `<table><tbody>
    ${row("Kind", `<span class="badge">${esc(p.kind)}</span>`)}
    ${row("Status", esc(p.status))}
    ${row("Ticket", p.ticket && !/^</.test(p.ticket) ? esc(p.ticket) : "")}
    ${row("Owners", (p.owners || []).map(esc).join(", "))}
    ${row("Env", `${esc(env.label || "")} ${env.ip ? "<code>" + esc(env.ip) + "</code>" : ""} ${env.source ? "· " + esc(env.source) : ""}`)}
    ${row("bnk.run", env.bnk_run ? "<code>" + esc(env.bnk_run) + "</code>" : "")}
    ${row("Last deploy", ld.file ? `<code>${esc(ld.file)}</code> v${esc(ld.version)} · ${esc(ld.at || "")}` : "")}
    ${row("Routines", tags(a.routines))}
    ${row("Versions", tags(a.versions))}
    ${row("Files", tags(a.files))}
    ${row("Params", tags(a.params))}
    ${row("Untracked", tags(a.untracked))}
    ${row("Depends · routines", tags(d.routines))}
    ${row("Depends · files", tags(d.files))}
    ${row("Depends · params", tags(d.params))}
  </tbody></table>`;
}

// ---------- TOC + scroll-spy + progress ----------
let tocLinks = [], headings = [];
function buildToc(toc) {
  const nav = $("#toc");
  if (!toc || !toc.length) { nav.innerHTML = ""; tocLinks = []; headings = []; return; }
  nav.innerHTML = `<div class="toc-title">On this page</div>` +
    toc.filter(t => t.level <= 4).map(t =>
      `<a href="#${esc(t.id)}" class="lvl-${t.level}" data-id="${esc(t.id)}">${esc(t.text)}</a>`).join("");
  tocLinks = $$("#toc a");
  tocLinks.forEach(a => a.onclick = (e) => {
    e.preventDefault();
    document.getElementById(a.dataset.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  headings = tocLinks.map(a => document.getElementById(a.dataset.id)).filter(Boolean);
}

function updateProgress() {
  const r = $("#reader");
  const max = r.scrollHeight - r.clientHeight;
  $("#progress").style.width = (max > 0 ? (r.scrollTop / max) * 100 : 0) + "%";
  if (headings.length) {
    let active = headings[0];
    for (const h of headings) { if (h.getBoundingClientRect().top < 140) active = h; else break; }
    tocLinks.forEach(a => a.classList.toggle("active", a.dataset.id === active.id));
  }
}

// ---------- in-app markdown links ----------
function bindMdLinks(root) {
  $$("a.md-link", root).forEach(a => a.onclick = (e) => {
    e.preventDefault();
    const proj = a.dataset.project, path = a.dataset.doc;
    if (proj === "_workspace") { openWorkspace(path, path); return; }
    const p = state.lib.projects.find(x => x.id === proj);
    if (p) { openProject(p.id); setTimeout(() => openDoc(p.id, path), 0); }
    else openDoc(proj, path);
  });
}

// ---------- chrome: theme / font / focus / search / keys ----------
const THEMES = ["day", "night", "paper"];
const THEME_NAME = { day: "Day", night: "Night", paper: "Paper" };
const THEME_BG = { day: "#f4f1ea", night: "#14121a", paper: "#e8e0cf" };
function initTheme() {
  let t = localStorage.opalTheme;
  if (!t) t = matchMedia("(prefers-color-scheme: dark)").matches ? "night" : "day";
  setTheme(t);
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.opalTheme = t;
  const el = $("#theme-name"); if (el) el.textContent = THEME_NAME[t];
  const m = document.querySelector('meta[name="theme-color"]'); if (m) m.content = THEME_BG[t] || "#14121a";
  // night = near-black -> extra-dark tile; day (cream) & paper (sepia) are light -> light tile
  const logo = $("#brand-logo"); if (logo) logo.src = `/static/opal-${t === "night" ? "extra-dark" : "light"}.png?v=8`;
}

function setTitle(s) { document.title = s ? `${s} — Opal` : "Opal — T24 project reader"; }
function cycleTheme() {
  const cur = document.documentElement.dataset.theme;
  setTheme(THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length]);
}
function initFont() {
  const v = parseInt(localStorage.opalFont || "18", 10);
  document.documentElement.style.setProperty("--read", v + "px");
}
function bumpFont(d) {
  let v = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--read")) || 18;
  v = Math.max(15, Math.min(23, v + d));
  document.documentElement.style.setProperty("--read", v + "px");
  localStorage.opalFont = v;
}
function toggleFocus() {
  const on = document.body.classList.toggle("focus");
  localStorage.opalFocus = on ? "1" : "0";
  $("#focus-toggle")?.classList.toggle("on", on);
}

function on(sel, ev, fn) { const el = $(sel); if (el) el.addEventListener(ev, fn); }

function bindChrome() {
  on("#theme-toggle", "click", cycleTheme);
  on("#new-window", "click", newWindowCurrent);
  on("#font-inc", "click", () => bumpFont(1));
  on("#font-dec", "click", () => bumpFont(-1));
  on("#focus-toggle", "click", toggleFocus);
  $("#focus-toggle")?.classList.toggle("on", document.body.classList.contains("focus"));
  on("#reader", "scroll", updateProgress);
  on("#search", "input", (e) => { state.search = e.target.value; renderProjects(); });
  on("#open-playbook", "click", (e) => {
    e.preventDefault();
    const pb = state.lib.workspace.find(w => /playbook/i.test(w.id));
    if (pb) openWorkspace(pb.id, pb.label);
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" && e.key !== "Escape") return;
    if (e.key === "/") { e.preventDefault(); $("#search").focus(); }
    else if (e.key === "f") toggleFocus();
    else if (e.key === "t") cycleTheme();
    else if (e.key === "n") newWindowCurrent();
    else if (e.key === "Escape") {
      if (document.body.classList.contains("focus")) toggleFocus();
      else if ($("#search").value) { $("#search").value = ""; state.search = ""; renderProjects(); }
    }
  });
}
