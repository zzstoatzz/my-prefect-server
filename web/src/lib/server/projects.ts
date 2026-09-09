import { z } from "zod";
import inventory from "../../../../packages/mps/src/mps/projects.json";

export const projectInventory = inventory;

const fleetRowSchema = z.object({
  project: z.string(),
  name: z.string(),
  url: z.string().nullable(),
  healthy: z.boolean(),
  status: z.number().int().min(0).max(599),
  ms: z.number().finite().nonnegative(),
  detail: z.string(),
  kind: z.enum(["endpoint", "deep"]),
  checkedAt: z.iso.datetime({ offset: true }),
});
const fleetArtifactSchema = z
  .array(
    z.object({
      data: z
        .string()
        .transform((value, context) => {
          try {
            return JSON.parse(value);
          } catch {
            context.addIssue({
              code: "custom",
              message: "Invalid fleet artifact JSON",
            });
            return z.NEVER;
          }
        })
        .pipe(z.array(fleetRowSchema).min(1)),
    }),
  )
  .min(1);

export async function loadFleetStatus() {
  const auth = process.env.FLEET_PREFECT_API_AUTH_STRING;
  if (!auth) throw new Error("Fleet report access is not configured");
  const base =
    process.env.FLEET_PREFECT_API_URL ?? "https://prefect-server.waow.tech/api";
  const response = await fetch(`${base}/artifacts/filter`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Basic ${Buffer.from(auth).toString("base64")}`,
    },
    body: JSON.stringify({
      artifacts: { key: { any_: ["fleet-health-status"] } },
      sort: "CREATED_DESC",
      limit: 1,
    }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok)
    throw new Error(`Fleet report source HTTP ${response.status}`);
  return parseFleetReport(await response.text(), Date.now());
}

export function parseFleetReport(serialized: string, now: number) {
  const artifacts = fleetArtifactSchema.parse(JSON.parse(serialized));
  const rows = artifacts[0].data;
  const checkedAt = rows[0].checkedAt;
  const age = now - Date.parse(checkedAt);
  if (
    age < -30_000 ||
    age > 30 * 60_000 ||
    rows.some((row) => row.checkedAt !== checkedAt)
  ) {
    throw new Error("Fleet report is stale or inconsistent");
  }
  const endpoints = rows.filter((row) => row.kind === "endpoint");
  const expected = inventory.flatMap((p) =>
    p.services.map((s) => ({ project: p.name, ...s })),
  );
  if (
    rows.filter((row) => row.kind === "deep").length !== 1 ||
    endpoints.some(
      (row) => row.healthy !== (row.status >= 200 && row.status < 300),
    ) ||
    endpoints.length !== expected.length ||
    new Set(endpoints.map((row) => row.url)).size !== expected.length ||
    expected.some(
      (s) =>
        !endpoints.some(
          (row) =>
            row.project === s.project &&
            row.name === s.name &&
            row.url === s.url,
        ),
    )
  ) {
    throw new Error("Fleet report does not match the deployed inventory");
  }
  return {
    checkedAt,
    results: endpoints.map((row) => ({
      project: row.project,
      name: row.name,
      url: row.url,
      status: row.status,
      ok: row.healthy,
      ms: row.ms,
    })),
    deepChecks: rows
      .filter((row) => row.kind === "deep")
      .map((row) => ({
        project: row.project,
        name: row.name,
        ok: row.healthy,
        detail: row.detail,
      })),
  };
}
