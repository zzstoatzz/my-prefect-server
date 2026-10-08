#!/usr/bin/env bash
set -euo pipefail

: "${KUBECONFIG:?set the explicit production kubeconfig}"
test -f "$KUBECONFIG"
cd "$(dirname "$0")/.."
k=(kubectl -n prefect)
old=prefect-redis
new=prefect-redis-durable

info() {
    "${k[@]}" exec "deployment/$1" -- redis-cli -e --raw INFO "$2" | tr -d '\r'
}

pod_ip() {
    "${k[@]}" get pods -l "app=$1" -o json | jq -er '
      [.items[] | select(.status.phase == "Running") | .status.podIP]
      | if length == 1 then .[0] else error("expected exactly one running pod") end'
}

case "${1:?choose prepare, promote, route, or status}" in
    prepare)
        test "$("${k[@]}" get service prefect-redis -o jsonpath='{.spec.selector.app}')" = "$old"
        info "$old" replication | grep -qx 'role:master'
        kubectl create --dry-run=client -f deploy/prefect-redis.yaml -o json |
            jq '{apiVersion:"v1",kind:"List",items:[.items[] | select(.kind != "Service")]}' |
            "${k[@]}" apply -f -
        "${k[@]}" rollout status "deployment/$new" --timeout=120s
        "${k[@]}" exec "deployment/$new" -- redis-cli -e REPLICAOF "$(pod_ip "$old")" 6379
        info "$new" replication
        ;;
    promote)
        expected=${2:?supply the tested server commit SHA}
        [[ "$expected" =~ ^[a-f0-9]{40}$ ]]
        "${k[@]}" get deployment prefect-server-webserver prefect-server-services -o json |
            jq -e --arg suffix ":$expected" 'all(.items[].spec.template.spec.containers[]; .image | endswith($suffix))' >/dev/null
        test "$("${k[@]}" get service prefect-redis -o jsonpath='{.spec.selector.app}')" = "$old"
        info "$old" replication | grep -qx 'role:master'
        replica=$(info "$new" replication)
        grep -qx 'role:slave' <<<"$replica"
        grep -qx 'master_link_status:up' <<<"$replica"
        grep -qx 'master_sync_in_progress:0' <<<"$replica"
        "${k[@]}" exec "deployment/$old" -- redis-cli -e FAILOVER TO "$(pod_ip "$new")" 6379 TIMEOUT 10000
        info "$old" replication
        ;;
    route)
        info "$new" replication | grep -qx 'role:master'
        previous=$(info "$old" replication)
        grep -qx 'role:slave' <<<"$previous"
        grep -qx "master_host:$(pod_ip "$new")" <<<"$previous"
        grep -qx 'master_failover_state:no-failover' <<<"$previous"
        persistence=$(info "$new" persistence)
        grep -qx 'aof_enabled:1' <<<"$persistence"
        grep -qx 'aof_rewrite_in_progress:0' <<<"$persistence"
        grep -qx 'aof_last_write_status:ok' <<<"$persistence"
        "${k[@]}" apply -f deploy/prefect-redis.yaml
        "${k[@]}" get endpoints prefect-redis
        ;;
    status)
        "${k[@]}" get service prefect-redis -o wide
        info "$old" replication
        info "$new" replication
        info "$new" persistence
        ;;
    *) exit 2 ;;
esac
