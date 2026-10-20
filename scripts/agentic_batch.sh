#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PREFECT_API_URL='https://api.prefect.cloud/api/accounts/12242a57-9f05-4bf5-8853-9bff595d4bab/workspaces/cafa2ffa-f6cc-4ed6-ab76-eaa4ba1ad40e'
unset PREFECT_API_AUTH_STRING
api() { prefect --profile bot --no-prompt api "$@" </dev/null; }
mode=${1:?init or run required}
directory=${2:?absolute batch directory required}
[[ $directory == /* ]] || exit 2
if [[ $mode == init ]]; then
  [[ ! -e $directory ]] || { echo 'Batch already exists; resume it' >&2; exit 2; }
  shift 2
  (($# > 0 && $# <= 12)) || exit 2
  identifier=$(basename "$directory")
  [[ $identifier =~ ^[a-z0-9-]{1,50}$ ]] || exit 2
  mkdir -p "$directory"
  jq -n --arg id "$identifier" --args '$ARGS.positional|map(tonumber) as $issues |
    if ($issues|unique|length) != ($issues|length) or any($issues[]; . < 1 or floor != .)
    then error("Invalid queue") else
    {id:$id,started_at:(now|floor),expires_at:((now|floor)+7200),max_prs:3,issue_numbers:$issues} end' "$@" > "$directory/policy.json"
  chmod 444 "$directory/policy.json"
  jq -n --slurpfile policy "$directory/policy.json" '{policy:$policy[0],status:"ready",current:null,attempts:[],prs:[],consecutive_failures:0,input_tokens:0,output_tokens:0}' > "$directory/ledger.json"
  cat "$directory/policy.json"
  exit
fi
[[ $mode == run ]] || exit 2
ledger="$directory/ledger.json"
[[ -f $ledger && -f $directory/policy.json ]] || exit 2
if ! mkdir "$directory/lock" 2>/dev/null; then
  old_pid=$(cat "$directory/lock/pid" 2>/dev/null || true)
  [[ -n $old_pid ]] || { echo 'Batch lock owner is not yet recorded; refusing concurrent start' >&2; exit 2; }
  if [[ -n $old_pid ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo 'This batch is already running' >&2; exit 2
  fi
  rm -f "$directory/lock/pid"
  rmdir "$directory/lock"
  mkdir "$directory/lock"
fi
printf '%s\n' "$$" > "$directory/lock/pid"
trap 'rm -f "$directory/lock/pid"; rmdir "$directory/lock"' EXIT
update() { jq "$@" "$ledger" > "$directory/ledger.next"; mv "$directory/ledger.next" "$ledger"; }
stop() { update --arg reason "$1" '.status=$reason'; jq '{status,prs,attempts,input_tokens,output_tokens}' "$ledger"; }
cancel_current() {
  local run=$1 task_ids children child
  task_ids=$(api POST /task_runs/filter --data "$(jq -nc --arg run "$run" '{flow_runs:{id:{any_:[$run]}},limit:200}')" | jq '[.[].id]')
  if [[ $task_ids != '[]' ]]; then
    children=$(api POST /flow_runs/filter --data "$(jq -nc --argjson tasks "$task_ids" '{flow_runs:{parent_task_run_id:{any_:$tasks}},limit:200}')")
    while IFS= read -r child; do
      api POST "/flow_runs/$child/set_state" --data '{"state":{"type":"CANCELLING"}}' >/dev/null
    done < <(jq -r '.[]|select(.state_type|IN("SCHEDULED","PENDING","RUNNING","PAUSED"))|.id' <<<"$children")
  fi
  api POST "/flow_runs/$run/set_state" --data '{"state":{"type":"CANCELLING"}}' >/dev/null
}
while true; do
  reason=$(jq -r '
    if now >= .policy.expires_at then "deadline_reached"
    elif (.prs|length) >= .policy.max_prs then "target_reached"
    elif .consecutive_failures >= 3 then "failure_limit"
    elif .input_tokens >= 8000000 or .output_tokens >= 160000 then "token_limit"
    elif .current == null and (.attempts|length) >= (.policy.issue_numbers|length) then "queue_exhausted"
    else "continue" end' "$ledger")
  if [[ $reason != continue ]]; then
    run=$(jq -r '.current.run_id // empty' "$ledger")
    if [[ -n $run ]]; then cancel_current "$run"; fi
    stop "$reason"; exit
  fi
  if jq -e '.current == null' "$ledger" >/dev/null; then
    issue=$(jq -r '.policy.issue_numbers[(.attempts|length)]' "$ledger")
    key=$(jq -r --arg issue "$issue" '.policy.id+"-issue-"+$issue' "$ledger")
    update --argjson issue "$issue" --arg key "$key" '.current={issue:$issue,key:$key,run_id:null} | .status="running"'
  fi
  issue=$(jq -r '.current.issue' "$ledger")
  case_dir="$directory/issue-$issue"
  mkdir -p "$case_dir"
  run=$(jq -r '.current.run_id // empty' "$ledger")
  if [[ -z $run ]]; then
    key=$(jq -r '.current.key' "$ledger")
    if ! AGENTIC_BATCH_POLICY_FILE="$directory/policy.json" bash scripts/agentic_fastmcp.sh run "$issue" "$key" > "$case_dir/submission.json" 2> "$case_dir/setup.log"; then
      stop "submission_needs_attention"; exit 1
    fi
    run=$(jq -er '.id' "$case_dir/submission.json")
    update --arg run "$run" '.current.run_id=$run'
    printf 'Started #%s: %s\n' "$issue" "$run"
  fi
  api GET "/flow_runs/$run/execution-plan" > "$case_dir/graph.json"
  status=$(jq -r '.status' "$case_dir/graph.json")
  if [[ $status != completed && $status != failed ]]; then sleep 20; continue; fi
  bash scripts/agentic_fastmcp.sh results "$run" > "$case_dir/results.jsonl"
  printf '[]\n' > "$case_dir/logs.json"
  for offset in 0 200 400 600 800; do
    api POST /logs/filter --data "$(jq -nc --arg run "$run" --argjson offset "$offset" '{logs:{flow_run_id:{any_:[$run]}},limit:200,offset:$offset,sort:"TIMESTAMP_ASC"}')" > "$case_dir/log-page.json"
    jq -s '.[0]+.[1]' "$case_dir/logs.json" "$case_dir/log-page.json" > "$case_dir/logs.next"
    mv "$case_dir/logs.next" "$case_dir/logs.json"
    if (($(jq length "$case_dir/log-page.json") < 200)); then break; fi
    if ((offset == 800)); then stop "usage_inventory_incomplete"; exit 1; fi
  done
  branch=$(jq -r --arg issue "$issue" '"agentic/"+.policy.id+"/issue-"+$issue' "$ledger")
  gh pr list --repo PrefectHQ/fastmcp --head "$branch" --state all --json number,url,isDraft,headRefOid > "$case_dir/prs.json"
  jq -n --slurpfile graph "$case_dir/graph.json" --slurpfile results "$case_dir/results.jsonl" \
    --slurpfile logs "$case_dir/logs.json" --slurpfile prs "$case_dir/prs.json" --arg run "$run" --argjson issue "$issue" '
    [$logs[0][]|.message|select(contains("Model request completed"))|capture("input_tokens=(?<input>[0-9]+), output_tokens=(?<output>[0-9]+)")|{input:(.input|tonumber),output:(.output|tonumber)}] as $usage |
    {issue:$issue,run_id:$run,finished_at:(now|todateiso8601),
      failed:($graph[0].status=="failed" or any($graph[0].nodes[];.status=="failed" or any(.outputs[]?;.status=="selected" and (.output|IN("failed","crashed","cancelled"))))),
      outcomes:[$results[]|select(.node != null)|{node,output,value}],prs:$prs[0],
      input_tokens:([$usage[].input]|add//0),output_tokens:([$usage[].output]|add//0)}' > "$case_dir/outcome.json"
  update --slurpfile outcome "$case_dir/outcome.json" '
    .attempts += [$outcome[0]] | .prs = ((.prs + $outcome[0].prs)|unique_by(.number)) |
    .input_tokens += $outcome[0].input_tokens | .output_tokens += $outcome[0].output_tokens |
    .consecutive_failures = (if $outcome[0].failed then .consecutive_failures+1 else 0 end) | .current=null'
  jq '{issue,failed,prs,outcomes:[.outcomes[]|{node,output}]}' "$case_dir/outcome.json"
done
