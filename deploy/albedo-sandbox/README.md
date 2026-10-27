# albedo sandbox (heavypad)

[albedo](https://tangled.org/okami.mom/albedo) runs model-driven jobs on heavypad inside a bwrap sandbox. albedo's agents get an unrestricted Python kernel and shell, so the sandbox is what makes a job safe: the agent sees `/nix`, `/usr` and `/etc` read-only plus `~/albedo-sandbox/{home,work}`, and none of the real home (no sops keys, no `~/.config/prod-worker/env`, no other checkouts). Network is shared because the model is remote; staying off other hosts is an instruction in the prompt, not an enforced rule.

The first job is the weekly `albedo-drift-audit` deployment (`flows/albedo_drift_audit.py`). It compares heavypad's user units with `deploy/` and `docs/`, archives the report under `~/prefect-analytics/albedo-drift-audit/`, and hands it to phi's `drift-audit` slot.

## one-time setup

albedo is built from a checkout with nix and linked onto the worker's PATH:

```sh
git clone https://tangled.org/okami.mom/albedo ~/tangled.org/okami.mom/albedo
cd ~/tangled.org/okami.mom/albedo && nix build .#albedo
ln -sfn "$PWD/result/bin/albedo" ~/.local/bin/albedo
mkdir -p ~/albedo-sandbox/home/.albedo ~/albedo-sandbox/work
```

The provider profile is OpenRouter with `deepseek/deepseek-v4.1-flash`. From the laptop, the key goes from the secrets store into the sandbox's albedo config over ssh stdin; the daemon moves it into `creds.json` on its next start:

```sh
sops -d --extract '["openrouter"]["api_key"]' ~/tangled.org/zzstoatzz.io/secrets/prod.yaml \
  | ssh heavypad 'umask 077; jq -Rn --arg key "$(cat)" "{providers: {openrouter: {extension: \"openai\", baseUrl: \"https://openrouter.ai/api/v1\", apiKey: \$key, model: \"deepseek/deepseek-v4.1-flash\", protocol: \"responses\"}}, active: \"openrouter\"}" > ~/albedo-sandbox/home/.albedo/config.json'
```

`protocol` must be `responses`: albedo's chat-completions decoder rejects the second finish chunk OpenRouter sends (okami.mom/albedo issue "chat completions: openrouter's repeated finish chunk fails the turn").

The key is readable inside the sandbox, so a job's agent could read it. Rotating it means re-running the command above.

## running a job by hand

From a checkout of this repo on heavypad, put the job's inputs in `~/albedo-sandbox/work` (the agent's working directory), then:

```sh
deploy/albedo-sandbox/sandbox.sh albedo -p "..." --timeout 10m
deploy/albedo-sandbox/sandbox.sh albedo daemon --stop
```

Or run the deployment: `just prefect deployment run 'albedo-drift-audit/albedo-drift-audit' --watch`.
