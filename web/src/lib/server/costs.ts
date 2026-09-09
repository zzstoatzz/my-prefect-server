// loads the latest io.zzstoatzz.cost.snapshot record from the public PDS.
// no auth — the same com.atproto.repo.listRecords path hub already uses for
// tangled.org data. the costs flow (my-prefect-server) writes these daily.

import type { InfraCostSnapshot } from "$lib/types";
import { z } from "zod";
import { projectInventory } from "./projects";

const PDS_HOST = process.env.PDS_HOST ?? "https://pds.zzstoatzz.io";
const COST_REPO = process.env.COST_REPO ?? "zzstoatzz.io";
const COLLECTION = "io.zzstoatzz.cost.snapshot";

const costSnapshotSchema: z.ZodType<InfraCostSnapshot> = z.object({
  generatedAt: z.iso.datetime({ offset: true }),
  periodStart: z.string().optional(),
  periodEnd: z.string().optional(),
  currency: z.literal("USD"),
  total: z.number().int().nonnegative(),
  byProject: z.array(
    z.object({
      key: z.string(),
      amount: z.number().int().nonnegative(),
      estimated: z.boolean(),
    }),
  ),
  byProvider: z.array(
    z.object({
      key: z.string(),
      amount: z.number().int().nonnegative(),
      estimated: z.boolean(),
    }),
  ),
  lineItems: z.array(
    z.object({
      provider: z.string(),
      project: z.string(),
      service: z.string(),
      amount: z.number().int().nonnegative(),
      estimated: z.boolean(),
      usage: z.string().optional(),
      note: z.string().optional(),
    }),
  ),
});
const costRecordsSchema = z.object({
  records: z.array(z.object({ value: costSnapshotSchema })),
});

export function attributeInfraCosts(
  snapshot: InfraCostSnapshot,
): InfraCostSnapshot {
  const lineItems = snapshot.lineItems.map((item) => {
    const key = `${item.provider}:${item.service}`;
    const owners = projectInventory.filter((p) =>
      p.costKeys.some(
        (resource) => key === resource || key.startsWith(resource + ":"),
      ),
    );
    return {
      ...item,
      project: owners.length === 1 ? owners[0].name : "unattributed",
    };
  });
  const names = [...new Set(lineItems.map((item) => item.project))];
  const byProject = names
    .map((key) => {
      const items = lineItems.filter((item) => item.project === key);
      return {
        key,
        amount: items.reduce((sum, item) => sum + item.amount, 0),
        estimated: items.some((item) => item.estimated),
        repo: projectInventory.find((p) => p.name === key)?.repo ?? null,
      };
    })
    .sort((a, b) => b.amount - a.amount);
  return {
    ...snapshot,
    lineItems,
    byProject,
    total: lineItems.reduce((sum, item) => sum + item.amount, 0),
  };
}

let cached: { at: number; data: InfraCostSnapshot | null } | null = null;
const TTL_MS = 5 * 60 * 1000;

export async function loadInfraCosts(): Promise<InfraCostSnapshot | null> {
  if (cached && Date.now() - cached.at < TTL_MS) return cached.data;

  const url =
    `${PDS_HOST}/xrpc/com.atproto.repo.listRecords` +
    `?repo=${encodeURIComponent(COST_REPO)}&collection=${COLLECTION}&limit=1`;

  try {
    // rkey is YYYY-MM-DD; listRecords returns newest rkey first, so limit=1
    // is the most recent snapshot.
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`PDS ${resp.status}`);
    const body = costRecordsSchema.parse(await resp.json());
    const snapshot = body.records[0]?.value;
    const data = snapshot ? attributeInfraCosts(snapshot) : null;
    cached = { at: Date.now(), data };
    return data;
  } catch (err) {
    console.warn("failed to load infra costs:", err);
    // serve stale on error rather than blanking the panel
    return cached?.data ?? null;
  }
}
