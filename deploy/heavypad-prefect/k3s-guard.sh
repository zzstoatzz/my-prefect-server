#!/usr/bin/env bash
# k3s inside the k3d server can exit at boot ("failed to find interface with
# specified node ip") while the container keeps running, so Docker's restart
# policy never fires. Wait for readiness; restart the container at most twice.
set -euo pipefail
export DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
container="${PREFECT_HOME_K3D_SERVER:-k3d-prefect-home-server-0}"
wait_seconds="${PREFECT_HOME_K3S_WAIT:-120}"
poll_seconds="${PREFECT_HOME_K3S_POLL:-5}"
ready() {
  local deadline=$((SECONDS + wait_seconds))
  until docker exec "$container" kubectl get --raw /readyz >/dev/null 2>&1; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep "$poll_seconds"
  done
}
for restarts in 0 1 2; do
  if ready; then
    printf 'k3s ready after %s restart(s)\n' "$restarts"
    exit 0
  fi
  [ "$restarts" -lt 2 ] || break
  printf 'k3s not ready after %ss; restarting %s\n' "$wait_seconds" "$container" >&2
  docker restart "$container" >/dev/null
done
printf 'k3s still not ready after two restarts\n' >&2
exit 1
