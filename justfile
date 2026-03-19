# my-prefect-server deployment (k3s + helm)
# required env vars: HCLOUD_TOKEN, POSTGRES_PASSWORD, AUTH_STRING, DOMAIN, LETSENCRYPT_EMAIL
# optional env vars: GRAFANA_DOMAIN (default: prefect-metrics.waow.tech)

set dotenv-load

export KUBECONFIG := source_directory() / "kubeconfig.yaml"

# --- infrastructure ---

# initialize terraform
init:
    terraform -chdir=infra init

# create the hetzner server with k3s
infra:
    terraform -chdir=infra apply -var="hcloud_token=$HCLOUD_TOKEN"

# plan infra changes
infra-plan:
    terraform -chdir=infra plan -var="hcloud_token=$HCLOUD_TOKEN"

# destroy all infrastructure
destroy:
    terraform -chdir=infra destroy -var="hcloud_token=$HCLOUD_TOKEN"

# get the server IP from terraform
server-ip:
    @terraform -chdir=infra output -raw server_ip

# ssh into the server
ssh:
    ssh root@$(just server-ip)

# --- cluster access ---

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

# --- deployment ---

# deploy everything to the cluster
deploy:
    #!/usr/bin/env bash
    set -euo pipefail

    helm repo add prefect https://prefecthq.github.io/prefect-helm
    helm repo add jetstack https://charts.jetstack.io
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update

    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    : "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}"
    : "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL}"
    GRAFANA_DOMAIN="${GRAFANA_DOMAIN:-prefect-metrics.waow.tech}"

    echo "==> creating namespaces"
    kubectl create namespace prefect --dry-run=client -o yaml | kubectl apply -f -
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

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

    echo "==> installing prefect server"
    sed "s|DOMAIN_PLACEHOLDER|$DOMAIN|g" deploy/prefect-values.yaml \
        | helm upgrade --install prefect-server prefect/prefect-server \
            --namespace prefect \
            --values - \
            --set postgresql.auth.password="$POSTGRES_PASSWORD" \
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

    echo "==> installing prefect exporter"
    helm upgrade --install prometheus-prefect-exporter prefect/prometheus-prefect-exporter \
        --namespace prefect \
        --values deploy/exporter-values.yaml \
        --wait --timeout 2m

    echo ""
    echo "done. point DNS:"
    echo "  $DOMAIN -> $(just server-ip)"
    echo "  $GRAFANA_DOMAIN -> $(just server-ip)"
    echo "then check:"
    echo "  curl https://$DOMAIN/api/health"
    echo "  curl https://$GRAFANA_DOMAIN"

# deploy the kubernetes worker to the cluster
worker:
    kubectl apply -f deploy/worker.yaml

# create the analytics hostPath directory on the node (run once after cluster is up)
analytics-storage:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "==> creating /var/lib/prefect-analytics on the k3s node"
    ssh root@$(just server-ip) "mkdir -p /var/lib/prefect-analytics"
    echo "done — Grafana and flow pods can now share analytics.duckdb via hostPath"

# create the results PVC, analytics hostPath, and patch the work pool
results-storage: analytics-storage
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DOMAIN:?set DOMAIN}"
    : "${AUTH_STRING:?set AUTH_STRING}"
    echo "==> creating results PVC"
    kubectl apply -f deploy/results-pvc.yaml
    echo "==> patching kubernetes-pool base job template"
    PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
        uv run --with prefect python scripts/patch_work_pool.py

# register flow deployments (run locally with PREFECT_API_URL + PREFECT_API_AUTH_STRING)
register-flows:
    PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
        uv run --with prefect prefect --no-prompt deploy --all

# --- prefect resources (terraform) ---

# initialize prefect terraform
prefect-init:
    terraform -chdir=prefect init

# apply prefect resources
prefect-apply:
    terraform -chdir=prefect apply \
        -var="domain=$DOMAIN" \
        -var="auth_string=$AUTH_STRING"

# plan prefect resource changes
prefect-plan:
    terraform -chdir=prefect plan \
        -var="domain=$DOMAIN" \
        -var="auth_string=$AUTH_STRING"

# --- operations ---

# check the state of everything
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

# tail logs for a component (server, background-services, worker)
logs component="prefect-server":
    kubectl logs -n prefect -l app.kubernetes.io/name={{component}} -f

# check prefect health via public endpoint
health:
    #!/usr/bin/env bash
    : "${DOMAIN:?set DOMAIN}"
    curl -sf "https://$DOMAIN/api/health" | jq .

# first-time dbt setup: install deps, seed reference data, compile models
init-analytics:
    cd analytics && uv run dbt deps && uv run dbt seed && uv run dbt compile

# run a prefect CLI command against the remote server
prefect *args:
    PREFECT_API_URL="https://$DOMAIN/api" PREFECT_API_AUTH_STRING="$AUTH_STRING" \
        uv run --with prefect prefect {{args}}

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
