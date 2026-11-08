import {
  BACKUPS,
  EMPTY_STATE,
  PROBES,
  decide,
  newestKey,
  problems,
  type BackupResult,
  type ProbeResult,
  type State,
} from "./watchdog";

type Env = { BACKUPS: R2Bucket; DISCORD_WEBHOOK_URL: string };

const STATE_KEY = "watchdog/state.json";

async function probe(name: string, url: string): Promise<ProbeResult> {
  try {
    const response = await fetch(url, { redirect: "manual", signal: AbortSignal.timeout(10_000) });
    const ok = response.status < 400;
    return { name, ok, detail: `HTTP ${response.status}` };
  } catch (error) {
    return { name, ok: false, detail: error instanceof Error ? error.message : String(error) };
  }
}

async function listKeys(bucket: R2Bucket, prefix: string): Promise<string[]> {
  const keys: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await bucket.list({ prefix, cursor });
    keys.push(...page.objects.map((o) => o.key));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor !== undefined);
  return keys;
}

function parseState(value: unknown): State {
  if (typeof value !== "object" || value === null) return EMPTY_STATE;
  const v = value as Record<string, unknown>;
  const failing = Array.isArray(v.failing) ? v.failing.filter((f): f is string => typeof f === "string") : [];
  return {
    failing,
    since: typeof v.since === "string" ? v.since : null,
    lastAlertAt: typeof v.lastAlertAt === "string" ? v.lastAlertAt : null,
  };
}

async function run(env: Env): Promise<{ failing: string[]; message: string | null }> {
  const now = Date.now();
  const probes = await Promise.all(PROBES.map((p) => probe(p.name, p.url)));
  const backups: BackupResult[] = await Promise.all(
    BACKUPS.map(async (b) => ({ name: b.name, newest: newestKey(await listKeys(env.BACKUPS, b.prefix), b.suffix) })),
  );
  const failing = problems(now, probes, backups);
  const stored = await env.BACKUPS.get(STATE_KEY);
  const previous = stored === null ? EMPTY_STATE : parseState(await stored.json());
  const { state, message } = decide(now, previous, failing);
  if (message !== null) {
    const response = await fetch(env.DISCORD_WEBHOOK_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: message }),
    });
    if (!response.ok) throw new Error(`discord webhook returned ${response.status}`);
  }
  await env.BACKUPS.put(STATE_KEY, JSON.stringify(state));
  return { failing, message };
}

export default {
  async scheduled(_controller: ScheduledController, env: Env) {
    await run(env);
  },
  async fetch(_request: Request, env: Env) {
    const stored = await env.BACKUPS.get(STATE_KEY);
    const state = stored === null ? EMPTY_STATE : parseState(await stored.json());
    return Response.json({ ok: state.failing.length === 0, ...state });
  },
} satisfies ExportedHandler<Env>;
