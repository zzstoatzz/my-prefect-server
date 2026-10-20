#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PREFECT_API_URL='https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e'
unset PREFECT_API_AUTH_STRING
flow_id=aa69d171-a0a1-40f7-9679-4b811550ad26
plan=deploy/agentic-workflows/fastmcp-issue-pr.json
api() { prefect --profile bot --no-prompt api "$@"; }

case "${1:-}" in
  validate)
    api POST /execution-plans/validate --data "@$plan"
    ;;
  publish)
    api POST /execution-plans/validate --data "@$plan" | jq -e '.valid == true'
    version=$(api POST "/flows/$flow_id/execution-plan/versions" --data "@$plan")
    version_id=$(jq -er '.id' <<<"$version")
    api POST "/flows/$flow_id/execution-plan/versions/$version_id/activate"
    ;;
  run)
    issue=${2:?issue number required}
    key=${3:?idempotency key required; reuse it to recover an uncertain submission}
    [[ $issue =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid issue number' >&2; exit 2; }
    body=$(jq -nc --argjson issue "$issue" --arg key "$key" \
      '{name:("issue-pr-"+($issue|tostring)),parameters:{issue_number:$issue},idempotency_key:$key}')
    api POST "/flows/$flow_id/execution-plan/runs" --data "$body"
    ;;
  results)
    run=${2:?run ID required}
    state=$(api GET "/flow_runs/$run/execution-plan")
    jq '{status,nodes:[.nodes[]|{node,status,activation_id,outputs}]}' <<<"$state"
    while IFS=$'\t' read -r node activation output; do
      api GET "/flow_runs/$run/execution-plan/activations/$activation/outputs/$output" \
        | jq --arg node "$node" --arg output "$output" '{node:$node,output:$output,value:.}'
    done < <(jq -r '.nodes[] | . as $n | .outputs[]? | select(.status=="selected") | [$n.node,$n.activation_id,.output] | @tsv' <<<"$state")
    ;;
  *)
    echo 'Usage: just agentic-fastmcp validate|publish|run ISSUE IDEMPOTENCY_KEY|results RUN_ID' >&2
    exit 2
    ;;
esac
