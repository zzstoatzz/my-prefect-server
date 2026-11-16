set dotenv-load

# --- dev ---

# sync workspace (all members)
sync:
    uv sync

# build the local Zig Prefect worker guard
guard optimize="ReleaseSafe":
    zig build --build-file tools/prefect-worker-guard/build.zig -Doptimize={{ optimize }}

# build the HeavyPad Linux Zig Prefect worker guard from macOS
guard-linux optimize="ReleaseSafe":
    zig build --build-file tools/prefect-worker-guard/build.zig -Dtarget=x86_64-linux-gnu -Doptimize={{ optimize }}

# Run the CLI against the configured production server.
[positional-arguments]
prefect *args:
    uv run scripts/prefect_cli.py "$@"

# push main to both remotes; github first, since the tangled push triggers
# CI that installs the package from the github mirror
push:
    git push github main
    git push origin main

# --- analytics ---

# first-time dbt setup: install deps, seed reference data, compile models
init-analytics:
    cd analytics && uv run dbt deps && uv run dbt seed && uv run dbt compile

# Run the checks required before trusted-host registration.
check:
    just validate-deployments
    uv run ruff check .
    uv run ruff format --check .
    uv run --extra inbox ty check
    just test
    cd web && bun install --frozen-lockfile && bun run check && bun run lint && bun test
    cd deploy/heavypad-watchdog && bun install --frozen-lockfile && bun run check && bun test

# Run Python tests with the same bounded parallelism as CI; use -n 0 for a focused test.
[positional-arguments]
test *args:
    uv run --extra dagster --extra inbox pytest -q -n 4 "$@"

[positional-arguments]
test-unit *args:
    just test -m "not integration" "$@"

[positional-arguments]
test-integration *args:
    just test -n 2 -m integration "$@"

# Apply standalone notification and cross-deployment automations.
[positional-arguments]
automations *args:
    uv run scripts/prefect_cli.py --script scripts/apply_automations.py "$@"

[positional-arguments]
autofix-policy-check *args:
    uv run scripts/prefect_cli.py --script -m scripts.check_autofix_policy "$@"

