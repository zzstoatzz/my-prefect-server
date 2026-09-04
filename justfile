# my-prefect-server
# required env vars: HCLOUD_TOKEN, POSTGRES_PASSWORD, AUTH_STRING, DOMAIN, LETSENCRYPT_EMAIL
# optional env vars: GRAFANA_DOMAIN (default: prefect-metrics.waow.tech)

set dotenv-load

export KUBECONFIG := source_directory() / "kubeconfig.yaml"

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

# run a prefect CLI command against the remote server
prefect *args:
    PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
        uv run --with prefect prefect {{args}}

# push main to both remotes; github first, since the tangled push triggers
# CI that installs the package from the github mirror
push:
    git push github main
    git push origin main

# --- infrastructure ---

# initialize terraform
init:
    terraform -chdir=infra init

# create the hetzner server with k3s
infra:
    terraform -chdir=infra apply -var="hcloud_token=$HCLOUD_TOKEN"

# destroy all infrastructure
destroy:
    terraform -chdir=infra destroy -var="hcloud_token=$HCLOUD_TOKEN"

# get the server IP from terraform
server-ip:
    #!/usr/bin/env bash
    set -euo pipefail
    if IP=$(terraform -chdir=infra output -raw server_ip 2>/dev/null) && [ -n "$IP" ]; then
        printf '%s\n' "$IP"
        exit 0
    fi
    if [ -f kubeconfig.yaml ]; then
        ruby -ryaml -ruri -e 'puts URI(YAML.load_file("kubeconfig.yaml")["clusters"][0]["cluster"]["server"]).host'
        exit 0
    fi
    echo "no terraform state or kubeconfig.yaml available" >&2
    exit 1

# ssh into the server
ssh:
    ssh root@$(just server-ip)

# fetch kubeconfig from the server (run after cloud-init finishes)
kubeconfig:
    #!/usr/bin/env bash
    set -euo pipefail
    IP=$(just server-ip)
    echo "fetching kubeconfig from $IP..."
    until ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new root@$IP test -f /run/k3s-ready 2>/dev/null; do
        echo "  waiting for k3s..."
        sleep 5
    done
    scp root@$IP:/etc/rancher/k3s/k3s.yaml kubeconfig.yaml
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "s|127.0.0.1|$IP|g" kubeconfig.yaml
    else
        sed -i "s|127.0.0.1|$IP|g" kubeconfig.yaml
    fi
    chmod 600 kubeconfig.yaml
    echo "kubeconfig written"
    kubectl get nodes

# --- cluster ---

