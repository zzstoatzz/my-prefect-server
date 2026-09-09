import { test } from "node:test";
import assert from "node:assert/strict";
import { projectInventory, parseFleetReport } from "../src/lib/server/projects";
import { attributeInfraCosts } from "../src/lib/server/costs";
import type { InfraCostSnapshot } from "../src/lib/types";
const now = Date.now();
function fleetFixture() {
  const rows = projectInventory.flatMap((p) =>
    p.services.map((s) => ({
      project: p.name,
      name: s.name,
      url: s.url,
      healthy: true,
      status: 200,
      ms: 10,
      detail: "HTTP 200",
      kind: "endpoint",
      checkedAt: new Date(now).toISOString(),
    })),
  );
  rows.push({
    project: "stream",
    name: "stream (deep)",
    url: "",
    healthy: true,
    status: 0,
    ms: 0,
    detail: "tail advancing",
    kind: "deep",
    checkedAt: new Date(now).toISOString(),
  });
  return rows;
}
const serialize = (rows: ReturnType<typeof fleetFixture>) =>
  JSON.stringify([{ data: JSON.stringify(rows) }]);
test("the complete fleet report and Stream deep check are readable", () => {
  const report = parseFleetReport(serialize(fleetFixture()), now);
  assert.equal(
    report.results.length,
    projectInventory.reduce((n, p) => n + p.services.length, 0),
  );
  assert.equal(report.deepChecks.length, 1);
});
test("stale, partial and contradictory fleet artifacts cannot appear healthy", () => {
  assert.throws(() =>
    parseFleetReport(serialize(fleetFixture()), now + 31 * 60_000),
  );
  const partial = fleetFixture();
  partial.pop();
  assert.throws(() => parseFleetReport(serialize(partial), now));
  const wrong = fleetFixture();
  wrong[0].healthy = false;
  assert.throws(() => parseFleetReport(serialize(wrong), now));
});
test("both cost views use the same ownership, preserving unassigned spend", () => {
  const snapshot: InfraCostSnapshot = {
    generatedAt: new Date(now).toISOString(),
    currency: "USD",
    total: 300,
    byProject: [],
    byProvider: [],
    lineItems: [
      {
        provider: "fly",
        service: "relay-api:compute",
        project: "wrong",
        amount: 100,
        estimated: true,
      },
      {
        provider: "fly",
        service: "relay-api-new:compute",
        project: "plyr.fm",
        amount: 200,
        estimated: true,
      },
    ],
  };
  const result = attributeInfraCosts(snapshot);
  assert.equal(result.total, 300);
  assert.deepEqual(
    result.lineItems.map((i) => i.project),
    ["plyr.fm", "unattributed"],
  );
  assert.equal(
    result.byProject.find((p) => p.key === "plyr.fm")?.repo,
    "https://tangled.org/zzstoatzz.io/plyr.fm",
  );
});
