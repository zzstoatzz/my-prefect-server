import { defineConfig, triggers } from "cf/config";
import * as entrypoint from "./worker.js" with { type: "cf-worker" };

export default defineConfig({
  worker: {
    name: "prefect-builder-reaper",
    compatibilityDate: "2026-10-27",
    entrypoint,
    triggers: [triggers.scheduled({ schedule: "*/5 * * * *" })],
    observability: { enabled: true, traces: { enabled: true } },
  },
});
