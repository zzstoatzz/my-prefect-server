import { defineConfig, triggers, bindings } from "cf/config";
import * as entrypoint from "./worker.js" with { type: "cf-worker" };

export default defineConfig({
  worker: {
    name: "prefect-builder-reaper",
    compatibilityDate: "2026-10-27",
    entrypoint,
    env: { BILLING: bindings.r2({ name: "heavypad-prefect-backups" }) },
    triggers: [triggers.scheduled({ schedule: "*/5 * * * *" })],
    observability: { enabled: true, traces: { enabled: true } },
  },
});
