import { describe, expect, test } from "bun:test";
import { EMPTY_STATE, MAX_BACKUP_AGE_MS, REMIND_EVERY_MS, decide, newestKey, problems, stampTime } from "./watchdog";

const NOW = Date.parse("2026-11-08T12:00:00Z");
const FRESH = "postgres/20261108T054931Z.dump.age";
const OK = { name: "login.zzstoatzz.io", ok: true, detail: "HTTP 200" };

test("stamp parses the backup name", () => {
  expect(stampTime(FRESH)).toBe(Date.parse("2026-11-08T05:49:31Z"));
  expect(stampTime("watchdog/state.json")).toBeNull();
});

test("newest key ignores other suffixes and unstamped keys", () => {
  const keys = ["postgres/x.dump.age", "postgres/20261107T054931Z.dump.age", FRESH, "postgres/20261108T060000Z.other"];
  expect(newestKey(keys, ".dump.age")).toBe(FRESH);
  expect(newestKey([], ".dump.age")).toBeNull();
});

describe("problems", () => {
  test("fresh backups and healthy probes are quiet", () => {
    expect(problems(NOW, [OK], [{ name: "postgres dump", newest: FRESH }])).toEqual([]);
  });
  test("stale, missing and failing are reported", () => {
    const stale = new Date(NOW - MAX_BACKUP_AGE_MS - 3600_000).toISOString().replace(/[-:]|\.\d+/g, "");
    const found = problems(
      NOW,
      [{ name: "hub.waow.tech", ok: false, detail: "HTTP 530" }],
      [
        { name: "postgres dump", newest: `postgres/${stale}.dump.age` },
        { name: "host snapshot", newest: null },
      ],
    );
    expect(found).toEqual([
      "hub.waow.tech: HTTP 530",
      `postgres dump: newest postgres/${stale}.dump.age is 27h old`,
      "host snapshot: none in R2",
    ]);
  });
});

describe("decide", () => {
  const failing = ["hub.waow.tech: HTTP 530"];
  test("first failure alerts and records since", () => {
    const d = decide(NOW, EMPTY_STATE, failing);
    expect(d.message).toStartWith("heavypad watchdog: DOWN");
    expect(d.state.since).toBe(new Date(NOW).toISOString());
  });
  test("unchanged failure within a day is silent", () => {
    const first = decide(NOW, EMPTY_STATE, failing).state;
    expect(decide(NOW + 15 * 60_000, first, failing).message).toBeNull();
  });
  test("unchanged failure re-alerts daily", () => {
    const first = decide(NOW, EMPTY_STATE, failing).state;
    const d = decide(NOW + REMIND_EVERY_MS, first, failing);
    expect(d.message).toStartWith("heavypad watchdog: still failing since");
    expect(d.state.lastAlertAt).toBe(new Date(NOW + REMIND_EVERY_MS).toISOString());
  });
  test("a changed set alerts immediately", () => {
    const first = decide(NOW, EMPTY_STATE, failing).state;
    const d = decide(NOW + 60_000, first, [...failing, "host snapshot: none in R2"]);
    expect(d.message).toContain("host snapshot");
  });
  test("recovery alerts once then stays quiet", () => {
    const first = decide(NOW, EMPTY_STATE, failing).state;
    const back = decide(NOW + 60_000, first, []);
    expect(back.message).toContain("recovered");
    expect(back.state).toEqual(EMPTY_STATE);
    expect(decide(NOW + 120_000, back.state, []).message).toBeNull();
  });
});
