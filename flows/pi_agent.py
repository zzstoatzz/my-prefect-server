"""run pi (the coding agent) non-interactively as a prefect flow.

first step toward alert-driven agents: hand pi a prompt, let it work,
capture what it says. later iterations point it at a checkout and wire
alert payloads into the prompt.
"""

import argparse
import shutil
import subprocess

from prefect import flow


@flow(name="pi-agent", log_prints=True, timeout_seconds=1800)
def pi_agent(
    prompt: str,
    provider: str | None = None,
    model: str | None = None,
    cwd: str | None = None,
    extra_args: list[str] | None = None,
    timeout_seconds: int = 1500,
) -> str:
    """run `pi -p <prompt>` and return its final output.

    pi resolves credentials from the worker's ~/.pi/agent/auth.json or
    provider env vars (e.g. ANTHROPIC_API_KEY), so the worker box must be
    authed once out-of-band before this flow can do anything.
    """
    if shutil.which("pi") is None:
        raise RuntimeError(
            "pi is not installed on this worker — "
            "npm install -g @earendil-works/pi-coding-agent"
        )

    cmd = ["pi", "--print", "--no-session"]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    if extra_args:
        cmd += extra_args
    cmd.append(prompt)

    print(f"running: {' '.join(cmd[:-1])} <prompt: {len(prompt)} chars>")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout_seconds,
    )
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"pi exited {result.returncode}: {result.stderr[-2000:]}")
    return result.stdout


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--cwd")
    args = parser.parse_args()
    pi_agent(args.prompt, provider=args.provider, model=args.model, cwd=args.cwd)
