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
  const strike = (state = EMPTY_STATE, at = NOW, f = failing) => decide(at, state, f);
  test("one failing run is silent, the second alerts", () => {
    const first = strike();
    expect(first.message).toBeNull();
    const second = strike(first.state, NOW + 15 * 60_000);
    expect(second.message).toStartWith("heavypad watchdog: DOWN");
    expect(second.state.since).toBe(new Date(NOW).toISOString());
  });
  test("a blip that recovers never alerts", () => {
    expect(strike(strike().state, NOW + 15 * 60_000, []).message).toBeNull();
  });
  test("changing detail text is not a change; a new subject is", () => {
    const down = strike(strike().state, NOW + 15 * 60_000).state;
    const aged = strike(down, NOW + 30 * 60_000, ["hub.waow.tech: connection timed out"]);
    expect(aged.message).toBeNull();
    expect(strike(aged.state, NOW + 45 * 60_000, [...failing, "host snapshot: none in R2"]).message).toContain("host snapshot");
  });
  test("unchanged failure re-alerts daily", () => {
    const down = strike(strike().state, NOW + 15 * 60_000).state;
    expect(strike(down, NOW + 15 * 60_000 + REMIND_EVERY_MS).message).toStartWith("heavypad watchdog: still failing since");
  });
  test("recovery alerts once after a DOWN, then stays quiet", () => {
    const down = strike(strike().state, NOW + 15 * 60_000).state;
    const back = strike(down, NOW + 30 * 60_000, []);
    expect(back.message).toContain("recovered");
    expect(strike(back.state, NOW + 45 * 60_000, []).message).toBeNull();
  });
});
