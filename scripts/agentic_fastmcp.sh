#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PREFECT_API_URL='https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e'
unset PREFECT_API_AUTH_STRING
flow_id=aa69d171-a0a1-40f7-9679-4b811550ad26
continuation_flow_id=6afddacc-ee55-4190-a5dc-13704bf5ddd2
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
  publish|publish-continuation)
    if [[ $1 == publish-continuation ]]; then
      flow_id=$continuation_flow_id
      plan=deploy/agentic-workflows/fastmcp-publish-patch.json
    fi
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
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    [[ -n ${AGENTIC_BATCH_POLICY_FILE:-} ]] || { echo 'A bounded batch policy is required' >&2; exit 2; }
    jq -e --argjson issue "$issue" '
      . as $p | .max_prs >= 1 and .max_prs <= 3 and
      (.issue_numbers|length) <= 12 and (.issue_numbers|index($issue)) != null and
      .started_at <= now and now < .expires_at and .expires_at <= .started_at + 7200
    ' "$AGENTIC_BATCH_POLICY_FILE" >/dev/null
    bash scripts/collect_fastmcp_evidence.sh "$issue" > "$scratch/evidence.json"
    bash scripts/load_fastmcp_skills.sh > "$scratch/instructions.json"
    body=$(jq -nc --argjson issue "$issue" --arg key "$key" \
      --slurpfile instructions "$scratch/instructions.json" \
      --slurpfile evidence "$scratch/evidence.json" --slurpfile batch "$AGENTIC_BATCH_POLICY_FILE" \
      '{name:("issue-pr-"+($issue|tostring)),parameters:({issue_number:$issue,evidence:$evidence[0],batch:$batch[0]} + $instructions[0]),idempotency_key:$key}')
    if [[ -n ${4:-} ]]; then
      body=$(jq --rawfile context "$4" '.parameters.followup_context=$context' <<<"$body")
    fi
    printf '%s\n' "$body" > "$scratch/request.json"
    api POST "/flows/$flow_id/execution-plan/runs" --data "@$scratch/request.json"
    ;;
  continue)
    source_run=${2:?source run ID required}
    validation_run=${3:?completed validation run ID required}
    key=${4:?idempotency key required}
    state=$(api GET "/flow_runs/$source_run/execution-plan")
    jq -e --arg flow "$flow_id" '
      .snapshot.flow_id == $flow and
      (.status == "completed" or .status == "failed") and
      any(.nodes[]; .node == "publish" and .status == "unreached")
    ' <<<"$state" >/dev/null
    eligibility=$(jq -er '.nodes[]|select(.node=="eligibility")|.activation_id' <<<"$state")
    preparation=$(jq -er '.nodes[]|select(.node=="fix")|.activation_id' <<<"$state")
    candidate=$(api GET "/flow_runs/$source_run/execution-plan/activations/$eligibility/outputs/eligible")
    patch=$(api GET "/flow_runs/$source_run/execution-plan/activations/$preparation/outputs/ready")
    jq -e '.decision == "eligible"' <<<"$candidate" >/dev/null
    validation=$(api GET "/flow_runs/$validation_run")
    jq -e --argjson patch "$patch" '
      .deployment_id == "5f1e86fb-d975-47e5-a85d-a45b3dd04865" and
      .state_type == "COMPLETED" and .parameters == $patch
    ' <<<"$validation" >/dev/null
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    api GET "/flow_runs/$source_run" | jq -e '.parameters.batch' > "$scratch/batch.json"
    bash scripts/load_fastmcp_skills.sh "$(jq -er '.base_sha' <<<"$patch")" \
      | jq '{review_instructions}' > "$scratch/instructions.json"
    body=$(jq -nc --arg source "$source_run" --arg key "$key" \
      --arg validation "$validation_run" \
      --argjson candidate "$candidate" --argjson patch "$patch" --slurpfile instructions "$scratch/instructions.json" \
      --slurpfile batch "$scratch/batch.json" \
      '{name:("resume-"+$source),idempotency_key:$key,parameters:({candidate:$candidate,patch:$patch,source_run_id:$source,batch:$batch[0],validation:{child_flow_run_id:$validation,state:"Completed"}} + $instructions[0])}')
    printf '%s\n' "$body" > "$scratch/request.json"
    api POST "/flows/$continuation_flow_id/execution-plan/runs" --data "@$scratch/request.json"
    ;;
  retry-publication)
    review_run=${2:?review run ID required}
    key=${3:?idempotency key required}
    state=$(api GET "/flow_runs/$review_run/execution-plan")
    jq -e 'any(.nodes[]; .node == "publish" and
      any(.outputs[]; .status == "selected" and
        (.output == "failed" or .output == "crashed" or .output == "cancelled")))' <<<"$state" >/dev/null
    review=$(jq -er '.nodes[]|select(.node=="review" and .status=="completed")|.activation_id' <<<"$state")
    approval=$(api GET "/flow_runs/$review_run/execution-plan/activations/$review/outputs/approved")
    body=$(jq -nc --arg source "$review_run" --arg key "$key" --argjson approval "$approval" \
      '{name:("publish-recovery-"+$source),idempotency_key:$key,
        parameters:($approval|del(.patch_sha256)|.recovery_review_run_id=$source)}')
    api POST /deployments/b74cd9ba-4596-41e8-84fc-6f0620bd8d4b/create_flow_run --data "$body"
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
    echo 'Usage: just agentic-fastmcp validate|deploy-validation|publish|publish-continuation|run ISSUE KEY [CONTEXT_FILE]|continue SOURCE_RUN VALIDATION_RUN KEY|retry-publication REVIEW_RUN KEY|results RUN_ID|verify' >&2
    exit 2
    ;;
esac
