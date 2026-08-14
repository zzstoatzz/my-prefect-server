// mcp.waow.tech — a directory of MCP servers self-published as
// tech.waow.mcp.server records on their authors' PDSes.
//
// GET  /               directory page
// GET  /api/atlas.json current crawl output (built by the mcp-atlas prefect flow)
// POST /api/atlas.json ingest, bearer-authed with the INGEST_TOKEN wrangler secret

const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mcp atlas</title>
<style>
  :root {
    --bg: #faf9f6; --fg: #1a1a1a; --muted: #6b6b6b; --line: #e2e0da;
    --card: #ffffff; --accent: #2f6f4f; --dead: #a05a5a;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #131313; --fg: #e8e6e1; --muted: #8f8d88; --line: #2a2a2a;
      --card: #1b1b1b; --accent: #7fbf9f; --dead: #c98787;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 16px/1.55 ui-monospace, "SF Mono", Menlo, monospace;
  }
  main { max-width: 44rem; margin: 0 auto; padding: 3rem 1.25rem 4rem; }
  h1 { font-size: 1.4rem; margin: 0 0 .25rem; font-weight: 600; }
  .sub { color: var(--muted); margin: 0 0 2rem; font-size: .85rem; }
  .sub a { color: inherit; }
  .stats { display: flex; gap: 2rem; flex-wrap: wrap; margin: 0 0 1.6rem; }
  .stat .n { font-size: 1.5rem; font-weight: 600; display: block; }
  .stat .l { color: var(--muted); font-size: .75rem; }
  .map {
    border: 1px solid var(--line); border-radius: 8px; background: var(--card);
    margin: 0 0 .5rem; overflow: hidden; position: relative;
  }
  .map svg { display: block; width: 100%; height: auto; }
  .map .dot { cursor: pointer; }
  .map .tip {
    position: absolute; pointer-events: none; display: none;
    background: var(--fg); color: var(--bg); border-radius: 6px;
    padding: .35rem .55rem; font-size: .75rem; white-space: nowrap;
    transform: translate(-50%, -130%); z-index: 2;
  }
  .map-caption {
    color: var(--muted); font-size: .72rem; margin: 0 0 .4rem;
  }
  .map-legend {
    display: flex; gap: 1.2rem; flex-wrap: wrap; align-items: center;
    color: var(--muted); font-size: .75rem; margin: 0 0 1.6rem;
  }
  .map-legend .swatch {
    display: inline-block; width: 9px; height: 9px; border-radius: 50%;
    margin-right: .35rem; vertical-align: -1px;
  }
  :root { --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; }
  @media (prefers-color-scheme: dark) {
    :root { --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; }
  }
  .server {
    border: 1px solid var(--line); border-radius: 8px; background: var(--card);
    padding: 1rem 1.1rem; margin-bottom: .9rem;
  }
  .server h2 { font-size: 1rem; margin: 0; display: inline; font-weight: 600; }
  .server h2 a { color: var(--fg); text-decoration: none; }
  .server h2 a:hover { text-decoration: underline; }
  .live { color: var(--accent); font-size: .75rem; margin-left: .5rem; }
  .unreachable { color: var(--dead); font-size: .75rem; margin-left: .5rem; }
  .by { color: var(--muted); font-size: .8rem; float: right; }
  .by a { color: inherit; text-decoration: none; }
  .by a:hover { text-decoration: underline; }
  .desc { margin: .5rem 0 .4rem; font-size: .875rem; }
  .tools { color: var(--muted); font-size: .78rem; word-break: break-word; }
  .tools span[title] { text-decoration: underline dotted; cursor: help; }
  .req { color: var(--dead); }
  .links { font-size: .78rem; margin-top: .45rem; }
  .links a { color: var(--accent); margin-right: 1rem; }
  footer { color: var(--muted); font-size: .75rem; margin-top: 2.5rem; }
  footer a { color: inherit; }
  .empty { color: var(--muted); }
