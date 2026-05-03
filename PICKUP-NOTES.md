# pickup notes — prefect/server cleanup

handing this off; another agent should pick these up while skills work
continues on the bot side.

## 1. flows are cargo-culting `Secret.load(...).get()` instead of using job variables

every flow in this repo (`atlas.py`, `curate.py`, `ingest.py`,
`morning.py`, `compact.py`, `brief.py`, `pds_records.py`) loads secrets
explicitly inside the flow code — for example `cf_token =
Secret.load("cloudflare-api-token").get()`. that's the wrong pattern for
values that are really just environment configuration.

prefect's intended mechanism is **job variables** — you declare env vars
at the work pool or deployment level, and prefect resolves Secret block
references at runtime so the value lands in `os.environ` of the running
flow pod. flow code then doesn't touch secrets at all; it just reads
`os.environ["CLOUDFLARE_API_TOKEN"]` (or doesn't read it at all if the
subprocess that needs it inherits the env).

**read the prefect docs on job variables before refactoring.** in
particular how Secret block references can be templated into the work
pool's env config and resolve at run time.

scope:
- pick one flow as the canary (probably `atlas.py` — that's where the
  problem became visible) and convert it to job-variable-driven secrets.
- once the canary works in production, fan out to the other flows.
- the work pool's base job template (defaulted by
  `scripts/patch_work_pool.py`) is probably the right place for env vars
  that every flow needs (anthropic-api-key, turbopuffer-api-key, etc.);
  per-deployment job variables for one-off secrets.

## 2. `rebuild-atlas` flow is currently broken

`flows/atlas.py:deploy_to_pages` was calling `apt-get install nodejs npm`
to provision wrangler, which pinned to Debian's node v20 — wrangler now
requires node ≥v22, so the deploy step has been failing on every run
since at least 2026-04-30.

i tried to switch to bun (`curl -fsSL https://bun.sh/install | bash` +
`bunx wrangler ...`). last two run attempts failed with
`FileNotFoundError: [Errno 2] No such file or directory: 'bun'` — the bun
installer ran but the binary wasn't on PATH when subprocess tried to
invoke it. i set `BUN_INSTALL=/tmp/bun` and prepended
`/tmp/bun/bin` to PATH, but evidently the install isn't landing where i
expected, OR the env isn't actually being passed to the second
subprocess call.

**worth verifying directly**:
- exec into a worker pod and run the install script manually to see
  where bun actually goes
- consider whether the right answer is to install bun in the worker
  *image* once, not at flow-run time. that's a kubernetes-side change
  (modify the prefect base image or add an init container).
- alternative: stop using bun, install node v22 from nodesource.

last two failed runs: `rapid-roadrunner`, `devout-kagu` (5/3, both
FAILED on `deploy_to_pages` after `clone_repo` and `build_atlas` both
COMPLETED).

## 3. ancillary

- `prefect deploy --all` is supposed to run on every push to main via
  `.tangled/workflows/`, but several recent tag pushes (v0.9.3, v0.9.6
  on the bot side) didn't trigger their CI deploys either. tangled CI
  reliability is worth a separate look — same symptom on two different
  repos.
