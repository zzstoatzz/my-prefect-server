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
  a { color: var(--accent); }
  main { max-width: 1060px; margin: 0 auto; padding: 2.2rem clamp(1rem, 3vw, 2rem) 3rem; }

  h1 { font-size: 1.3rem; font-weight: 600; letter-spacing: -0.02em; }
  h1 .src { font-size: 0.55rem; color: var(--muted); text-decoration: none; font-weight: 400; vertical-align: middle; }
  h1 .src:hover { color: var(--accent); }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0 1.4rem; max-width: 46rem; }
  .subtitle a { color: var(--muted); }

  /* ---- the map: its own panel, nothing on top of it ---- */
  .map-panel {
    position: relative; border: 1px solid var(--border); border-radius: 12px;
    background: var(--surface); overflow: hidden; margin-bottom: 0.6rem;
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
    background: var(--surface-2); border: 1px solid var(--border-strong);
    border-radius: 10px; padding: 0.8rem 1rem;
    box-shadow: 0 10px 30px rgba(0,0,0,0.55);
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
    border: 1px solid var(--border); border-radius: 12px; background: var(--surface);
    padding: 1rem 1.1rem 0.9rem; display: flex; flex-direction: column; gap: 0.55rem;
    border-top: 2px solid var(--pub, var(--border));
    transition: border-color 0.15s, box-shadow 0.3s;
    scroll-margin-top: 1.5rem;
  }
  .card:hover { border-color: var(--border-strong); border-top-color: var(--pub); }
  .card.flash { box-shadow: 0 0 0 2px var(--pub); }
  .card-head { display: flex; align-items: center; gap: 0.55rem; }
  .card-head .name { font-weight: 600; font-size: 0.95rem; }
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
  .chips { display: flex; flex-wrap: wrap; gap: 0.3rem; align-items: center; }
  .chip {
    font-size: 0.7rem; border: 1px solid var(--border); border-radius: 6px;
    padding: 0.05rem 0.4rem; color: var(--fg); background: rgba(13,17,23,0.4);
  }
  .chip[title] { cursor: help; }
  .chip.req { border-color: rgba(248,81,73,0.45); }
  .chips .label { color: var(--muted); font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.05em; margin-right: 0.15rem; }
  .more {
    font-size: 0.7rem; color: var(--accent); background: none; border: 0;
    cursor: pointer; padding: 0.05rem 0.3rem; font-family: inherit;
  }
  .more:hover { text-decoration: underline; }
  .card-foot {
    margin-top: auto; padding-top: 0.5rem; border-top: 1px solid var(--border);
    display: flex; align-items: center; gap: 0.9rem; font-size: 0.74rem;
  }
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
  <h1>mcp atlas <a class="src" href="https://tangled.org/zzstoatzz.io/my-prefect-server/tree/main/mcp-atlas">[src]</a></h1>
  <p class="subtitle">MCP servers, self-published to the atmosphere — each entry is a
  <code>tech.waow.mcp.server</code> record on its author's own PDS. this page is one view over them.
  <a href="/api/atlas.json">atlas.json</a></p>

  <div class="map-panel">
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
  const MAX_CHIPS = 6;
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
    return '<article class="card" id="card-' + i + '" style="--pub:var(' + slot(s.did) + ')">' +
      '<div class="card-head"><span class="name">' +
      (repo ? '<a href="' + esc(repo) + '">' + esc(s.name) + "</a>" : esc(s.name)) +
      '</span><span class="pill ' + st.cls + '">' + st.label + "</span></div>" +
      '<div class="desc">' + esc(s.description) + "</div>" +
      toolsRow + envRow + pkgRow +
      '<div class="card-foot">' +
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
    for (const p of pts) {
      const c = cssVar(slot(p.s.did));
      const active = hovered === p || selected === p;
      ctx.save();
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

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);

    if (pathname === "/api/atlas.json") {
      if (request.method === "POST") {
        const auth = request.headers.get("authorization") ?? "";
        if (auth !== `Bearer ${env.INGEST_TOKEN}`) {
          return new Response("unauthorized", { status: 401 });
        }
        const body = await request.text();
        try {
          JSON.parse(body);
        } catch {
          return new Response("not json", { status: 400 });
        }
        await env.ATLAS.put("atlas.json", body);
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
    return new Response("not found", { status: 404 });
  },
};
