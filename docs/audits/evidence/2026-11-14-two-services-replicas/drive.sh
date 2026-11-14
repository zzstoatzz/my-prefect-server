#!/usr/bin/env bash
set -u
L=$HOME/.local/state/lab-two-services
SRC=$HOME/.local/share/prefect-chaos/source/scripts/chaos
export CHAOS_SERVER_IMAGE=atcr.io/zzstoatzz.io/prefect-server:8c775d4f23edc25d3ea220eac5e98a1fdc8e1855
dc() { docker --context default compose -p prefect-lab2 -f $SRC/compose.yaml -f $L/override.yaml "$@"; }
run() { # label profile-args kill
  label=$1; prof=$2; kill=$3
  dc --profile two down -v --remove-orphans >/dev/null 2>&1
  if ! dc $prof up -d --wait --wait-timeout 90 --pull never >$L/$label.up.log 2>&1; then echo "$label UP-FAILED"; tail -5 $L/$label.up.log; dc --profile two logs --tail 20 2>&1 | tail -30; return; fi
  port=$(dc port gateway 4200 | cut -d: -f2)
  args=(--api "http://127.0.0.1:$port/api")
  [ -n "$kill" ] && args+=(--kill-cmd "$kill")
  echo "$label $(env -u UV_PYTHON $HOME/.local/bin/uv run -q --script $L/probe.py "${args[@]}" 2>$L/$label.err | tail -1)"
  [ -s $L/$label.err ] && tail -3 $L/$label.err
  echo "$label docket: $(dc exec -T redis redis-cli XINFO GROUPS prefect:stream 2>/dev/null | tr '\n' ' ')"
  echo "$label services containers: $(dc --profile two ps -a --format '{{.Service}}={{.State}}' | grep services | tr '\n' ' ')"
  dc --profile two logs services services-b 2>/dev/null | grep -ciE "error|panic" | sed "s/^/$label services error lines: /"
}
run one "" ""
run two "--profile two" ""
run two-kill "--profile two" "docker --context default kill prefect-lab2-services-b-1"
run two-kill-a "--profile two" "docker --context default kill prefect-lab2-services-1"
dc --profile two down -v --remove-orphans >/dev/null 2>&1
echo "teardown: containers=$(docker --context default ps -a --format '{{.Names}}' | grep -c prefect-lab2) volumes=$(docker --context default volume ls -q | grep -c prefect-lab2) networks=$(docker --context default network ls --format '{{.Name}}' | grep -c prefect-lab2)"
