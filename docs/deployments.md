# deployments

generated from `prefect.yaml` by `scripts/deployments_inventory.py`; do not edit by hand. `just inventory` regenerates it and CI fails on drift.

40 deployments, all on the `home-pool` process worker. a cadence of `after x` is an automation that fires when deployment x completes; `manual` means the deployment is started by the API, an automation outside `prefect.yaml`, or a person.

## pipeline

the hub pipeline: ingest, classify, transform, brief

| deployment | cadence | purpose | entrypoint |
|---|---|---|---|
| `ingest` | `0 * * * *` | Fetch GitHub, tangled.org, and phi memory concurrently, then persist sequentially | [`ingest`](flows/ingest.py) |
| `classify-emails` | after `ingest` | LLM-classify unclassified inbox emails (personal / work / notification / promotional) so scoring can down-weight promotional noise | [`classify_emails`](flows/classify_emails.py) |
| `transform` | after `classify-emails` | Run the dbt project over analytics.duckdb and export the hub-only tables | [`transform`](flows/transform.py) |
| `brief` | after `transform` | Write the hub briefing: an LLM reads the scored action items and produces briefing.json | [`brief`](flows/brief.py) |
| `phi-memory-synthesis` | after `transform` | Synthesize per-user relationship summaries from phi's memory | [`compact`](flows/compact.py) |

## phi

phi's identity and memory

| deployment | cadence | purpose | entrypoint |
|---|---|---|---|
| `phi-tag-maintenance` | `0 13 * * *` | Morning flow: tag maintenance | [`morning`](flows/morning.py) |
| `curate` | after `phi-tag-maintenance` | Phi reviews and curates its own semble records | [`curate`](flows/curate.py) |
| `phi-atlas` | `0 13 * * *` | Daily map of phi's mental landscape | [`phi_atlas`](flows/phi_atlas.py) |
| `docket` | after `phi-atlas` | Establish today's promotion object | [`docket`](flows/docket.py) |
| `phi-curation` | `0 3 * * 1` | phi's weekly publication-curation pass | [`phi_trigger`](flows/phi_trigger.py) |
| `phi-likes-review` | `0 20 * * 0` | phi reads back her own likes for follow-ups and semble cards | [`phi_trigger`](flows/phi_trigger.py) |
| `phi-pull-review` | manual | phi reviews a gardener pull and comments a VERDICT line | [`phi_trigger`](flows/phi_trigger.py) |
| `phi-editorial` | `0 15 * * *` | phi refreshes coral's editorial context from trending entities | [`phi_trigger`](flows/phi_trigger.py) |
| `phi-character-retro` | `0 17 1 * *` | phi rereads her own writing and rewrites her [SELF] record | [`phi_trigger`](flows/phi_trigger.py) |
| `phi-chicken-scout` | `0 18 * * *` | mid-round chicken market scout | [`phi_trigger`](flows/phi_trigger.py) |
| `phi-chicken-precheck` | `0 4 * * *` | pre-lock chicken check, final books and last call | [`phi_trigger`](flows/phi_trigger.py) |

## publish

snapshots and indexes published for other products

| deployment | cadence | purpose | entrypoint |
|---|---|---|---|
| `leaflet-atlas` | `0 */6 * * *` | Rebuild the 2D semantic map and deploy to Cloudflare Pages | [`rebuild_atlas`](flows/atlas.py) |
| `pds-records` | manual | General-purpose PDS record management | [`pds_records`](flows/pds_records.py) |
| `typeahead-identity-hourly` | `20 * * * *` | typeahead-identity-hourly: give newly discovered actors their handles | [`typeahead_identity_hourly`](flows/typeahead_identity.py) |
| `typeahead-enrich-backfill` | `0 20 * * *` | Enrich actors whose profile has never been checked, paced against the appview | [`typeahead_enrich_backfill`](flows/typeahead_enrich_backfill.py) |
| `typeahead-index` | `0 9 */3 * *` | Build the typeahead prefix-index snapshot (the offline `MODE=indexer` job) on the home box and publish it to R2 | [`typeahead_index`](flows/typeahead_index.py) |
| `typeahead-plc-identity` | `0 5 * * 1` | Resolve typeahead's DID -> (handle, pds) backlog in bulk from the PLC log | [`typeahead_plc_identity`](flows/typeahead_plc_identity.py) |
| `pub-search-snapshot` | `40 */2 * * *` | Build the pub-search replica snapshot (the `BUILDER_MODE=1` job) on the home box and publish it to R2 | [`pub_search_snapshot`](flows/pub_search_snapshot.py) |
| `bisk-snapshot` | `*/10 * * * *` | compute the authoritative bisk.social snapshot and publish it to R2 | [`bisk_snapshot`](flows/bisk.py) |

## gardener

pi as a coding agent: diagnose, propose, revise, merge

| deployment | cadence | purpose | entrypoint |
|---|---|---|---|
| `pi-agent` | manual | run `pi -p <prompt>` in the workspace and return its final output | [`pi_agent`](flows/pi_agent.py) |
| `autofix` | manual | pi diagnoses a failed run and, when proposing is on for that deployment, opens a gardener pull | [`autofix`](flows/autofix.py) |
| `watch-tangled-pulls` | `*/2 * * * *` | turn the operator's comments on gardener's pulls into autofix-revise runs | [`watch_tangled_pulls`](flows/watch_tangled_pulls.py) |
| `autofix-revise` | manual | revise a gardener-authored pull in response to an operator comment | [`autofix_revise`](flows/autofix_revise.py) |
| `pi-pr` | manual | have pi attempt `task` in `repo` and open a tangled PR as gardener | [`pi_pr`](flows/pi_pr.py) |
| `merge-approved` | manual | a phi-approved gardener pull lands when the operator resumes this run | [`merge_approved`](flows/merge_approved.py) |
| `dep-bump` | manual | re-pin `dep` to `version` in each downstream; land the ones whose tests pass | [`dep_bump`](flows/dep_bump.py) |
| `stream-admission` | manual | Gate a stream commit on heavypad | [`stream_admission`](flows/stream_admission.py) |

## watch

health, traffic, and cost reporting

| deployment | cadence | purpose | entrypoint |
|---|---|---|---|
| `fleet-health` | `3,18,33,48 * * * *` | fleet health — one deep check for stream, shallow checks for everything else | [`fleet_health`](flows/fleet_health.py) |
| `mcp-fleet-health` | `12 * * * *` | mcp fleet health — connect, discover, and route across the public MCP servers | [`mcp_fleet_health`](flows/mcp_fleet_health.py) |
| `diagnostics` | `37 * * * *` | diagnostic flow — liveness canary plus host resource telemetry | [`diagnostics`](flows/diagnostics.py) |
| `costs` | `0 8 * * *` | Collect infra costs from all connectors and snapshot them to PDS | [`costs`](flows/costs.py) |
| `bufo-traffic` | `7 * * * *` | Roll the trailing window of find-bufo.com requests into per-day PDS records | [`bufo_traffic`](flows/bufo_traffic.py) |
| `watch-fastmcp` | `*/5 * * * *` | Turn fastmcp activity into events on the hub's bus | [`watch_fastmcp`](flows/watch_fastmcp.py) |
| `fastmcp-brief` | `0 */4 * * *` | Compose what happened in fastmcp into something worth reading | [`fastmcp_brief`](flows/fastmcp_brief.py) |
| `strata-hourly` | `21 * * * *` | Summarise every sealed segment the worker lacks or holds at a stale checksum; returns segments ingested | [`ingest_segment_collections`](flows/strata.py) |
