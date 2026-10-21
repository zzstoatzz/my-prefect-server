#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PREFECT_API_URL='https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e'
unset PREFECT_API_AUTH_STRING
api() { prefect --profile bot --no-prompt api "$@" </dev/null; }

case "${1:-}" in
  validate)
    for role in assess reproduce prepare review; do
      api POST /execution-plans/validate --data "@deploy/agentic-workflows/fastmcp-stack-$role.plan.json" | jq -e '.valid == true'
    done
    ;;
  register)
    git diff --quiet
    git diff --cached --quiet
    git fetch github main
    revision=$(git rev-parse HEAD)
    git merge-base --is-ancestor "$revision" refs/remotes/github/main || {
      echo 'Controller source must land on GitHub main before registration' >&2; exit 1;
    }
    MPS_AGENTIC_REF=$revision prefect --profile bot --no-prompt deploy \
      --prefect-file deploy/agentic-workflows/prefect.yaml \
      --name cloud-stack-batch --name cloud-stack-validation
    ;;
  run)
    batch=${2:?batch JSON file required}
    runtime=${3:?pinned runtime JSON file required}
    deployment=${4:?managed stack controller deployment UUID required}
    [[ $deployment =~ ^[0-9a-f-]{36}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    jq -e '.id | test("^[a-z0-9-]{1,40}$")' "$batch" >/dev/null
    jq -n --slurpfile batch "$batch" --slurpfile runtime "$runtime" \
      '{name:("stack-"+$batch[0].id),idempotency_key:("fastmcp-stack-"+$batch[0].id),parameters:{batch:$batch[0],runtime:$runtime[0]}}' \
      > "$scratch/request.json"
    api POST "/deployments/$deployment/create_flow_run" --data "@$scratch/request.json"
    ;;
  results)
    run=${2:?controller run UUID required}
    [[ $run =~ ^[0-9a-f-]{36}$ ]] || exit 2
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    api GET "/flow_runs/$run" > "$scratch/run.json"
    jq '{id,state_type,state_name,parameters:{batch:.parameters.batch}}' "$scratch/run.json"
    key=$(jq -er '.parameters.batch.id + "-review-bins"' "$scratch/run.json")
    jq -n --arg run "$run" --arg key "$key" \
      '{artifacts:{key:{any_:[$key]}},flow_runs:{id:{any_:[$run]}},limit:2}' > "$scratch/filter.json"
    api POST /artifacts/filter --data "@$scratch/filter.json" | jq -e 'if length==1 then .[0].data else error("Final review bins not available; inspect the run and retained stage artifacts") end'
    ;;
  *)
    echo 'Usage: agentic_stack.sh validate | register | run BATCH RUNTIME DEPLOYMENT | results RUN' >&2
    exit 2
    ;;
esac