# last N fastmcp backlog snapshots (flows/fastmcp_backlog.py), oldest first
fastmcp-backlog n="24":
    #!/usr/bin/env bash
    set -euo pipefail
    {
        echo "at source issues <7d 7-30d 30-90d >90d unlabeled awaiting prs draft bot"
        ssh -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad 'tail -n {{ n }} /home/stoat/prefect-analytics/fastmcp-backlog.jsonl' \
            | jq -r '[.at[:16], .source, .issues.open, .issues.lt_7d, .issues.d7_30, .issues.d30_90, .issues.gt_90d,
                      (.issues.unlabeled // "-"), (.issues.awaiting_reporter // "-"),
                      .prs.open, (.prs.draft // "-"), (.prs.bot // "-")] | @tsv'
    } | column -t

# Inspect production worker units and the registered source revision.
heavypad-status:
    ssh -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad 'systemctl --user list-units "prefect-heavypad-worker*" --all --no-pager; systemctl --user show pi-exe-worker -p ActiveState -p SubState -p UnitFileState; cat ~/.local/share/prefect-home/registration/registered-sha; df -h /'

# Register the preserved presence revision on its active HeavyPad owner.
presence-deploy:
    uv run scripts/prefect_cli.py --no-prompt deploy --prefect-file prefect.heavypad.yaml --name phone-presence

# Fast, read-only deployment contracts (also enforced in CI).
validate-deployments *args:
    uv run scripts/validate_deployments.py {{ args }}

# Install staged-content validation for this checkout.
hooks:
    uvx --from pre-commit==4.6.0 pre-commit install

# Read-only coding-job evidence inventory; output stays local and private.
[positional-arguments]
coding-jobs output=(env_var('HOME') / '.local/state/mps/coding-jobs') *args:
    uv run scripts/prefect_cli.py --script scripts/coding_jobs_audit.py "$@"

# answer the paused email-triage run in your own words (resumes it with the text)
[positional-arguments]
triage-reply *text:
    uv run scripts/prefect_cli.py --script scripts/triage_reply.py "$@"

# Install the immutable stdlib-only source helper for legacy pinned deployments.
install-source-cache:
    #!/usr/bin/env bash
    set -euo pipefail
    source_file=packages/mps/src/mps/deployment_steps.py
    digest=$(shasum -a 256 "$source_file" | awk '{print $1}')
    destination="/home/stoat/.local/share/mps/source-cache/$digest"
    scp "$source_file" "stoat@heavypad:/tmp/mps-source-cache-$digest.py"
    ssh stoat@heavypad "test \"\$(sha256sum /tmp/mps-source-cache-$digest.py | cut -d' ' -f1)\" = '$digest' && mkdir -p '$destination' && install -m 0444 /tmp/mps-source-cache-$digest.py '$destination/deployment_steps.py' && rm /tmp/mps-source-cache-$digest.py"

# Build the baked exe.dev environment from the locked workspace.
gardener-image image="gardener:dev":
    docker build --platform linux/amd64 -f deploy/gardener/Dockerfile --build-arg SOURCE_REVISION=$(git rev-parse HEAD) -t '{{ image }}' .

# Preserve an existing deployment wheel in a locked image; keep context for reproducible rebuilds.
gardener-wheel-image wheel sha base image context:
    bash deploy/gardener/build-wheel-image.sh '{{ wheel }}' '{{ sha }}' '{{ base }}' '{{ image }}' '{{ context }}'

# Publish through SSH to the private exe.dev registry without a registry password.
gardener-publish image tag:
    bash deploy/gardener/publish-image.sh '{{ image }}' '{{ tag }}'

# Explicit live test: creates two disposable VMs, tests exit/cancel/restart, deletes both.
gardener-probe *args:
    uv run python tests/exe_provider_probe.py {{ args }}

# Inspect the worker without displaying its environment or credentials.
gardener-status:
    ssh stoat@heavypad 'systemctl --user show pi-exe-worker -p ActiveState -p SubState -p ActiveEnterTimestamp -p NRestarts; cat /home/stoat/gardener-exe-worker/current/SOURCE_REVISION'

# Install a committed, locked worker release when its VM inventory is empty.
gardener-install revision="HEAD":
    bash deploy/gardener/install-worker.sh '{{ revision }}'

# Deploy the committed bandwidth monitor without network dependency downloads.
deploy-bandwidth-watchdog:
    ./scripts/deploy_bandwidth_watchdog.sh

# Deploy the committed alerts MCP to heavypad, from its package cache when possible.
deploy-alerts-mcp:
    ./scripts/deploy_alerts_mcp.sh

# Explicit cluster target; never uses the laptop's current kubectl context.
[positional-arguments]
kubectl *args:
    uv run scripts/prefect_kubectl.py "$@"

# Create an encrypted production server backup.
prefect-backup:
    uv run scripts/prefect_remote.py backup

# Copy encrypted backups to R2; never deletes or overwrites.
prefect-offsite:
    uv run scripts/prefect_remote.py offsite

# Verify an offsite backup in an isolated PostgreSQL.
prefect-offsite-verify:
    uv run scripts/prefect_remote.py offsite-verify

# Verify secret consumers; --apply renders changes.
[positional-arguments]
prefect-secrets *args:
    uv run scripts/heavypad_prefect_secrets.py "$@"

[positional-arguments]
presence-ingress *args:
    uv run scripts/heavypad_presence_ingress.py "$@"

# Explicit Cloud workspace; never changes the active profile or home schedules.
[positional-arguments]
cloud-recovery-prefect *args:
    env -u PREFECT_API_AUTH_STRING -u PREFECT_API_KEY PREFECT_API_URL=https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e uv run --with prefect==3.9.3 prefect --profile bot "$@"

[positional-arguments]
recovery-secret-blocks *args:
    uv run --with prefect==3.9.3 python scripts/recovery_secret_blocks.py "$@"

[positional-arguments]
verify-bufo-publication *args:
    uv run --with prefect==3.9.3 python scripts/verify_bufo_publication.py "$@"

[positional-arguments]
verify-cost-publication *args:
    uv run --with prefect==3.9.3 python scripts/verify_cost_publication.py "$@"

# Install a committed app-service release on the configured production host.
edge-install revision:
    bash deploy/prefect-edge/install.sh '{{ revision }}'

# Verify app-service credentials; --apply renders existing canonical values.
[positional-arguments]
edge-secrets *args:
    uv run scripts/prefect_edge_secrets.py "$@"

# send the OpenRouter key from the secrets store to the phone albedo sandbox on heavypad over ssh stdin; nothing is printed
albedo-phone-key:
    #!/usr/bin/env bash
    set -euo pipefail
    sops -d --extract '["openrouter"]["api_key"]' ~/tangled.org/zzstoatzz.io/secrets/prod.yaml \
      | ssh -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad 'umask 077; mkdir -p ~/albedo-phone/home/.albedo; jq -Rn --arg key "$(cat)" "{providers: {openrouter: {extension: \"openai\", baseUrl: \"https://openrouter.ai/api/v1\", apiKey: \$key, model: \"deepseek/deepseek-v4.1-flash\", protocol: \"responses\"}}, active: \"openrouter\"}" > ~/albedo-phone/home/.albedo/config.json && echo "config.json written ($(stat -c %s ~/albedo-phone/home/.albedo/config.json) bytes)"'

# install or update the albedo-phone unit on heavypad from this checkout
albedo-phone-install:
    #!/usr/bin/env bash
    set -euo pipefail
    stage="$(ssh -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad 'mktemp -d')"
    scp -q -r deploy/albedo-sandbox deploy/albedo-phone "stoat@heavypad:$stage/"
    ssh -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad "bash '$stage/albedo-phone/install.sh'; rm -rf '$stage'"

# print the phone's sign-in link (QR code in a terminal); --new-link replaces it
albedo-phone-link *args:
    ssh -t -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad 'ALBEDO_SANDBOX=$HOME/albedo-phone $HOME/.local/share/albedo-phone/sandbox.sh albedo web --no-open --local --port 4317 --public-url https://heavypad.tailb660b6.ts.net:11443 {{ args }}'
