#!/usr/bin/env bash
set -euo pipefail
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

names='["autofix","autofix-revise","investigate","merge-approved","phi-pull-review","pi-agent","test-pull-patch","watch-tangled-pulls"]'
for target in prefect-legacy prefect; do
  just "$target" api POST /deployments/filter --data '{"limit":500}' >"$work/$target-deployments.json"
  jq -e --argjson names "$names" '
    [.[] | select(.name as $n | $names | index($n))] |
    length == 8 and all(.[]; all(.schedules[]; .active == false))
  ' "$work/$target-deployments.json" >/dev/null
  ids=$(jq -c --argjson names "$names" '[.[] | select(.name as $n | $names | index($n)) | .id]' "$work/$target-deployments.json")
  just "$target" api POST /flow_runs/filter --data '{"flow_runs":{"state":{"type":{"any_":["SCHEDULED","PENDING","RUNNING","CANCELLING","PAUSED"]}}},"limit":500}' >"$work/runs.json"
  jq -e --argjson ids "$ids" 'length < 500 and ([.[] | select(.deployment_id as $id | $ids | index($id))] | length == 0)' "$work/runs.json" >/dev/null
done
just prefect-legacy api POST /automations/filter --data '{}' >"$work/automations.json"
jq -e '[.[] | select(.name == "flow-run failure -> autofix" or .name == "autofix proposed -> merge-approved" or .name == "autofix proposed -> phi review" or .name == "autofix revise requested -> autofix-revise")] | length == 4 and all(.[]; .enabled == false)' "$work/automations.json" >/dev/null

since=$(uv run python -c 'from datetime import datetime,UTC,timedelta; print((datetime.now(UTC)-timedelta(days=30)).isoformat())')
query=$(jq -cn --arg since "$since" '{filter:{event:{prefix:["autofix.","merge."]},occurred:{since:$since}},limit:1000}')
just prefect-legacy api POST /events/filter --data "$query" >"$work/events.json"
jq -e '.total == (.events|length) and (.events|map(.id)|unique|length) == .total' "$work/events.json" >/dev/null
just prefect-legacy api POST /variables/filter --data '{}' >"$work/variables.json"
jq '[.[] | select(.name == "autofix_handled_comments") | {name,value,tags}]' "$work/variables.json" >"$work/selected.json"
jq -e 'length == 1' "$work/selected.json" >/dev/null
if [ "${1:-}" != --apply ]; then
  jq '"Ready to transfer \(.total) retained events and the handled-comment checkpoint; no writes"' "$work/events.json"
  exit 0
fi

jq -r '.events[] | [.id,.occurred,.event,.resource["prefect.resource.id"],(.resource|tojson),([.related[]?["prefect.resource.id"]]|tojson),(.related|tojson),(.payload|tojson),.received,.received,.follows] | @csv' "$work/events.json" >"$work/events.csv"
{
  printf '%s\n' 'BEGIN; CREATE TEMP TABLE migration_events (LIKE events INCLUDING DEFAULTS) ON COMMIT DROP;'
  printf '%s\n' 'COPY migration_events (id,occurred,event,resource_id,resource,related_resource_ids,related,payload,received,recorded,follows) FROM STDIN WITH (FORMAT csv);'
  cat "$work/events.csv"
  printf '%s\n' '\.'
  cat <<'SQL'
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM migration_events m JOIN events e USING(id)
    WHERE (m.occurred,m.event,m.resource,m.related,m.payload)
      IS DISTINCT FROM (e.occurred,e.event,e.resource,e.related,e.payload)) THEN
    RAISE EXCEPTION 'Existing event ID has different content';
  END IF;
END $$;
INSERT INTO events SELECT * FROM migration_events ON CONFLICT (id) DO NOTHING;
COMMIT;
SQL
} | just kubectl -n prefect-home exec -i deployment/postgres -- psql -U prefect -d prefect -Xq -v ON_ERROR_STOP=1

just prefect api POST /variables/filter --data '{}' >"$work/target-variables.json"
while IFS= read -r body; do
  name=$(jq -r .name <<<"$body")
  if jq -e --arg name "$name" 'any(.[]; .name == $name)' "$work/target-variables.json" >/dev/null; then
    just prefect api PATCH "/variables/name/$name" --data "$(jq -c 'del(.name)' <<<"$body")" >/dev/null
  else
    just prefect api POST /variables/ --data "$body" >/dev/null
  fi
done < <(jq -c '.[]' "$work/selected.json")
just prefect api POST /variables/filter --data '{}' >"$work/target-variables.json"
jq -e --slurpfile source "$work/selected.json" '([.[] | select(.name == "autofix_handled_comments") | {name,value,tags}] | sort_by(.name)) == ($source[0] | sort_by(.name))' "$work/target-variables.json" >/dev/null
just prefect api POST /events/filter --data "$query" >"$work/target-events.json"
jq -e --slurpfile source "$work/events.json" '([.events[]|{id,event,occurred,resource,related,payload}]|sort_by(.id)) == ([$source[0].events[]|{id,event,occurred,resource,related,payload}]|sort_by(.id))' "$work/target-events.json" >/dev/null
jq '"Verified \(.total) retained events and the handled-comment checkpoint; historical events were not replayed to automations"' "$work/events.json"