# deploy everything to the cluster (idempotent)
deploy:
    #!/usr/bin/env bash
    set -euo pipefail

    helm repo add jetstack https://charts.jetstack.io
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update

    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    : "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
    : "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL}"
    GRAFANA_DOMAIN="${GRAFANA_DOMAIN:-prefect-metrics.waow.tech}"
    # path to a checkout of tangled.org/zzstoatzz.io/prefect-server.
    # default sibling-directory layout per [reference_repo_path_convention].
    CHART_PATH="${PREFECT_SERVER_CHART_PATH:-../prefect-server/charts/prefect-server}"

    if [ ! -f "$CHART_PATH/Chart.yaml" ]; then
        echo "==> ERROR: prefect-server chart not found at $CHART_PATH"
        echo "    clone tangled.org/zzstoatzz.io/prefect-server next to this repo,"
        echo "    or set PREFECT_SERVER_CHART_PATH"
        exit 1
    fi

    echo "==> creating namespaces"
    kubectl create namespace prefect --dry-run=client -o yaml | kubectl apply -f -
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
    kubectl apply -f deploy/prefect-limits.yaml

    echo "==> installing cert-manager"
    helm upgrade --install cert-manager jetstack/cert-manager \
        --namespace cert-manager --create-namespace \
        --set crds.enabled=true \
        --wait

    echo "==> applying cluster issuer"
    sed "s|LETSENCRYPT_EMAIL_PLACEHOLDER|$LETSENCRYPT_EMAIL|g" deploy/cluster-issuer.yaml \
        | kubectl apply -f -

    echo "==> creating prefect auth secret"
    kubectl create secret generic prefect-auth \
        --namespace prefect \
        --from-literal=auth-string="$AUTH_STRING" \
        --dry-run=client -o yaml | kubectl apply -f -

    echo "==> applying standalone postgres + redis (zig chart is BYO)"
    sed "s|POSTGRES_PASSWORD_PLACEHOLDER|$POSTGRES_PASSWORD|g" deploy/prefect-postgres.yaml \
        | kubectl apply -f -
    kubectl apply -f deploy/prefect-redis.yaml
    kubectl -n prefect wait --for=condition=available --timeout=120s \
        deployment/prefect-postgres deployment/prefect-redis

    echo "==> installing prefect server (zig chart from $CHART_PATH)"
    sed "s|DOMAIN_PLACEHOLDER|$DOMAIN|g" deploy/prefect-values.yaml \
        | helm upgrade --install prefect-server "$CHART_PATH" \
            --namespace prefect \
            --values - \
            --wait --timeout 5m

    echo "==> installing monitoring stack"
    sed "s|GRAFANA_DOMAIN_PLACEHOLDER|$GRAFANA_DOMAIN|g" deploy/monitoring-values.yaml \
        | helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
            --namespace monitoring \
            --values - \
            --wait --timeout 5m

    echo "==> applying grafana ingress"
    sed "s|GRAFANA_DOMAIN_PLACEHOLDER|$GRAFANA_DOMAIN|g" deploy/grafana-ingress.yaml \
        | kubectl apply -f -

    echo "==> loading prefect dashboards"
    for dashboard in deploy/dashboards/*.json; do
        name=$(basename "$dashboard" .json | tr '.' '-')
        kubectl create configmap "prefect-dashboard-$name" \
            --namespace monitoring \
            --from-file="$dashboard" \
            --dry-run=client -o yaml \
            | kubectl label --local -f - grafana_dashboard=1 -o yaml \
            | kubectl apply -f -
    done

    echo ""
    echo "done. point DNS:"
    echo "  $DOMAIN -> $(just server-ip)"
    echo "  $GRAFANA_DOMAIN -> $(just server-ip)"

# build the Zig Prefect server on the Hetzner node and import it into k3s
publish-server-remote optimize="ReleaseFast":
    #!/usr/bin/env bash
    set -euo pipefail
    SERVER=$(just server-ip)
    OPTIMIZE="{{ optimize }}"
    LABEL="$OPTIMIZE"
    SERVER_SRC="${PREFECT_SERVER_SOURCE:-../prefect-server}"
    if [ ! -d "$SERVER_SRC/.git" ]; then
      echo "ABORT: PREFECT_SERVER_SOURCE must point to the prefect-server checkout"
      echo "current value: $SERVER_SRC"
      exit 1
    fi
    if [ -n "$(git -C "$SERVER_SRC" status --porcelain)" ]; then
      echo "ABORT: $SERVER_SRC working tree is dirty — refusing to build unreconstructable source"
      git -C "$SERVER_SRC" status --porcelain
      exit 1
    fi

    echo "==> syncing prefect-server source to $SERVER"
    rsync -az --delete \
      --exclude='.zig-cache' --exclude='zig-out' --exclude='runtime-lib' \
      --exclude='.env' --exclude='.venv' \
      "$SERVER_SRC"/ root@"$SERVER":/opt/prefect-server/
    ssh root@"$SERVER" 'find /opt/prefect-server \
      \( -path /opt/prefect-server/.zig-cache \
         -o -path /opt/prefect-server/zig-out \
         -o -path /opt/prefect-server/runtime-lib \
         -o -path /opt/prefect-server/.venv \) -prune \
      -o -exec chown root:root {} +'

    ssh root@"$SERVER" "cat > /tmp/prefect-server-build.sh" <<SCRIPT
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v zig >/dev/null 2>&1; then
      echo "==> installing zig 0.16.0 on node"
      mkdir -p /opt/zig
      if [ ! -x /opt/zig/zig-x86_64-linux-0.16.0/zig ]; then
        curl -fsSL https://ziglang.org/download/0.16.0/zig-x86_64-linux-0.16.0.tar.xz \
          | tar -xJ -C /opt/zig
      fi
      ln -sf /opt/zig/zig-x86_64-linux-0.16.0/zig /usr/local/bin/zig
    fi

    cd /opt/prefect-server
    if [ -n "\$(git status --porcelain)" ]; then
      echo "ABORT: /opt/prefect-server working tree is dirty after sync"
      git status --porcelain
      exit 1
    fi

    TAG=\$(git rev-parse --short HEAD)
    IMAGE="atcr.io/zzstoatzz.io/prefect-server:${LABEL}-\${TAG}"

    echo "==> building binary (\${TAG}, ${OPTIMIZE})"
    zig build -Doptimize=${OPTIMIZE} -Dtarget=x86_64-linux-musl -Dgit_sha=\${TAG}

    echo "==> collecting runtime libraries"
    rm -rf runtime-lib
    mkdir -p runtime-lib
    find .zig-cache -name "libfacil.io.so" -exec cp {} runtime-lib/ \; 2>/dev/null || true

    echo "==> building container image (\${IMAGE})"
    buildah bud -t "\${IMAGE}" -f Dockerfile.runtime .

    echo "==> importing into k3s containerd"
    buildah push "\${IMAGE}" docker-archive:/tmp/prefect-server.tar:"\${IMAGE}"
    ctr -n k8s.io images import /tmp/prefect-server.tar
    rm -f /tmp/prefect-server.tar

    echo "==> updating deployments"
    kubectl set image deployment/prefect-server-webserver -n prefect prefect-server="\${IMAGE}"
    kubectl set image deployment/prefect-server-services -n prefect prefect-server="\${IMAGE}"
    kubectl rollout status deployment/prefect-server-webserver -n prefect --timeout=180s
    kubectl rollout status deployment/prefect-server-services -n prefect --timeout=180s

    echo "==> deployed \${IMAGE}"
    SCRIPT

    ssh root@"$SERVER" 'setsid bash /tmp/prefect-server-build.sh >/tmp/prefect-server-deploy.log 2>&1 </dev/null & echo $! >/tmp/prefect-server-deploy.pid'
    echo "==> build running detached on $SERVER (log: /tmp/prefect-server-deploy.log)"
    echo "==> safe to disconnect now — reattach with: just server-deploy-logs"
    ssh root@"$SERVER" 'tail -n +1 -f /tmp/prefect-server-deploy.log & TPID=$!; PID=$(cat /tmp/prefect-server-deploy.pid); while kill -0 "$PID" 2>/dev/null; do sleep 2; done; sleep 1; kill "$TPID" 2>/dev/null' || true

# assert the server is actually DOING work, not merely alive.
#
# After the 2026-08-13 outage: pods Running, worker ONLINE and heartbeating,
# /health 200, zero runs late — while every flow run had crashed for 100
# minutes. Liveness is not throughput.
#
# Filtering happens *client-side* on purpose. The first version of this recipe
# asked the server to filter by end_time, which the deployed version did not
# support, so the predicate was ignored and it cheerfully reported 8936
# completions "since the deploy". A verifier must not depend on features of
# the thing it is verifying.
verify-deploy minutes="12":
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    AUTH=$(printf "$AUTH_STRING" | base64)
    SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    DEADLINE=$(( $(date +%s) + {{ minutes }} * 60 ))

    recent() {
        curl -s -X POST "https://$DOMAIN/api/flow_runs/filter" \
            -H "Authorization: Basic $AUTH" -H 'content-type: application/json' \
            -d "{\"sort\":\"$1\",\"limit\":30}"
    }

    echo "==> watching for flow runs finishing after $SINCE (up to {{ minutes }}m)"
    while [ "$(date +%s)" -lt "$DEADLINE" ]; do
        runs=$(recent END_TIME_DESC)

        crashed=$(printf '%s' "$runs" | jq -r --arg since "$SINCE" \
            '[.[] | select(.state_type == "CRASHED" and (.state_timestamp // .updated) > $since)] | length')
        if [ "${crashed:-0}" -gt 0 ]; then
            echo "FAIL: $crashed flow run(s) CRASHED since the deploy"
            printf '%s' "$runs" | jq -r --arg since "$SINCE" \
                '.[] | select(.state_type == "CRASHED" and (.state_timestamp // .updated) > $since)
                 | "  \(.name): \(.state_message // "no message")"' | head -3
            exit 1
        fi

        done_since=$(printf '%s' "$runs" | jq -r --arg since "$SINCE" \
            '[.[] | select(.state_type == "COMPLETED" and (.end_time // "") > $since)] | length')
        if [ "${done_since:-0}" -gt 0 ]; then
            echo "OK: $done_since flow run(s) completed since the deploy"
            printf '%s' "$runs" | jq -r --arg since "$SINCE" \
                '.[] | select(.state_type == "COMPLETED" and (.end_time // "") > $since) | "  \(.end_time) \(.name)"' | head -3
            exit 0
        fi
        sleep 20
    done

    echo "FAIL: no flow run completed within {{ minutes }}m of the deploy"
    echo "      (worker alive but not producing — check 'just logs' and the work pool)"
    exit 1

# follow the most recent remote Prefect server deploy log
server-deploy-logs:
    SERVER=$(just server-ip); ssh root@"$SERVER" 'tail -n 200 -f /tmp/prefect-server-deploy.log'

# create the analytics hostPath + results PVC and reconcile the Kubernetes work pool
storage: _analytics-dir
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    echo "==> creating results PVC"
    kubectl apply -f deploy/prefect-limits.yaml
    kubectl apply -f deploy/results-pvc.yaml
    just work-pool

# apply declarative Prefect work-pool templates stored in deploy/work-pools
work-pool:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    echo "==> applying kubernetes-pool base job template"
    PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
        uv run --with prefect prefect work-pool update \
            kubernetes-pool \
            --base-job-template deploy/work-pools/kubernetes-pool-base-job-template.json

_analytics-dir:
    ssh root@$(just server-ip) "mkdir -p /var/lib/prefect-analytics"

# --- operations ---

# check cluster state
status:
    @echo "==> nodes"
    @kubectl top nodes
    @echo ""
    @echo "==> pods (by memory)"
    @kubectl top pods --all-namespaces --sort-by=memory
    @echo ""
    @echo "==> pods (prefect)"
    @kubectl get pods -n prefect
    @echo ""
    @echo "==> pods (monitoring)"
    @kubectl get pods -n monitoring

# tail logs for a component (server, background-services, etc.)
logs component="prefect-server":
    kubectl logs -n prefect -l app.kubernetes.io/name={{component}} -f

# check prefect API health
health:
    #!/usr/bin/env bash
    : "${DOMAIN:?set DOMAIN}"
    curl -sf "https://$DOMAIN/api/health" | jq .

# reload grafana dashboards from deploy/dashboards/
dashboards:
    #!/usr/bin/env bash
    set -euo pipefail
    for dashboard in deploy/dashboards/*.json; do
        name=$(basename "$dashboard" .json | tr '.' '-')
        kubectl create configmap "prefect-dashboard-$name" \
            --namespace monitoring \
            --from-file="$dashboard" \
            --dry-run=client -o yaml \
            | kubectl label --local -f - grafana_dashboard=1 -o yaml \
            | kubectl apply -f -
        echo "  loaded $name"
    done

# --- analytics ---

# first-time dbt setup: install deps, seed reference data, compile models
init-analytics:
    cd analytics && uv run dbt deps && uv run dbt seed && uv run dbt compile

# --- hub ---

# build the hub container image (linux/amd64 for hetzner k3s node)
build-web:
    docker build --platform linux/amd64 -t atcr.io/zzstoatzz.io/hub:latest web/

# build and push the hub image
push-web: build-web
    docker push atcr.io/zzstoatzz.io/hub:latest

# build the hub image on the Hetzner node and import it into k3s
publish-web-remote:
    #!/usr/bin/env bash
    set -euo pipefail
    SERVER=$(just server-ip)
    SRC="${MY_PREFECT_SERVER_SOURCE:-.}"
    if ! git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1; then
      echo "ABORT: MY_PREFECT_SERVER_SOURCE must point to this repo checkout"
      echo "current value: $SRC"
      exit 1
    fi
    if [ -n "$(git -C "$SRC" status --porcelain)" ]; then
      echo "ABORT: $SRC working tree is dirty — refusing to build unreconstructable source"
      git -C "$SRC" status --porcelain
      exit 1
    fi
    TAG=$(git -C "$SRC" rev-parse --short HEAD)

    echo "==> syncing hub source to $SERVER"
    rsync -az --delete \
      --exclude='.git' --exclude='.venv' --exclude='node_modules' \
      --exclude='web/node_modules' --exclude='web/.svelte-kit' --exclude='web/build' \
      --exclude='.env' --exclude='kubeconfig.yaml' \
      "$SRC"/ root@"$SERVER":/opt/my-prefect-server/
    ssh root@"$SERVER" 'chown -R root:root /opt/my-prefect-server'

    ssh root@"$SERVER" "cat > /tmp/hub-build.sh" <<'SCRIPT'
    #!/usr/bin/env bash
    set -euo pipefail
    cd /opt/my-prefect-server
    TAG="__HUB_TAG__"
    IMAGE="atcr.io/zzstoatzz.io/hub:${TAG}"

    echo "==> building hub container image (${IMAGE})"
    buildah bud -t "$IMAGE" -f web/Dockerfile web

    echo "==> importing into k3s containerd"
    buildah push "$IMAGE" docker-archive:/tmp/hub.tar:"$IMAGE"
    ctr -n k8s.io images import /tmp/hub.tar
    rm -f /tmp/hub.tar

    echo "==> applying hub manifests"
    kubectl apply -f deploy/hub-deployment.yaml
    sed "s|HUB_DOMAIN_PLACEHOLDER|hub.waow.tech|g" deploy/hub-ingress.yaml | kubectl apply -f -
    kubectl set image deployment/hub -n prefect hub="$IMAGE"
    kubectl rollout status deployment/hub -n prefect --timeout=180s
    echo "==> deployed $IMAGE"
    SCRIPT
    ssh root@"$SERVER" "sed -i 's/__HUB_TAG__/$TAG/g' /tmp/hub-build.sh"

    ssh root@"$SERVER" 'bash /tmp/hub-build.sh'

# apply hub k8s manifests and restart the pod to pull the new image
deploy-web:
    kubectl apply -f deploy/hub-deployment.yaml
    sed "s|HUB_DOMAIN_PLACEHOLDER|hub.waow.tech|g" deploy/hub-ingress.yaml | kubectl apply -f -
    kubectl rollout restart deployment/hub -n prefect

# build, push, and deploy hub
web: push-web deploy-web

# everything CI runs before it deploys: lint, format, types, tests, the hub's svelte-check and oxlint
check:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check
    uv run pytest -q
    cd web && bun install --frozen-lockfile && bun run check && bun run lint
    ./scripts/deployments_inventory.py --check

# regenerate docs/deployments.md from prefect.yaml; `--check` is what CI runs
inventory *args:
    ./scripts/deployments_inventory.py {{ args }}

# apply standalone automations from deploy/automations.yaml (send-notification
# and cross-deployment triggers, which prefect.yaml cannot express)
automations *args:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
        ./scripts/apply_automations.py {{ args }}

# heavypad, the home-pool worker box: what is installed, whether it matches
# what the deployments expect, and whether pi can still reach Luna. ten
# seconds over tailscale ssh; run it before blaming a flow. no values from the
# env file are printed, only key names.
heavypad-status:
    #!/usr/bin/env bash
    set -euo pipefail
    want="$(git rev-parse HEAD)"
    mirror="$(git ls-remote https://github.com/zzstoatzz/my-prefect-server.git refs/heads/main | cut -f1)"
    echo "local main:   ${want}"
    echo "github main:  ${mirror:-missing}  $([ "$mirror" = "$want" ] && echo '(current)' || echo '(BEHIND — push github)')"
    ssh -o ConnectTimeout=10 -o BatchMode=yes stoat@heavypad 'export PATH=/home/stoat/.local/bin:/usr/local/bin:/usr/bin:/bin
      echo "worker:       $(systemctl is-active prefect-home-worker) / health $(curl -s -m 5 http://127.0.0.1:8080/health | tr -d "\n")"
      echo "uv:           $(uv --version | cut -d" " -f2)   python: $(uv python list --only-installed 2>/dev/null | awk "{print \$1}" | sed "s/cpython-//; s/-linux.*//" | sort -u | tr "\n" " ")"
      echo "node:         $(node --version)   pi: $(pi --version 2>&1 | head -1)   (pi needs node >= 22.19)"
      echo "codex login:  $(pi auth check --provider openai-codex --no-refresh 2>&1 | head -1); expires $(python3 -c "import json,datetime;e=json.load(open(\"/home/stoat/.pi/agent/auth.json\"))[\"openai-codex\"][\"expires\"];print(datetime.datetime.fromtimestamp(e/1000,datetime.timezone.utc).strftime(\"%Y-%m-%d %H:%MZ\"))" 2>/dev/null || echo "none")"
      echo "uv cache:     $(ls /home/stoat/.cache/uv/archive-v0 2>/dev/null | wc -l) archive envs; prune with: uv cache prune"
      echo "env file keys: $(cut -d= -f1 /home/stoat/.config/prod-worker/env | sort | tr "\n" " ")"
      echo "disk:         $(df -h / | awk "NR==2{print \$5\" used of \"\$2}")"'
