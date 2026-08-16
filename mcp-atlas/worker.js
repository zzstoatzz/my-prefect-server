// mcp.waow.tech — a directory of MCP servers self-published as
// tech.waow.mcp.server records on their authors' PDSes.
//
// GET  /               directory page
// GET  /api/atlas.json current crawl output (built by the mcp-atlas prefect flow)
// POST /api/atlas.json ingest, bearer-authed with the INGEST_TOKEN wrangler secret

const PAGE = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mcp atlas</title>
<meta property="og:title" content="mcp atlas">
<meta property="og:description" content="MCP servers self-published to the atmosphere — one view over the network's tech.waow.mcp.server records">
<meta property="og:type" content="website">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='9' cy='22' r='4' fill='%233987e5'/%3E%3Ccircle cx='22' cy='9' r='4' fill='%23199e70'/%3E%3Ccircle cx='24' cy='24' r='3' fill='%23d95926'/%3E%3Cpath d='M9 22 L22 9 M22 9 L24 24' stroke='%238b949e' stroke-width='1' opacity='0.5'/%3E%3C/svg%3E">
<style>
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #8b949e;
    --border: #21262d; --border-strong: #30363d;
    --surface: #161b22; --surface-2: #1c2129; --accent: #58a6ff;
    --green: #6fbf73; --red: #f85149; --yellow: #d29922;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', ui-monospace, monospace;
    font-size: 14px; background: var(--bg); color: var(--fg); line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  /* ambient color field the glass refracts */
  body::before {
    content: ""; position: fixed; inset: -20%; z-index: -1; pointer-events: none;
    background:
      radial-gradient(38% 34% at 18% 12%, rgba(57,135,229,0.13), transparent 70%),
      radial-gradient(34% 30% at 84% 22%, rgba(25,158,112,0.11), transparent 70%),
      radial-gradient(30% 30% at 60% 88%, rgba(217,89,38,0.09), transparent 70%);
    filter: blur(60px);
  }
  .glassy {
    background: rgba(22,27,34,0.5);
    backdrop-filter: blur(18px) saturate(1.5);
    -webkit-backdrop-filter: blur(18px) saturate(1.5);
    border: 1px solid rgba(255,255,255,0.07);
    border-top-color: rgba(255,255,255,0.12);
    box-shadow: 0 10px 32px rgba(0,0,0,0.35);
  }
  a { color: var(--accent); }
  main { max-width: 1060px; margin: 0 auto; padding: 2.2rem clamp(1rem, 3vw, 2rem) 3rem; }

  .head-row { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  .cta {
    margin-left: auto; border-radius: 999px; padding: 0.42rem 0.95rem;
    font-size: 0.78rem; color: var(--fg); text-decoration: none; white-space: nowrap;
    transition: border-color 0.2s;
  }
  .cta:hover { border-color: rgba(88,166,255,0.55); color: var(--accent); }
  h1 { font-size: 1.3rem; font-weight: 600; letter-spacing: -0.02em; }
  h1 .src { font-size: 0.55rem; color: var(--muted); text-decoration: none; font-weight: 400; vertical-align: middle; }
  h1 .src:hover { color: var(--accent); }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0 1.4rem; max-width: 46rem; }
  .subtitle a { color: var(--muted); }

  /* ---- search ---- */
  .search-wrap { margin-bottom: 0.9rem; }
  #q {
    width: 100%; border-radius: 14px; padding: 0.7rem 1rem;
    font: inherit; font-size: 0.9rem; color: var(--fg); outline: none;
    transition: border-color 0.2s;
  }
  #q::placeholder { color: var(--muted); opacity: 0.75; }
  #q:focus { border-color: rgba(88,166,255,0.5); }

  .card.dim { opacity: 0.32; }
  .card .score { margin-left: 0.4rem; font-size: 0.66rem; color: var(--accent); font-variant-numeric: tabular-nums; }

  /* ---- the map: its own panel, nothing on top of it ---- */
  .map-panel {
    position: relative; border-radius: 16px;
    overflow: hidden; margin-bottom: 0.6rem;
  }
  #atlas { display: block; width: 100%; height: 340px; touch-action: manipulation; }
  #atlas-tip {
    position: absolute; z-index: 5; display: none; pointer-events: none;
    background: rgba(13,17,23,0.95); border: 1px solid var(--border-strong);
    border-radius: 6px; padding: 0.4rem 0.6rem; font-size: 0.74rem; white-space: nowrap;
  }
  /* selection card — slides up inside the map panel, Apple-Maps style */
  #sel {
    position: absolute; z-index: 6; left: 0.8rem; right: 0.8rem; bottom: 0.8rem;
    max-width: 26rem;
    background: rgba(28,33,41,0.72);
    backdrop-filter: blur(24px) saturate(1.6);
    -webkit-backdrop-filter: blur(24px) saturate(1.6);
    border: 1px solid rgba(255,255,255,0.1); border-top-color: rgba(255,255,255,0.16);
    border-radius: 14px; padding: 0.8rem 1rem;
    box-shadow: 0 12px 32px rgba(0,0,0,0.55);
    transform: translateY(8px); opacity: 0; pointer-events: none;
    transition: transform 0.18s ease, opacity 0.18s ease;
  }
  #sel.open { transform: translateY(0); opacity: 1; pointer-events: auto; }
  #sel .close {
    position: absolute; top: 0.45rem; right: 0.55rem;
    background: none; border: 0; color: var(--muted); font-size: 1rem;
    cursor: pointer; padding: 0.2rem 0.4rem; border-radius: 6px; font-family: inherit;
  }
  #sel .close:hover { color: var(--fg); background: var(--border); }
  #sel h3 { font-size: 0.95rem; }
  #sel .meta { color: var(--muted); font-size: 0.74rem; margin: 0.1rem 0 0.35rem; }
  #sel p { font-size: 0.8rem; color: var(--fg); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  #sel .go { display: inline-block; margin-top: 0.5rem; font-size: 0.76rem; }
  .map-foot {
    display: flex; gap: 0.5rem 1.4rem; flex-wrap: wrap; align-items: center;
    color: var(--muted); font-size: 0.74rem; margin-bottom: 2rem; padding: 0 0.2rem;
  }
  .map-foot .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 0.35rem; vertical-align: -1px; }
  .map-foot .note { opacity: 0.85; }
  .map-foot a { color: var(--muted); text-decoration: none; }
  .map-foot a:hover { color: var(--accent); }

  /* ---- status + provenance ---- */
  .meta-row {
    display: flex; gap: 0.6rem 1.5rem; flex-wrap: wrap; align-items: baseline;
    margin-bottom: 1.2rem; font-size: 0.8rem;
  }
  .verdict .sdot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.45rem; }
  .verdict.ok .sdot { background: var(--green); }
  .verdict.behind .sdot { background: var(--red); }
  .provenance { color: var(--muted); font-size: 0.74rem; }
  .provenance strong { color: var(--fg); font-weight: 500; font-variant-numeric: tabular-nums; }

  /* ---- the grid: cards that show everything at rest ---- */
  .grid {
    display: grid; gap: 0.9rem;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  }
  .card {
    border-radius: 16px; overflow: hidden;
    padding: 1rem 1.1rem 0.9rem; display: flex; flex-direction: column; gap: 0.55rem;
    transition: border-color 0.2s, box-shadow 0.3s;
    scroll-margin-top: 1.5rem;
  }
  .card:hover { border-color: color-mix(in oklab, var(--pub) 45%, transparent); }
  .card.flash { box-shadow: 0 0 0 2px var(--pub), 0 10px 32px rgba(0,0,0,0.35); }
  .card-head { display: flex; align-items: center; gap: 0.5rem; min-width: 0; }
  .card-head .name { font-weight: 600; font-size: 0.95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .lang {
    flex: none; width: 18px; height: 18px; border-radius: 5px;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 0.58rem; font-weight: 700; letter-spacing: 0.02em;
  }
  .lang.python { background: #3776ab; color: #ffd43b; }
  .lang.typescript { background: #3178c6; color: #fff; }
  .lang.javascript { background: #f7df1e; color: #000; }
  .lang.go { background: #00add8; color: #fff; }
  .lang.rust { background: #b7410e; color: #fff; }
  .lang.zig { background: #f7a41d; color: #000; }
  .card-head .name a { color: var(--fg); text-decoration: none; }
  .card-head .name a:hover { text-decoration: underline; }
  .pill {
    margin-left: auto; font-size: 0.68rem; padding: 0.1rem 0.55rem;
    border-radius: 999px; border: 1px solid var(--border-strong); white-space: nowrap;
  }
  .pill.live { color: var(--green); border-color: rgba(111,191,115,0.4); }
  .pill.auth { color: var(--yellow); border-color: rgba(210,153,34,0.4); }
  .pill.down { color: var(--red); border-color: rgba(248,81,73,0.4); }
  .pill.local { color: var(--muted); }
  .card .desc { color: var(--muted); font-size: 0.8rem; }
  .chips { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; min-width: 0; }
  .chip {
    font-size: 0.66rem; border: 1px solid rgba(255,255,255,0.08); border-radius: 6px;
    padding: 0.02rem 0.38rem; color: var(--muted); background: rgba(13,17,23,0.45);
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chip[title] { cursor: help; }
  .chip.req { border-color: rgba(248,81,73,0.45); color: var(--fg); }
  .chips .label { color: var(--muted); font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.06em; margin-right: 0.2rem; flex: none; }
  .more {
    font-size: 0.7rem; color: var(--accent); background: none; border: 0;
    cursor: pointer; padding: 0.05rem 0.3rem; font-family: inherit;
  }
  .more:hover { text-decoration: underline; }
  .card-foot {
    margin-top: auto; padding-top: 0.5rem; border-top: 1px solid rgba(255,255,255,0.06);
    display: flex; align-items: center; gap: 0.45rem; font-size: 0.74rem;
  }
  .card-foot .pub-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--pub); }
  .card-foot .pub-name { color: var(--muted); text-decoration: none; }
  .card-foot .pub-name:hover { color: var(--accent); }
  .card-foot .links { margin-left: auto; display: flex; gap: 0.8rem; }

  footer.page { color: var(--muted); font-size: 0.74rem; margin-top: 2.4rem; line-height: 1.8; }
  footer.page code { color: var(--fg); }
  .empty { color: var(--muted); padding: 1rem 0; }
</style>
</head>
<body>
<main>
  <div class="head-row">
    <h1>mcp atlas <a class="src" href="https://tangled.org/zzstoatzz.io/my-prefect-server/tree/main/mcp-atlas">[src]</a></h1>
    <a class="cta glassy" href="/publish">get your server listed →</a>
  </div>
  <p class="subtitle">MCP servers, self-published to the atmosphere — each entry is a
  <code>tech.waow.mcp.server</code> record on its author's own PDS. this page is one view over them.
  <a href="/api/atlas.json">atlas.json</a></p>

  <div class="search-wrap">
    <input id="q" class="glassy" type="search" placeholder="find a server in english — 'price used gpus', 'draw into my repo', …" autocomplete="off" spellcheck="false">
  </div>
  <div class="map-panel glassy">
    <canvas id="atlas"></canvas>
    <div id="atlas-tip"></div>
    <div id="sel">
      <button class="close" aria-label="close">×</button>
      <h3 id="sel-name"></h3>
      <div class="meta" id="sel-meta"></div>
      <p id="sel-desc"></p>
      <a class="go" id="sel-go" href="#">view full card ↓</a>
    </div>
  </div>
  <div class="map-foot" id="map-foot"></div>

  <div class="meta-row">
    <span class="verdict" id="verdict"></span>
    <span class="provenance" id="provenance"></span>
  </div>

  <div id="grid" class="grid"><p class="empty">loading…</p></div>

  <footer class="page">
    crawled from the network via <a href="https://relay.waow.tech">relay</a>
    <code>listReposByCollection</code> · lexicon published at
    <a href="https://pdsls.dev/at://did:plc:xbtmt2zjwlrfegqvch7fboei/com.atproto.lexicon.schema/tech.waow.mcp.server">com.atproto.lexicon.schema</a><br>
    publish yours: put a <code>tech.waow.mcp.server</code> record on your PDS — the next crawl finds it.
    <a href="/publish">step-by-step instructions →</a>
  </footer>
</main>
<script>
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const safeUrl = (u) => /^https?:\\/\\//.test(String(u ?? "")) ? u : null;
const SLOTS = ["--s1", "--s2", "--s3", "--s4"];
const cssVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const statusOf = (s) => !s.url
  ? { cls: "local", label: "local · stdio" }
  : s.alive
    ? (s.authRequired ? { cls: "auth", label: "live · auth" } : { cls: "live", label: "live" })
    : { cls: "down", label: "down" };

fetch("/api/atlas.json").then((r) => r.ok ? r.json() : null).then((atlas) => {
  const grid = document.getElementById("grid");
  if (!atlas || !atlas.servers?.length) {
    grid.innerHTML = '<p class="empty">no servers crawled yet.</p>';
    return;
  }
  const servers = atlas.servers;
  const dids = [...new Set(servers.map((s) => s.did))].sort();
  const slot = (did) => SLOTS[Math.min(dids.indexOf(did), SLOTS.length - 1)];

  // ---- verdict + provenance
  const remotes = servers.filter((s) => s.url);
  const down = remotes.filter((s) => !s.alive);
  const verdict = document.getElementById("verdict");
  verdict.className = "verdict " + (down.length ? "behind" : "ok");
  verdict.innerHTML = '<span class="sdot"></span>' + (down.length
    ? down.length + " of " + remotes.length + " hosted servers not answering"
    : "all " + remotes.length + " hosted servers answering");
  const ago = atlas.generatedAt
    ? Math.max(0, Math.round((Date.now() - new Date(atlas.generatedAt)) / 60000)) + "m ago"
    : "unknown";
  const toolCount = servers.reduce((n, s) => n + (s.tools?.length ?? 0), 0);
  document.getElementById("provenance").innerHTML =
    "crawled <strong>" + ago + "</strong> · <strong>" + servers.length + "</strong> servers · <strong>" +
    dids.length + "</strong> publishers · <strong>" + toolCount + "</strong> tools";

  // ---- map legend + caption
  document.getElementById("map-foot").innerHTML =
    dids.map((did) => {
      const s = servers.find((v) => v.did === did);
      return '<span><span class="swatch" style="background:var(' + slot(did) + ')"></span>' +
        '<a href="https://pdsls.dev/at://' + esc(did) + '/tech.waow.mcp.server">@' + esc(s.handle || did) + "</a></span>";
    }).join("") +
    '<span class="note">closer dots = more similar servers · filled = hosted, hollow = local, red = down</span>';

  // ---- cards: everything visible at rest, no hidden state
  const MAX_CHIPS = 4;
  const LANGS = { python: "py", typescript: "TS", javascript: "JS", go: "Go", rust: "rs", zig: "zig" };
  grid.innerHTML = servers.map((s, i) => {
    const st = statusOf(s);
    const repo = safeUrl(s.repo), url = safeUrl(s.url);
    const tools = s.tools ?? [];
    const chip = (t, cls) => '<span class="chip ' + (cls || "") + '"' +
      (t.description ? ' title="' + esc(t.description) + '"' : "") + ">" + esc(t.name) +
      (t.required ? "*" : "") + "</span>";
    const shown = tools.slice(0, MAX_CHIPS).map((t) => chip(t)).join("");
    const hidden = tools.slice(MAX_CHIPS).map((t) => chip(t)).join("");
    const toolsRow = tools.length
      ? '<div class="chips"><span class="label">tools</span>' + shown +
        (hidden ? '<span class="hidden-chips" hidden>' + hidden + "</span>" +
          '<button class="more" data-more>+' + (tools.length - MAX_CHIPS) + " more</button>" : "") + "</div>"
      : "";
    const envRow = (s.environment ?? []).length
      ? '<div class="chips"><span class="label">env</span>' +
        s.environment.map((v) => chip(v, v.required ? "req" : "")).join("") + "</div>"
      : "";
    const pkgRow = (s.packages ?? []).length
      ? '<div class="chips"><span class="label">install</span>' +
        s.packages.map((p) => '<span class="chip">' + esc(p.registry) + ":" + esc(p.identifier) + "</span>").join("") + "</div>"
      : "";
    const lang = LANGS[s.language]
      ? '<span class="lang ' + esc(s.language) + '" title="' + esc(s.language) + '">' + LANGS[s.language] + "</span>"
      : "";
    return '<article class="card glassy" id="card-' + i + '" style="--pub:var(' + slot(s.did) + ')">' +
      '<div class="card-head">' + lang + '<span class="name">' +
      (repo ? '<a href="' + esc(repo) + '">' + esc(s.name) + "</a>" : esc(s.name)) +
      '</span><span class="pill ' + st.cls + '">' + st.label + "</span></div>" +
      '<div class="desc">' + esc(s.description) + "</div>" +
      toolsRow + envRow + pkgRow +
      '<div class="card-foot">' +
      '<span class="pub-dot"></span>' +
      '<a class="pub-name" href="https://pdsls.dev/at://' + esc(s.did) + '/tech.waow.mcp.server">@' + esc(s.handle || s.did) + "</a>" +
      '<span class="links">' +
      (url ? '<a href="' + esc(url) + '">endpoint</a>' : "") +
      (s.uri ? '<a href="https://pdsls.dev/' + esc(s.uri) + '">record</a>' : "") +
      "</span></div></article>";
  }).join("");
  grid.querySelectorAll("[data-more]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const hiddenSpan = btn.previousElementSibling;
      const open = !hiddenSpan.hidden;
      hiddenSpan.hidden = open;
      btn.textContent = open ? "+" + hiddenSpan.children.length + " more" : "show fewer";
    });
  });

  // ---- search: english in, ranked grid + dimmed constellation out
  const qEl = document.getElementById("q");
  let searchSeq = 0;
  let matchSet = null; // null = no active search; Set of indices otherwise
  async function runSearch(q) {
    const seq = ++searchSeq;
    if (!q) {
      matchSet = null;
      servers.forEach((_, i) => {
        const card = document.getElementById("card-" + i);
        card.classList.remove("dim");
        card.style.order = "";
        card.querySelector(".score")?.remove();
      });
      draw();
      return;
    }
    const resp = await fetch("/api/search?q=" + encodeURIComponent(q));
    if (!resp.ok || seq !== searchSeq) return;
    const { results } = await resp.json();
    if (seq !== searchSeq) return;
    const scores = new Map(results.map((r) => [r.i, r.score]));
    const ranked = [...results].sort((a, b) => b.score - a.score);
    const cut = ranked.length ? Math.max(0.6 * ranked[0].score, ranked[0].score - 0.12) : 0;
    matchSet = new Set(ranked.filter((r) => r.score >= cut).map((r) => r.i));
    ranked.forEach((r, rank) => {
      const card = document.getElementById("card-" + r.i);
      card.style.order = rank;
      card.classList.toggle("dim", !matchSet.has(r.i));
      card.querySelector(".score")?.remove();
      if (matchSet.has(r.i)) {
        const el = document.createElement("span");
        el.className = "score";
        el.textContent = scores.get(r.i).toFixed(2);
        card.querySelector(".card-head").appendChild(el);
      }
    });
    draw();
  }
  let debounce;
  qEl.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => runSearch(qEl.value.trim()), 350);
  });
  qEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { clearTimeout(debounce); runSearch(qEl.value.trim()); }
    if (e.key === "Escape") { qEl.value = ""; runSearch(""); }
  });

  // ---- constellation: contained, fully interactive, selection card inside
  const canvas = document.getElementById("atlas");
  const tipEl = document.getElementById("atlas-tip");
  const selEl = document.getElementById("sel");
  const mapped = servers.map((s, i) => ({ s, i })).filter((m) => typeof m.s.x === "number" && typeof m.s.y === "number");
  let pts = [], hovered = null, selected = null;

  function layout() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const padX = Math.max(36, w * 0.07), padY = 40;
    pts = mapped.map((m) => ({
      ...m,
      px: padX + m.s.x * (w - 2 * padX),
      py: padY + m.s.y * (h - 2 * padY),
    }));
    const MIN = 30;
    for (let pass = 0; pass < 12; pass++)
      for (let a = 0; a < pts.length; a++) for (let b = a + 1; b < pts.length; b++) {
        const dx = pts[b].px - pts[a].px, dy = pts[b].py - pts[a].py;
        const d = Math.hypot(dx, dy) || 0.01;
        if (d < MIN) {
          const push = (MIN - d) / 2, ux = dx / d, uy = dy / d;
          pts[a].px -= ux * push; pts[a].py -= uy * push;
          pts[b].px += ux * push; pts[b].py += uy * push;
        }
      }
    draw();
  }
  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    // faint links from each server to its nearest semantic neighbor,
    // so proximity reads as structure instead of scatter
    ctx.save();
    ctx.strokeStyle = "rgba(139,148,158,0.16)";
    ctx.lineWidth = 1;
    const drawnLinks = new Set();
    for (const p of pts) {
      let best = null, bestD = Infinity;
      for (const q of pts) {
        if (q === p) continue;
        const d = Math.hypot(q.px - p.px, q.py - p.py);
        if (d < bestD) { bestD = d; best = q; }
      }
      if (!best) continue;
      const key = [Math.min(p.i, best.i), Math.max(p.i, best.i)].join("-");
      if (drawnLinks.has(key)) continue;
      drawnLinks.add(key);
      ctx.beginPath();
      ctx.moveTo(p.px, p.py);
      ctx.lineTo(best.px, best.py);
      ctx.stroke();
    }
    ctx.restore();
    const w = canvas.clientWidth;
    for (const p of pts) {
      const c = cssVar(slot(p.s.did));
      const active = hovered === p || selected === p;
      ctx.save();
      if (matchSet && !matchSet.has(p.i)) ctx.globalAlpha = 0.22;
      ctx.shadowColor = c; ctx.shadowBlur = active ? 20 : 10;
      ctx.beginPath();
      ctx.arc(p.px, p.py, active ? 7.5 : 6, 0, Math.PI * 2);
      if (!p.s.url) { ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.stroke(); }
      else if (p.s.alive) { ctx.fillStyle = c; ctx.fill(); }
      else { ctx.strokeStyle = cssVar("--red"); ctx.lineWidth = 2; ctx.stroke(); }
      if (selected === p) {
        ctx.shadowBlur = 0;
        ctx.beginPath(); ctx.arc(p.px, p.py, 12, 0, Math.PI * 2);
        ctx.strokeStyle = c; ctx.lineWidth = 1.5; ctx.globalAlpha = 0.6; ctx.stroke();
      }
      // name label, flipped to the left near the right edge
      ctx.shadowBlur = 0;
      ctx.font = "11px 'SF Mono', ui-monospace, monospace";
      ctx.fillStyle = active ? cssVar("--fg") : cssVar("--muted");
      const flip = p.px > w - 110;
      ctx.textAlign = flip ? "right" : "left";
      ctx.fillText(p.s.name, p.px + (flip ? -12 : 12), p.py + 4);
      ctx.restore();
    }
  }
  const hit = (e) => {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    return pts.find((p) => Math.hypot(p.px - x, p.py - y) < 16) ?? null;
  };
  canvas.addEventListener("pointermove", (e) => {
    const p = hit(e);
    if (p !== hovered) { hovered = p; draw(); }
    canvas.style.cursor = p ? "pointer" : "default";
    if (p && p !== selected) {
      const r = canvas.getBoundingClientRect();
      tipEl.textContent = p.s.name + " · @" + (p.s.handle || p.s.did);
      tipEl.style.left = Math.min(e.clientX - r.left + 12, r.width - 160) + "px";
      tipEl.style.top = e.clientY - r.top - 34 + "px";
      tipEl.style.display = "block";
    } else tipEl.style.display = "none";
  });
  canvas.addEventListener("pointerleave", () => {
    tipEl.style.display = "none";
    if (hovered) { hovered = null; draw(); }
  });
  function select(p) {
    selected = p;
    draw();
    if (!p) { selEl.classList.remove("open"); return; }
    const st = statusOf(p.s);
    document.getElementById("sel-name").textContent = p.s.name;
    document.getElementById("sel-meta").innerHTML =
      "@" + esc(p.s.handle || p.s.did) + ' · <span class="pill ' + st.cls + '">' + st.label + "</span>" +
      " · " + (p.s.tools?.length ?? 0) + " tools";
    document.getElementById("sel-desc").textContent = p.s.description;
    document.getElementById("sel-go").onclick = (e) => {
      e.preventDefault();
      const card = document.getElementById("card-" + p.i);
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 1600);
    };
    tipEl.style.display = "none";
    selEl.classList.add("open");
  }
  canvas.addEventListener("click", (e) => select(hit(e)));
  selEl.querySelector(".close").addEventListener("click", () => select(null));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") select(null); });
  window.addEventListener("resize", layout);
  layout();
});
</script>
</body>
</html>`;

const PUBLISH_PAGE = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>publish to the mcp atlas</title>
<meta property="og:title" content="publish to the mcp atlas">
<meta property="og:description" content="get your MCP server listed on mcp.waow.tech — one record on your own PDS">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='9' cy='22' r='4' fill='%233987e5'/%3E%3Ccircle cx='22' cy='9' r='4' fill='%23199e70'/%3E%3Ccircle cx='24' cy='24' r='3' fill='%23d95926'/%3E%3Cpath d='M9 22 L22 9 M22 9 L24 24' stroke='%238b949e' stroke-width='1' opacity='0.5'/%3E%3C/svg%3E">
<style>
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #8b949e;
    --border: #21262d; --border-strong: #30363d;
    --surface: #161b22; --accent: #58a6ff;
    --green: #6fbf73; --red: #f85149;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', ui-monospace, monospace;
    font-size: 14px; background: var(--bg); color: var(--fg); line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  body::before {
    content: ""; position: fixed; inset: -20%; z-index: -1; pointer-events: none;
    background:
      radial-gradient(38% 34% at 18% 12%, rgba(57,135,229,0.13), transparent 70%),
      radial-gradient(34% 30% at 84% 22%, rgba(25,158,112,0.11), transparent 70%),
      radial-gradient(30% 30% at 60% 88%, rgba(217,89,38,0.09), transparent 70%);
    filter: blur(60px);
  }
  .glassy {
    background: rgba(22,27,34,0.5);
    backdrop-filter: blur(18px) saturate(1.5);
    -webkit-backdrop-filter: blur(18px) saturate(1.5);
    border: 1px solid rgba(255,255,255,0.07);
    border-top-color: rgba(255,255,255,0.12);
    box-shadow: 0 10px 32px rgba(0,0,0,0.35);
  }
  a { color: var(--accent); }
  main { max-width: 760px; margin: 0 auto; padding: 2.2rem clamp(1rem, 3vw, 2rem) 3rem; }
  .back { font-size: 0.8rem; color: var(--muted); text-decoration: none; }
  .back:hover { color: var(--accent); }
  h1 { font-size: 1.3rem; font-weight: 600; letter-spacing: -0.02em; margin: 0.6rem 0 0.3rem; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 1.8rem; }
  .subtitle code, p code, li code, td code { color: var(--fg); background: rgba(255,255,255,0.06); border-radius: 4px; padding: 0.05rem 0.3rem; font-size: 0.92em; }
  section { border-radius: 16px; padding: 1.2rem 1.4rem; margin-bottom: 1.1rem; }
  section h2 { font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; }
  section h2 .n { color: var(--accent); margin-right: 0.4rem; }
  section p, section li { font-size: 0.85rem; color: var(--muted); }
  section p strong { color: var(--fg); font-weight: 600; }
  section ul { padding-left: 1.2rem; margin: 0.4rem 0; }
  .tabs { display: flex; gap: 0.4rem; margin: 0.8rem 0 0; }
  .tab {
    font: inherit; font-size: 0.78rem; color: var(--muted); cursor: pointer;
    background: rgba(13,17,23,0.45); border: 1px solid rgba(255,255,255,0.08);
    border-bottom: 0; border-radius: 8px 8px 0 0; padding: 0.35rem 0.9rem;
  }
  .tab.active { color: var(--fg); background: rgba(13,17,23,0.8); border-color: rgba(255,255,255,0.14); }
  pre {
    background: rgba(13,17,23,0.8); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 0 10px 10px 10px; padding: 0.9rem 1rem; overflow-x: auto;
    font-size: 0.78rem; line-height: 1.5; color: var(--fg);
  }
  pre .c { color: var(--muted); }
  .panel[hidden] { display: none; }
  table { width: 100%; border-collapse: collapse; font-size: 0.8rem; margin-top: 0.6rem; }
  th, td { text-align: left; padding: 0.35rem 0.6rem 0.35rem 0; border-bottom: 1px solid rgba(255,255,255,0.06); vertical-align: top; }
  th { color: var(--muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; }
  td { color: var(--muted); }
  td:first-child { color: var(--fg); white-space: nowrap; }
  .req { color: var(--red); }
  footer { color: var(--muted); font-size: 0.74rem; margin-top: 2rem; }
</style>
</head>
<body>
<main>
  <a class="back" href="/">← mcp atlas</a>
  <h1>publish your MCP server</h1>
  <p class="subtitle">the atlas doesn't have a database of servers — it crawls
  <code>tech.waow.mcp.server</code> records that authors publish on their <strong>own</strong>
  atproto PDS. one record, on your identity, and every crawl finds you. no signup, no approval.</p>

  <section class="glassy">
    <h2><span class="n">1</span>get an app password</h2>
    <p>any atproto account works — a <a href="https://bsky.app">Bluesky</a> account included.
    create an app password at
    <a href="https://bsky.app/settings/app-passwords">settings → app passwords</a>
    (never your real password). your handle (like <code>alice.bsky.social</code>) is the other
    thing you need.</p>
  </section>

  <section class="glassy">
    <h2><span class="n">2</span>publish the record</h2>
    <p>edit the server details, then run it. the record key is your server's name, so
    re-running <strong>updates in place</strong> — safe to wire into your deploy.</p>
    <div class="tabs" role="tablist">
      <button class="tab active" data-tab="py">python</button>
      <button class="tab" data-tab="ts">typescript</button>
    </div>
    <pre class="panel" id="panel-py"><code><span class="c"># publish.py — uv run --with httpx publish.py</span>
<span class="c"># requires: BSKY_HANDLE and BSKY_APP_PASSWORD in the environment</span>
import os, re
from datetime import datetime, timezone

import httpx

SERVER = {
    "$type": "tech.waow.mcp.server",
    "name": "my-server",
    "description": "what your server does, one paragraph.",
    "transport": "http",              <span class="c"># or "stdio" for run-locally servers</span>
    "url": "https://my-server.example.com/mcp",   <span class="c"># omit for stdio</span>
    "repo": "https://github.com/you/my-server",
    "language": "python",
    "tools": [
        {"name": "my_tool", "description": "what it does, one line."},
    ],
    "createdAt": datetime.now(timezone.utc).isoformat(),
}

handle = os.environ["BSKY_HANDLE"]
password = os.environ["BSKY_APP_PASSWORD"]

<span class="c"># handle → DID → PDS</span>
did = httpx.get(
    "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
    params={"handle": handle},
).json()["did"]
doc = httpx.get(f"https://plc.directory/{did}").json()
pds = next(s["serviceEndpoint"] for s in doc["service"] if s["id"] == "#atproto_pds")

<span class="c"># sign in and put the record on YOUR pds</span>
session = httpx.post(
    f"{pds}/xrpc/com.atproto.server.createSession",
    json={"identifier": handle, "password": password},
).json()
rkey = re.sub(r"[^a-z0-9-]+", "-", SERVER["name"].lower()).strip("-")
resp = httpx.post(
    f"{pds}/xrpc/com.atproto.repo.putRecord",
    headers={"Authorization": f"Bearer {session['accessJwt']}"},
    json={
        "repo": did,
        "collection": "tech.waow.mcp.server",
        "rkey": rkey,
        "record": SERVER,
    },
)
resp.raise_for_status()
print(resp.json()["uri"])</code></pre>
    <pre class="panel" id="panel-ts" hidden><code><span class="c">// publish.ts — bun run publish.ts</span>
<span class="c">// requires: BSKY_HANDLE and BSKY_APP_PASSWORD in the environment</span>
const SERVER = {
  $type: "tech.waow.mcp.server",
  name: "my-server",
  description: "what your server does, one paragraph.",
  transport: "http",                <span class="c">// or "stdio" for run-locally servers</span>
  url: "https://my-server.example.com/mcp",     <span class="c">// omit for stdio</span>
  repo: "https://github.com/you/my-server",
  language: "typescript",
  tools: [
    { name: "my_tool", description: "what it does, one line." },
  ],
  createdAt: new Date().toISOString(),
};

const handle = process.env.BSKY_HANDLE!;
const password = process.env.BSKY_APP_PASSWORD!;

<span class="c">// handle → DID → PDS</span>
const { did } = await (await fetch(
  "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=" + handle,
)).json();
const doc = await (await fetch("https://plc.directory/" + did)).json();
const pds = doc.service.find((s) => s.id === "#atproto_pds").serviceEndpoint;

<span class="c">// sign in and put the record on YOUR pds</span>
const session = await (await fetch(pds + "/xrpc/com.atproto.server.createSession", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ identifier: handle, password }),
})).json();
const rkey = SERVER.name.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
const resp = await fetch(pds + "/xrpc/com.atproto.repo.putRecord", {
  method: "POST",
  headers: {
    "content-type": "application/json",
    authorization: "Bearer " + session.accessJwt,
  },
  body: JSON.stringify({
    repo: did,
    collection: "tech.waow.mcp.server",
    rkey,
    record: SERVER,
  }),
});
if (!resp.ok) throw new Error(await resp.text());
console.log((await resp.json()).uri);</code></pre>
  </section>

  <section class="glassy">
    <h2><span class="n">3</span>you're on the map</h2>
    <p>the crawler sweeps the network every 6 hours via relay
    <code>listReposByCollection</code>. after the next sweep your server appears on
    <a href="/">the atlas</a> — hosted servers also get a liveness probe against their
    <code>url</code>. inspect your record any time at
    <code>pdsls.dev/at://&lt;your-did&gt;/tech.waow.mcp.server</code>.</p>
  </section>

  <section class="glassy">
    <h2>record reference</h2>
    <p>full schema lives on the network as
    <a href="https://pdsls.dev/at://did:plc:xbtmt2zjwlrfegqvch7fboei/com.atproto.lexicon.schema/tech.waow.mcp.server">com.atproto.lexicon.schema</a>.
    the useful fields:</p>
    <table>
      <tr><th>field</th><th>meaning</th></tr>
      <tr><td>name <span class="req">*</span></td><td>short name, also used as the record key</td></tr>
      <tr><td>description <span class="req">*</span></td><td>what the server does — this powers english search</td></tr>
      <tr><td>createdAt <span class="req">*</span></td><td>ISO timestamp</td></tr>
      <tr><td>transport</td><td><code>http</code> (hosted at <code>url</code>) or <code>stdio</code> (run locally from <code>repo</code>)</td></tr>
      <tr><td>url</td><td>remote endpoint for hosted servers</td></tr>
      <tr><td>repo</td><td>source repository</td></tr>
      <tr><td>language</td><td><code>python</code>, <code>typescript</code>, <code>javascript</code>, <code>go</code>, <code>rust</code>, <code>zig</code></td></tr>
      <tr><td>tools</td><td>list of <code>{name, description}</code> — also powers search</td></tr>
      <tr><td>environment</td><td>list of <code>{name, required, description}</code> env vars clients must set</td></tr>
      <tr><td>packages</td><td>list of <code>{registry, identifier, version}</code> — <code>pypi</code>, <code>npm</code>, <code>oci</code>, …</td></tr>
    </table>
  </section>

  <footer>records stay on your PDS, under your control — delete the record and you're off
  the atlas at the next crawl. questions: <a href="https://bsky.app/profile/zzstoatzz.io">@zzstoatzz.io</a></footer>
</main>
<script>
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== "panel-" + btn.dataset.tab; });
  });
});
</script>
</body>
</html>`;