</style>
</head>
<body>
<main>
  <h1>mcp atlas</h1>
  <p class="sub">MCP servers, self-published to the atmosphere. each entry is a
  <code>tech.waow.mcp.server</code> record on its author's own PDS — this page is
  just one view over them. <a href="/api/atlas.json">atlas.json</a></p>
  <div id="stats" class="stats"></div>
  <div id="map" class="map" hidden></div>
  <p id="map-caption" class="map-caption" hidden>each dot is a server; closer dots have more similar descriptions and tools. filled = hosted, hollow = local-only, red ring = endpoint down. hover for names.</p>
  <div id="map-legend" class="map-legend" hidden></div>
  <div id="servers"><p class="empty">loading…</p></div>
  <footer>
    crawled from the network via <a href="https://relay.waow.tech">relay</a> ·
    publish yours with a record in <code>tech.waow.mcp.server</code>
    <span id="asof"></span>
  </footer>
</main>
<script>
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const safeUrl = (u) => /^https?:\\/\\//.test(String(u ?? "")) ? u : null;

  fetch("/api/atlas.json").then((r) => r.ok ? r.json() : null).then((atlas) => {
    const root = document.getElementById("servers");
    if (!atlas || !atlas.servers?.length) {
      root.innerHTML = '<p class="empty">no servers crawled yet.</p>';
      return;
    }
    const publishers = new Set(atlas.servers.map((s) => s.did)).size;
    const remotes = atlas.servers.filter((s) => s.url);
    const live = remotes.filter((s) => s.alive).length;
    const tools = atlas.servers.reduce((n, s) => n + (s.tools?.length ?? 0), 0);
    document.getElementById("stats").innerHTML = [
      [atlas.servers.length, "servers"],
      [publishers, "publishers"],
      [remotes.length ? live + "/" + remotes.length : "—", "remotes live"],
      [tools, "tools"],
    ].map(([n, l]) =>
      '<div class="stat"><span class="n">' + esc(n) + '</span><span class="l">' + l + "</span></div>"
    ).join("");
    const mapped = atlas.servers.filter((s) => typeof s.x === "number" && typeof s.y === "number");
    if (mapped.length > 1) {
      const W = 720, H = 400, pad = 34;
      // a few relaxation passes so overlapping dots separate without
      // meaningfully moving off their semantic position
      const pts = mapped.map((s) => ({ s, x: pad + s.x * (W - 2 * pad), y: pad + s.y * (H - 2 * pad) }));
      const MIN = 26;
      for (let pass = 0; pass < 12; pass++) {
        for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
          const a = pts[i], b = pts[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const d = Math.hypot(dx, dy) || 0.01;
          if (d < MIN) {
            const push = (MIN - d) / 2, ux = dx / d, uy = dy / d;
            a.x -= ux * push; a.y -= uy * push;
            b.x += ux * push; b.y += uy * push;
          }
        }
      }
      const pos = new Map(pts.map((p) => [p.s, p]));
      const px = (s) => pos.get(s).x;
      const py = (s) => pos.get(s).y;

      // color = publisher identity, slots assigned in fixed (sorted-DID) order
      const dids = [...new Set(atlas.servers.map((s) => s.did))].sort();
      const slot = (did) => "var(--s" + (Math.min(dids.indexOf(did), 3) + 1) + ")";

      const dot = (s) => {
        const c = slot(s.did), x = px(s), y = py(s);
        // fill = hosted; hollow = local-only; red ring flags a down endpoint
        const body = !s.url
          ? '<circle cx="' + x + '" cy="' + y + '" r="5.5" fill="var(--card)" stroke="' + c + '" stroke-width="2"/>'
          : s.alive
            ? '<circle cx="' + x + '" cy="' + y + '" r="6" fill="' + c + '" stroke="var(--card)" stroke-width="2"/>'
            : '<circle cx="' + x + '" cy="' + y + '" r="5.5" fill="var(--card)" stroke="var(--dead)" stroke-width="2"/>';
        return '<g class="dot" data-i="' + atlas.servers.indexOf(s) + '" data-name="' + esc(s.name) +
          '" data-handle="' + esc(s.handle || s.did) + '">' + body +
          '<circle cx="' + x + '" cy="' + y + '" r="14" fill="transparent"/></g>';
      };

      const mapEl = document.getElementById("map");
      mapEl.hidden = false;
      mapEl.innerHTML =
        '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="semantic map of MCP servers">' +
        mapped.map(dot).join("") + '</svg><div class="tip" id="tip"></div>';
      document.getElementById("map-caption").hidden = false;

      const legend = document.getElementById("map-legend");
      legend.hidden = false;
      legend.innerHTML = dids.map((did) => {
        const s = atlas.servers.find((v) => v.did === did);
        return '<span><span class="swatch" style="background:' + slot(did) + '"></span>@' +
          esc(s.handle || did) + "</span>";
      }).join("");

      const tip = document.getElementById("tip");
      const svg = mapEl.querySelector("svg");
      mapEl.querySelectorAll(".dot").forEach((g) => {
        g.addEventListener("click", () => {
          document.getElementById("s-" + g.dataset.i)?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        g.addEventListener("pointerenter", () => {
          const c = g.querySelector("circle");
          const r = mapEl.getBoundingClientRect();
          const scale = r.width / W;
          tip.textContent = g.dataset.name + " · @" + g.dataset.handle;
          tip.style.left = c.getAttribute("cx") * scale + "px";
          tip.style.top = c.getAttribute("cy") * (svg.getBoundingClientRect().height / H) + "px";
          tip.style.display = "block";
        });
        g.addEventListener("pointerleave", () => { tip.style.display = "none"; });
      });
    }
    root.innerHTML = atlas.servers.map((s, i) => {
      const repo = safeUrl(s.repo), url = safeUrl(s.url);
      const title = repo
        ? '<a href="' + esc(repo) + '">' + esc(s.name) + "</a>"
        : esc(s.name);
      const status = url
        ? (s.alive
            ? '<span class="live">● live' + (s.authRequired ? " (auth)" : "") + "</span>"
            : '<span class="unreachable">○ unreachable</span>')
        : "";
      const author = '<span class="by"><a href="https://bsky.app/profile/' +
        esc(s.did) + '">@' + esc(s.handle || s.did) + "</a></span>";
      const tools = s.tools?.length
        ? '<div class="tools">tools: ' + s.tools.map((t) =>
            t.description
              ? '<span title="' + esc(t.description) + '">' + esc(t.name) + "</span>"
              : esc(t.name)
          ).join(", ") + "</div>"
        : "";
      const env = s.environment?.length
        ? '<div class="tools">env: ' + s.environment.map((v) => {
            const label = esc(v.name) + (v.required ? '<span class="req">*</span>' : "");
            return v.description
              ? '<span title="' + esc(v.description) + '">' + label + "</span>"
              : label;
          }).join(", ") + (s.environment.some((v) => v.required) ? ' <span class="req">(* required)</span>' : "") + "</div>"
        : "";
      const pkgs = s.packages?.length
        ? '<div class="tools">install: ' + s.packages.map((p) =>
            esc(p.registry) + ":" + esc(p.identifier)
          ).join(", ") + "</div>"
        : "";
      const links = [
        url && '<a href="' + esc(url) + '">endpoint</a>',
        s.uri && '<a href="https://pdsls.dev/' + esc(s.uri) + '">record</a>',
      ].filter(Boolean).join("");
      return '<div class="server" id="s-' + i + '">' + author + "<h2>" + title + "</h2>" + status +
        '<p class="desc">' + esc(s.description) + "</p>" + tools + env + pkgs +
        (links ? '<div class="links">' + links + "</div>" : "") + "</div>";
    }).join("");
    if (atlas.generatedAt) {
      document.getElementById("asof").textContent =
        " · as of " + new Date(atlas.generatedAt).toISOString().slice(0, 16) + "Z";
    }
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
