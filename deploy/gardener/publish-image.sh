#!/usr/bin/env bash
set -euo pipefail
image=$1
tag=$2
[[ "$image" =~ ^[a-zA-Z0-9][a-zA-Z0-9./:@_-]*$ ]]
[[ "$tag" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]
known_hosts=$(mktemp)
trap 'unlink "$known_hosts"' EXIT
uv run python -c 'from prefect_exe.client import HOST_KEY; print("exe.dev,*.exe.xyz " + HOST_KEY)' > "$known_hosts"
remote=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$known_hosts" gardener-registry.exe.xyz)
docker save "$image" | gzip -1 | "${remote[@]}" 'gunzip | sudo docker load'
"${remote[@]}" "sudo docker tag '$image' 'localhost:8000/gardener:$tag' && sudo docker push 'localhost:8000/gardener:$tag'"
