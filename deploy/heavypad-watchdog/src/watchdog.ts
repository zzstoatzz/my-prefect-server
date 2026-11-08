export const MAX_BACKUP_AGE_MS = 26 * 60 * 60 * 1000;
export const REMIND_EVERY_MS = 24 * 60 * 60 * 1000;

export const PROBES = [
  { name: "login.zzstoatzz.io", url: "https://login.zzstoatzz.io/" },
  { name: "spindle.zzstoatzz.io", url: "https://spindle.zzstoatzz.io/xrpc/org.tangled.ci.queryPipelines?repo=did:plc:xrndz6e42qj6ygbclfmruugv&limit=1" },
  { name: "hub.waow.tech", url: "https://hub.waow.tech/" },
] as const;

export const BACKUPS = [
  { name: "postgres dump", prefix: "postgres/", suffix: ".dump.age" },
  { name: "host snapshot", prefix: "host/", suffix: ".host.tar.age" },
] as const;

export type ProbeResult = { name: string; ok: boolean; detail: string };
export type BackupResult = { name: string; newest: string | null };

export type State = {
  failing: string[];
  since: string | null;
  lastAlertAt: string | null;
};

export const EMPTY_STATE: State = { failing: [], since: null, lastAlertAt: null };

const STAMP = /(\d{8}T\d{6}Z)/;

export function stampTime(key: string): number | null {
  const match = STAMP.exec(key);
  if (match === null) return null;
  const s = match[1];
  return Date.parse(
    `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T${s.slice(9, 11)}:${s.slice(11, 13)}:${s.slice(13, 15)}Z`,
  );
}

export function newestKey(keys: readonly string[], suffix: string): string | null {
  const stamped = keys.filter((k) => k.endsWith(suffix) && stampTime(k) !== null).sort();
  return stamped.at(-1) ?? null;
}

export function problems(now: number, probes: readonly ProbeResult[], backups: readonly BackupResult[]): string[] {
  const found: string[] = [];
  for (const p of probes) if (!p.ok) found.push(`${p.name}: ${p.detail}`);
  for (const b of backups) {
    if (b.newest === null) {
      found.push(`${b.name}: none in R2`);
      continue;
    }
    const age = now - (stampTime(b.newest) ?? 0);
    if (age > MAX_BACKUP_AGE_MS) found.push(`${b.name}: newest ${b.newest} is ${Math.floor(age / 3600000)}h old`);
  }
  return found;
}

export type Decision = { state: State; message: string | null };

export function subject(problem: string): string {
  return problem.split(":")[0];
}

export function decide(now: number, previous: State, failing: string[]): Decision {
  const at = new Date(now).toISOString();
  if (failing.length === 0) {
    if (previous.lastAlertAt === null) return { state: EMPTY_STATE, message: null };
    return {
      state: EMPTY_STATE,
      message: `heavypad watchdog: recovered (was failing since ${previous.since})`,
    };
  }
  const since = previous.failing.length === 0 ? at : previous.since;
  const firstStrike = previous.failing.length === 0;
  const changed = failing.map(subject).join("\n") !== previous.failing.map(subject).join("\n");
  const lastAlert = previous.lastAlertAt === null ? null : Date.parse(previous.lastAlertAt);
  const due = lastAlert === null || now - lastAlert >= REMIND_EVERY_MS;
  if (firstStrike || (!changed && !due)) {
    return { state: { failing, since, lastAlertAt: previous.lastAlertAt }, message: null };
  }
  const header = lastAlert === null ? "heavypad watchdog: DOWN" : `heavypad watchdog: still failing since ${since}`;
  return { state: { failing, since, lastAlertAt: at }, message: `${header}\n${failing.map((f) => `- ${f}`).join("\n")}` };
}
