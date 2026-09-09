import { PROXY_URL } from "./services.js";

export function validateStatus(projects, report, now = Date.now()) {
  const expected = projects.flatMap((p) =>
    p.services.map((s) => ({ ...s, project: p.name })),
  );
  const age = now - Date.parse(report.checkedAt);
  const results = report.results;
  if (
    !expected.length ||
    !Number.isFinite(age) ||
    age < -30_000 ||
    age > 30 * 60_000 ||
    !Array.isArray(results) ||
    results.length !== expected.length ||
    new Set(results.map((r) => r.url)).size !== expected.length ||
    expected.some(
      (e) =>
        !results.some(
          (r) =>
            r.url === e.url && r.name === e.name && r.project === e.project,
        ),
    ) ||
    results.some(
      (r) =>
        typeof r.ok !== "boolean" ||
        !Number.isInteger(r.status) ||
        r.status < 0 ||
        r.status > 599 ||
        !Number.isFinite(r.ms) ||
        r.ms < 0 ||
        r.ok !== (r.status >= 200 && r.status < 300),
    )
  ) {
    throw new Error("Monitor returned incomplete, invalid, or stale results");
  }
  return new Map(results.map((result) => [result.url, result]));
}

export async function checkAll(projects) {
  const response = await fetch(PROXY_URL + "/fleet.json", {
    cache: "no-store",
    signal: AbortSignal.timeout(45_000),
  });
  if (!response.ok) throw new Error(`Monitor HTTP ${response.status}`);
  const report = await response.json();
  return {
    results: validateStatus(projects, report),
    checkedAt: report.checkedAt,
    deepChecks: report.deepChecks,
  };
}
