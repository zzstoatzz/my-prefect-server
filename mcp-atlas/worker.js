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
    root.innerHTML = atlas.servers.map((s) => {
      const repo = safeUrl(s.repo), url = safeUrl(s.url);
      const title = repo
        ? '<a href="' + esc(repo) + '">' + esc(s.name) + "</a>"
        : esc(s.name);
      const status = url
        ? (s.alive
            ? '<span class="live">● live</span>'
            : '<span class="unreachable">○ unreachable</span>')
        : "";
      const author = '<span class="by"><a href="https://bsky.app/profile/' +
        esc(s.did) + '">@' + esc(s.handle || s.did) + "</a></span>";
      const tools = s.tools?.length
        ? '<div class="tools">tools: ' + s.tools.map(esc).join(", ") + "</div>"
        : "";
      const links = [
        url && '<a href="' + esc(url) + '">endpoint</a>',
        s.uri && '<a href="https://pdsls.dev/' + esc(s.uri) + '">record</a>',
      ].filter(Boolean).join("");
      return '<div class="server">' + author + "<h2>" + title + "</h2>" + status +
        '<p class="desc">' + esc(s.description) + "</p>" + tools +
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
