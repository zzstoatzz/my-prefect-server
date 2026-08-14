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
    --bg: #0d1117; --fg: #c9d1d9; --muted: #8b949e;
    --border: #21262d; --border-strong: #30363d;
    --surface: #161b22; --accent: #58a6ff;
    --green: #6fbf73; --red: #f85149; --yellow: #d29922;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', ui-monospace, monospace;
    font-size: 13px; background: var(--bg); color: var(--fg); line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--accent); }

  /* the constellation — fixed behind everything, interactive in the exposed band */
  #atlas {
    position: fixed; top: 0; left: 0;
    width: 100vw; height: 340px; z-index: 0;
  }
  #atlas-tip {
    position: fixed; z-index: 100; display: none;
    background: rgba(22,27,34,0.92); border: 1px solid var(--border-strong);
    border-radius: 6px; padding: 0.45rem 0.65rem;
    font-size: 0.74rem; pointer-events: none; white-space: nowrap;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  }
  #atlas-tip .n { font-weight: 600; }
  #atlas-tip .h { color: var(--muted); }

  .spacer { height: 300px; }
  .glass {
    position: relative; z-index: 1;
    width: 100%; max-width: 1000px; margin: 0 auto;
    padding: 1.6rem clamp(1rem, 2.5vw, 2rem) 2.5rem;
    background: rgba(13,17,23,0.86);
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-top: 1px solid var(--border);
    border-radius: 12px 12px 0 0;
    min-height: calc(100vh - 300px);
  }
  header.page { position: relative; z-index: 1; max-width: 1000px; margin: 0 auto; padding: 1.2rem clamp(1rem, 2.5vw, 2rem) 0; pointer-events: none; }
  header.page a, header.page .hint { pointer-events: auto; }
  h1 { font-size: 1.25rem; font-weight: 600; letter-spacing: -0.02em; }
  h1 .src { font-size: 0.55rem; color: var(--muted); text-decoration: none; font-weight: 400; vertical-align: middle; }
  h1 .src:hover { color: var(--accent); }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-top: 0.2rem; }
  .subtitle a { color: var(--muted); }
  .hint { color: var(--muted); font-size: 0.68rem; margin-top: 0.5rem; opacity: 0.8; }

  .status-strip {
    margin: 0 0 1rem; font-size: 0.82rem; line-height: 1.6;
    border: 1px solid var(--border); border-radius: 8px;
    padding: 0.55rem 0.85rem; background: rgba(22,27,34,0.7);
  }
  .status-strip:empty { display: none; }
  .sdot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.45rem; }
  .status-strip.ok .sdot { background: var(--green); }
  .status-strip.behind .sdot { background: var(--red); }
  .provenance { color: var(--muted); font-size: 0.74rem; margin: 0 0 1.4rem; }
  .provenance strong { color: var(--fg); font-weight: 500; font-variant-numeric: tabular-nums; }

  .legend { display: flex; gap: 1.1rem; flex-wrap: wrap; font-size: 0.76rem; color: var(--muted); margin: 0 0 1.2rem; }
  .legend .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 0.35rem; vertical-align: -1px; }
  .legend a { color: var(--muted); text-decoration: none; }
  .legend a:hover { color: var(--accent); }

  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left; font-size: 0.68rem; font-weight: 400; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.06em;
    padding: 0.45rem 0.7rem; border-bottom: 1px solid var(--border-strong);
  }
  th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
  tbody tr.row { border-bottom: 1px solid var(--border); cursor: pointer; }
  tbody tr.row:hover { background: rgba(22,27,34,0.75); }
  td { padding: 0.55rem 0.7rem; vertical-align: top; }
  td .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.5rem; vertical-align: 0; }
  td .name { font-weight: 600; color: var(--fg); }
  td .pub { color: var(--muted); font-size: 0.78rem; }
  .badge {
    display: inline-block; font-size: 0.62rem; padding: 0.05rem 0.4rem;
    border: 1px solid var(--border-strong); border-radius: 10px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .st { font-size: 0.76rem; white-space: nowrap; }
  .st.live { color: var(--green); }
  .st.auth { color: var(--yellow); }
  .st.down { color: var(--red); }
  .st.local { color: var(--muted); }

  tr.detail { display: none; }
  tr.detail.open { display: table-row; }
  tr.detail > td {
    background: rgba(22,27,34,0.55); border-bottom: 1px solid var(--border);
    font-size: 0.8rem; color: var(--fg); padding: 0.8rem 1rem 0.9rem;
  }
  .detail .desc { max-width: 60rem; margin-bottom: 0.55rem; }
  .kv { color: var(--muted); font-size: 0.74rem; margin-top: 0.35rem; line-height: 1.8; }
  .kv .k { color: var(--muted); text-transform: uppercase; font-size: 0.62rem; letter-spacing: 0.05em; margin-right: 0.5rem; }
  .chip {
    display: inline-block; border: 1px solid var(--border); border-radius: 4px;
    padding: 0 0.35rem; margin: 0 0.25rem 0.25rem 0; font-size: 0.72rem; color: var(--fg);
  }
  .chip[title] { border-color: var(--border-strong); cursor: help; }
  .req { color: var(--red); }
  .links a { margin-right: 1rem; font-size: 0.76rem; }

  footer { color: var(--muted); font-size: 0.74rem; margin-top: 2rem; line-height: 1.8; }
  footer code { color: var(--fg); }
  .empty { color: var(--muted); padding: 1rem 0; }
  @media (max-width: 640px) {
    .hide-sm { display: none; }
    .spacer { height: 240px; }
    #atlas { height: 280px; }
  }
</style>
</head>
<body>
<canvas id="atlas"></canvas>
<div id="atlas-tip"></div>
<header class="page">
  <h1>mcp atlas <a class="src" href="https://tangled.org/zzstoatzz.io/my-prefect-server/tree/main/mcp-atlas">[src]</a></h1>
  <p class="subtitle">MCP servers, self-published to the atmosphere — each entry is a
  <code>tech.waow.mcp.server</code> record on its author's own PDS. this page is one view over them.
  <a href="/api/atlas.json">atlas.json</a></p>
  <p class="hint">↑ the constellation: closer servers do more similar things. hover a dot; click to jump to its row.</p>
</header>
<div class="spacer"></div>
<main class="glass">
  <div id="status" class="status-strip"></div>
  <p id="provenance" class="provenance"></p>
  <div id="legend" class="legend"></div>
  <div id="content"><p class="empty">loading…</p></div>
  <footer>
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
  ? { cls: "local", label: "local" }
  : s.alive
    ? (s.authRequired ? { cls: "auth", label: "● live (auth)" } : { cls: "live", label: "● live" })
    : { cls: "down", label: "○ down" };

fetch("/api/atlas.json").then((r) => r.ok ? r.json() : null).then((atlas) => {
  const content = document.getElementById("content");
  if (!atlas || !atlas.servers?.length) {
    content.innerHTML = '<p class="empty">no servers crawled yet.</p>';
    return;
  }
  const servers = atlas.servers;
  const dids = [...new Set(servers.map((s) => s.did))].sort();
  const slot = (did) => SLOTS[Math.min(dids.indexOf(did), SLOTS.length - 1)];

  // ---- status strip: the plain-language verdict
  const remotes = servers.filter((s) => s.url);
  const down = remotes.filter((s) => !s.alive);
  const strip = document.getElementById("status");
  if (down.length) {
    strip.className = "status-strip behind";
    strip.innerHTML = '<span class="sdot"></span>' + down.length + " of " + remotes.length +
      " hosted servers not answering: " + down.map((s) => "<strong>" + esc(s.name) + "</strong>").join(", ");
  } else {
    strip.className = "status-strip ok";
    strip.innerHTML = '<span class="sdot"></span>all ' + remotes.length +
      " hosted servers answering an MCP initialize";
  }

  // ---- provenance
  const ago = atlas.generatedAt
    ? Math.max(0, Math.round((Date.now() - new Date(atlas.generatedAt)) / 60000)) + "m ago"
    : "unknown";
  const tools = servers.reduce((n, s) => n + (s.tools?.length ?? 0), 0);
  document.getElementById("provenance").innerHTML =
    "crawled <strong>" + ago + "</strong> · <strong>" + servers.length + "</strong> servers · <strong>" +
    dids.length + "</strong> publishers · <strong>" + tools + "</strong> tools";

  // ---- legend: publisher colors
  document.getElementById("legend").innerHTML = dids.map((did) => {
    const s = servers.find((v) => v.did === did);
    return '<span><span class="swatch" style="background:var(' + slot(did) + ')"></span>' +
      '<a href="https://pdsls.dev/at://' + esc(did) + '/tech.waow.mcp.server">@' + esc(s.handle || did) + "</a></span>";
  }).join("");

  // ---- table
  const rows = servers.map((s, i) => {
    const st = statusOf(s);
    const repo = safeUrl(s.repo), url = safeUrl(s.url);
    const toolChips = (s.tools ?? []).map((t) =>
      '<span class="chip"' + (t.description ? ' title="' + esc(t.description) + '"' : "") + ">" + esc(t.name) + "</span>"
    ).join("");
    const envChips = (s.environment ?? []).map((v) =>
      '<span class="chip"' + (v.description ? ' title="' + esc(v.description) + '"' : "") + ">" +
      esc(v.name) + (v.required ? '<span class="req">*</span>' : "") + "</span>"
    ).join("");
    const pkgs = (s.packages ?? []).map((p) => '<span class="chip">' + esc(p.registry) + ":" + esc(p.identifier) + "</span>").join("");
    const links = [
      repo && '<a href="' + esc(repo) + '">repo</a>',
      url && '<a href="' + esc(url) + '">endpoint</a>',
      s.uri && '<a href="https://pdsls.dev/' + esc(s.uri) + '">record</a>',
    ].filter(Boolean).join("");
    return '<tr class="row" id="s-' + i + '" data-i="' + i + '">' +
      '<td><span class="dot" style="background:var(' + slot(s.did) + ')"></span><span class="name">' + esc(s.name) + "</span></td>" +
      '<td class="pub hide-sm">@' + esc(s.handle || s.did) + "</td>" +
      '<td class="hide-sm">' + (s.transport ? '<span class="badge">' + esc(s.transport) + "</span>" : "") + "</td>" +
      '<td class="num">' + (s.tools?.length ?? 0) + "</td>" +
      '<td class="st ' + st.cls + '">' + st.label + "</td></tr>" +
      '<tr class="detail" id="d-' + i + '"><td colspan="5">' +
      '<div class="desc">' + esc(s.description) + "</div>" +
      (toolChips ? '<div class="kv"><span class="k">tools</span>' + toolChips + "</div>" : "") +
      (envChips ? '<div class="kv"><span class="k">env</span>' + envChips +
        ((s.environment ?? []).some((v) => v.required) ? ' <span class="req">* required</span>' : "") + "</div>" : "") +
      (pkgs ? '<div class="kv"><span class="k">install</span>' + pkgs + "</div>" : "") +
      (links ? '<div class="kv links"><span class="k">links</span>' + links + "</div>" : "") +
      "</td></tr>";
  }).join("");
  content.innerHTML = "<table><thead><tr>" +
    "<th>server</th><th class='hide-sm'>publisher</th><th class='hide-sm'>transport</th><th class='num'>tools</th><th>status</th>" +
    "</tr></thead><tbody>" + rows + "</tbody></table>";
  content.querySelectorAll("tr.row").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;
      document.getElementById("d-" + tr.dataset.i).classList.toggle("open");
    });
  });

  // ---- constellation canvas
  const canvas = document.getElementById("atlas");
  const tip = document.getElementById("atlas-tip");
  const mapped = servers.map((s, i) => ({ s, i })).filter((m) => typeof m.s.x === "number" && typeof m.s.y === "number");
  let pts = [];
  function layout() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || 340;
    canvas.width = w * dpr; canvas.height = h * dpr;
    const padX = Math.max(40, w * 0.08), padT = 90, padB = 46;
    pts = mapped.map((m) => ({
      ...m,
      px: padX + m.s.x * (w - 2 * padX),
      py: padT + m.s.y * (h - padT - padB),
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
    draw(dpr);
  }
  let hovered = null;
  function draw(dpr) {
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    for (const p of pts) {
      const c = cssVar(slot(p.s.did));
      const hot = hovered === p;
      ctx.save();
      ctx.shadowColor = c; ctx.shadowBlur = hot ? 22 : 12;
      ctx.beginPath();
      ctx.arc(p.px, p.py, hot ? 7.5 : 6, 0, Math.PI * 2);
      if (!p.s.url) {
        ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.stroke();
      } else if (p.s.alive) {
        ctx.fillStyle = c; ctx.fill();
      } else {
        ctx.strokeStyle = cssVar("--red"); ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.restore();
    }
  }
  function hit(e) {
    const r = canvas.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    return pts.find((p) => Math.hypot(p.px - x, p.py - y) < 16) ?? null;
  }
  canvas.addEventListener("pointermove", (e) => {
    const p = hit(e);
    if (p !== hovered) { hovered = p; draw(window.devicePixelRatio || 1); }
    canvas.style.cursor = p ? "pointer" : "default";
    if (p) {
      const st = statusOf(p.s);
      tip.innerHTML = '<span class="n">' + esc(p.s.name) + '</span> <span class="h">@' +
        esc(p.s.handle || p.s.did) + "</span><br><span class='st " + st.cls + "'>" + st.label + "</span>";
      tip.style.left = e.clientX + 14 + "px";
      tip.style.top = e.clientY - 10 + "px";
      tip.style.display = "block";
    } else tip.style.display = "none";
  });
  canvas.addEventListener("pointerleave", () => { tip.style.display = "none"; hovered = null; draw(window.devicePixelRatio || 1); });
  canvas.addEventListener("click", (e) => {
    const p = hit(e);
    if (!p) return;
    const row = document.getElementById("s-" + p.i);
    document.getElementById("d-" + p.i).classList.add("open");
    row.scrollIntoView({ behavior: "smooth", block: "center" });
  });
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
