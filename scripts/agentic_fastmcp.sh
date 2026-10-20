#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PREFECT_API_URL='https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e'
unset PREFECT_API_AUTH_STRING
flow_id=aa69d171-a0a1-40f7-9679-4b811550ad26
plan=deploy/agentic-workflows/fastmcp-issue-pr.json
api() { prefect --profile bot --no-prompt api "$@" </dev/null; }

case "${1:-}" in
  deploy-validation)
    MPS_AGENTIC_REF=$(git rev-parse HEAD) prefect --profile bot --no-prompt deploy \
      --prefect-file deploy/agentic-workflows/prefect.yaml --all
    ;;
  validate)
    api POST /execution-plans/validate --data "@$plan"
    ;;
  publish)
    api POST /execution-plans/validate --data "@$plan" | jq -e '.valid == true'
    version=$(api POST "/flows/$flow_id/execution-plan/versions" --data "@$plan")
    version_id=$(jq -er '.id' <<<"$version")
    api POST "/flows/$flow_id/execution-plan/versions/$version_id/activate" \
      | jq '{version_id:.active_version.id,semantic_hash:.active_version.semantic_hash}'
    ;;
  run)
    issue=${2:?issue number required}
    key=${3:?idempotency key required; reuse it to recover an uncertain submission}
    [[ $issue =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid issue number' >&2; exit 2; }
    body=$(jq -nc --argjson issue "$issue" --arg key "$key" \
      '{name:("issue-pr-"+($issue|tostring)),parameters:{issue_number:$issue},idempotency_key:$key}')
    if [[ -n ${4:-} ]]; then
      body=$(jq --rawfile context "$4" '.parameters.followup_context=$context' <<<"$body")
    fi
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
  verify)
    while IFS=$'\t' read -r issue run expected fix_status; do
      results=$(bash scripts/agentic_fastmcp.sh results "$run")
      jq -es --arg expected "$expected" --argjson issue "$issue" --arg fix_status "$fix_status" '
        (.[0].status == "completed") and
        (any(.[]; .node == "eligibility" and
          .output == (if $expected == "eligible" then "eligible" else "deferred" end) and
          .value.issue_number == $issue and .value.decision == $expected)) and
        (if $expected == "eligible" then
          any(.[]; .node == "fix" and .value.status == $fix_status and .value.pr_url == "")
        else
          (.[0].nodes | any(.node == "fix" and .status == "unreached"))
        end) and
        (any(.[]; .node == "report" and .value.issue_number == $issue))
      ' <<<"$results" >/dev/null
      jq -nc --argjson issue "$issue" --arg decision "$expected" \
        '{issue_number:$issue,decision:$decision,verified:true}'
    done < <(jq -r '.cases[] | [.issue_number,.run_id,.decision,(.fix_status // "unreached")] | @tsv' deploy/agentic-workflows/gate-cases.json)
    ;;
  *)
    echo 'Usage: just agentic-fastmcp validate|publish|run ISSUE KEY [CONTEXT_FILE]|results RUN_ID|verify' >&2
    exit 2
    ;;
esac
