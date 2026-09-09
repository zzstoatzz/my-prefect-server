export const COSTS_URL = "/api/costs.json";
export const usd = (cents) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    cents / 100,
  );
export async function loadCosts(projects) {
  const response = await fetch(COSTS_URL, {
    signal: AbortSignal.timeout(15_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Costs HTTP ${response.status}`);
  const snapshot = await response.json();
  if (
    !snapshot ||
    !Array.isArray(snapshot.lineItems) ||
    !Number.isFinite(Date.parse(snapshot.generatedAt))
  )
    throw new Error("No valid cost snapshot");
  return {
    byProject: new Map(
      projects.map((p) => [
        p.name,
        snapshot.lineItems.filter((i) => i.project === p.name),
      ]),
    ),
    unresolved: snapshot.lineItems.filter((i) => i.project === "unattributed"),
    total: snapshot.total,
    generatedAt: snapshot.generatedAt,
    stale: Date.now() - Date.parse(snapshot.generatedAt) > 72 * 60 * 60 * 1000,
  };
}
