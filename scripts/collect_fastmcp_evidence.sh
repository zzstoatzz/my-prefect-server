#!/usr/bin/env bash
set -euo pipefail
issue=${1:?issue number required}
[[ $issue =~ ^[1-9][0-9]*$ ]] || exit 2
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
repo=PrefectHQ/fastmcp
api() {
  local attempt
  for attempt in 1 2 3; do
    if gh api "$@" > "$scratch/response.json"; then
      cat "$scratch/response.json"
      return
    fi
    sleep "$attempt"
  done
  return 1
}
pages() {
  local endpoint=$1 page count
  printf '[]\n' > "$scratch/pages.json"
  for page in $(seq 1 10); do
    api "$endpoint?per_page=100&page=$page" > "$scratch/page.json"
    count=$(jq -er 'if type=="array" then length else error("Expected page") end' "$scratch/page.json")
    jq -s '.[0]+.[1]' "$scratch/pages.json" "$scratch/page.json" > "$scratch/next.json"
    mv "$scratch/next.json" "$scratch/pages.json"
    if ((count < 100)); then cat "$scratch/pages.json"; return; fi
  done
  echo 'Evidence pagination exceeded 1000 records; refusing incomplete intake' >&2
  return 1
}
api "repos/$repo/issues/$issue" | jq '{number,title,body,state,html_url,updated_at,user:{login:.user.login},assignees:[.assignees[].login],labels:[.labels[].name],pull_request}' > "$scratch/issue.json"
jq -e '.pull_request == null' "$scratch/issue.json" >/dev/null
pages "repos/$repo/issues/$issue/comments" | jq '[.[]|{id,body,html_url,created_at,updated_at,user:{login:.user.login}}]' > "$scratch/comments.json"
pages "repos/$repo/issues/$issue/timeline" | jq '[.[]|{event,created_at,source,actor:{login:.actor.login}}]' > "$scratch/timeline.json"
jq -rn --slurpfile issue "$scratch/issue.json" --arg number "$issue" '
  "repo:PrefectHQ/fastmcp is:pr " + $number + " in:body",
  ($issue[0].title | gsub("\\[Bug\\]|\\[bug\\]";"") |
    [scan("[A-Za-z_][A-Za-z0-9_]{3,}") | select((ascii_downcase | IN("fastmcp","with","when","from","that","does","doesn","have","this","into","after","before","without","using","support","fails","returns","error"))|not)] |
    unique | sort_by(-length) | .[:6] | map("\"" + . + "\"") | join(" ") |
    if length==0 then error("No useful PR search terms") else "repo:PrefectHQ/fastmcp is:pr " + . end)
' > "$scratch/queries"
printf '[]\n' > "$scratch/searches.json"
printf '[]\n' > "$scratch/matches.json"
while IFS= read -r query; do
  api -X GET search/issues -f q="$query" -f per_page=100 > "$scratch/search.json"
  jq -e '.incomplete_results == false and .total_count <= 100' "$scratch/search.json" >/dev/null || {
    echo "PR search incomplete: $query" >&2; exit 1;
  }
  jq --arg query "$query" --slurpfile result "$scratch/search.json" '. + [{query:$query,total_count:$result[0].total_count,complete:true}]' "$scratch/searches.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/searches.json"
  jq -s '.[0]+.[1].items | unique_by(.number)' "$scratch/matches.json" "$scratch/search.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/matches.json"
  if jq -e --arg number "$issue" 'any(.items[]; (.body // "") | test("(?i)(fix(es|ed)?|close[sd]?|resolve[sd]?)\\s+#"+$number+"(?![0-9])"))' "$scratch/search.json" >/dev/null; then
    break
  fi
done < "$scratch/queries"
jq -nr --slurpfile timeline "$scratch/timeline.json" '
  [$timeline[0][] | .source.issue | select(.pull_request and (.repository.full_name == "PrefectHQ/fastmcp" or (.html_url|startswith("https://github.com/PrefectHQ/fastmcp/pull/")))) | .number] | unique[]
' > "$scratch/numbers"
jq -r '.[].number' "$scratch/matches.json" >> "$scratch/numbers"
sort -nu "$scratch/numbers" > "$scratch/unique-numbers"
if (($(wc -l < "$scratch/unique-numbers") > 30)); then
  echo 'More than 30 PR candidates; bounded matching requires narrower investigation' >&2
  exit 1
fi
printf '[]\n' > "$scratch/prs.json"
while IFS= read -r number; do
  [[ -n $number ]] || continue
  api "repos/$repo/pulls/$number" | jq '{number,title,body,state,draft,merged,html_url,updated_at,user:{login:.user.login},base_sha:.base.sha,head_sha:.head.sha}' > "$scratch/pr.json"
  jq -s '.[0]+[.[1]]' "$scratch/prs.json" "$scratch/pr.json" > "$scratch/next.json"
  mv "$scratch/next.json" "$scratch/prs.json"
done < "$scratch/unique-numbers"
jq -n --slurpfile issue "$scratch/issue.json" --slurpfile comments "$scratch/comments.json" \
  --slurpfile timeline "$scratch/timeline.json" --slurpfile searches "$scratch/searches.json" --slurpfile prs "$scratch/prs.json" \
  '{schema_version:1,collected_at:(now|todateiso8601),complete:true,issue:$issue[0],comments:$comments[0],timeline:$timeline[0],searches:$searches[0],prs:$prs[0]}' > "$scratch/evidence.json"
if (($(wc -c < "$scratch/evidence.json") > 250000)); then
  echo 'Evidence exceeds the 250 KB intake bound' >&2
  exit 1
fi
cat "$scratch/evidence.json"