const EMBED_MODEL = "@cf/baai/bge-base-en-v1.5";

const serverDoc = (s) =>
  [
    s.name,
    s.description,
    ...(s.tools ?? []).map((t) => t.name + (t.description ? ": " + t.description : "")),
  ].join("\n").slice(0, 2000);

const cosine = (a, b) => {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
};

export default {
  async fetch(request, env) {
    const { pathname, searchParams } = new URL(request.url);

    if (pathname === "/api/search") {
      const q = (searchParams.get("q") ?? "").trim().slice(0, 500);
      if (!q) return Response.json({ query: q, results: [] });
      const [atlasRaw, vectorsRaw] = await Promise.all([
        env.ATLAS.get("atlas.json"),
        env.ATLAS.get("vectors.json"),
      ]);
      const atlas = atlasRaw ? JSON.parse(atlasRaw) : { servers: [] };
      const vectors = vectorsRaw ? JSON.parse(vectorsRaw) : null;
      let results;
      if (vectors && vectors.length === atlas.servers.length) {
        const emb = await env.AI.run(EMBED_MODEL, { text: [q] });
        const qv = emb.data[0];
        results = atlas.servers
          .map((s, i) => ({ i, name: s.name, handle: s.handle, score: cosine(qv, vectors[i]) }))
          .sort((a, b) => b.score - a.score);
      } else {
        // vectors missing (embed failed at ingest) — keyword fallback
        const terms = q.toLowerCase().split(/\\s+/).filter(Boolean);
        results = atlas.servers
          .map((s, i) => {
            const doc = serverDoc(s).toLowerCase();
            return { i, name: s.name, handle: s.handle, score: terms.filter((t) => doc.includes(t)).length / terms.length };
          })
          .sort((a, b) => b.score - a.score);
      }
      return Response.json({ query: q, results }, {
        headers: { "access-control-allow-origin": "*" },
      });
    }

    if (pathname === "/api/atlas.json") {
      if (request.method === "POST") {
        const auth = request.headers.get("authorization") ?? "";
        if (auth !== `Bearer ${env.INGEST_TOKEN}`) {
          return new Response("unauthorized", { status: 401 });
        }
        const body = await request.text();
        let atlas;
        try {
          atlas = JSON.parse(body);
        } catch {
          return new Response("not json", { status: 400 });
        }
        await env.ATLAS.put("atlas.json", body);
        // embed every server now so /api/search only embeds the query.
        // failure is non-fatal: search falls back to keywords until the
        // next successful ingest.
        try {
          const docs = (atlas.servers ?? []).map(serverDoc);
          const emb = docs.length ? await env.AI.run(EMBED_MODEL, { text: docs }) : { data: [] };
          await env.ATLAS.put("vectors.json", JSON.stringify(emb.data));
        } catch (err) {
          await env.ATLAS.delete("vectors.json");
          return new Response("ok (embed failed: " + err + ")");
        }
        return new Response("ok");
      }
      const data = await env.ATLAS.get("atlas.json");
      return new Response(data ?? '{"servers":[]}', {
        headers: {
          "content-type": "application/json",
          "access-control-allow-origin": "*",
          "cache-control": "public, max-age=300",
        },
      });
    }

    if (pathname === "/") {
      return new Response(PAGE, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    if (pathname === "/publish") {
      return new Response(PUBLISH_PAGE, {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    return new Response("not found", { status: 404 });
  },
};
