#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""Check this repository's deployment contracts without importing or running flows."""

import argparse
import ast
import os
import re
import shlex
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PIN_TEMPLATE = "{{ $MPS_PIN }}"
GIT_PACKAGE = re.compile(
    r"my-prefect-server(?:\[[\w,.-]+\])? @ "
    r"git\+https://github.com/zzstoatzz/my-prefect-server\.git(.*)"
)
MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
MPS_PACKAGE = re.compile(r"(?:mps|my-prefect-server)(?:\[[\w,.-]+\])? @ .+")


def _load_time_imports(tree: ast.Module) -> list[str]:
    """Modules imported while the file loads: function bodies and TYPE_CHECKING are skipped."""
    found: list[str] = []
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            pending.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
        pending.extend(ast.iter_child_nodes(node))
    return found


def mps_import_chain(root: Path, source: str) -> list[str] | None:
    """Return the checkout files leading from `source` to a load-time mps import."""
    parents: dict[str, str | None] = {source: None}
    queue = [source]
    while queue:
        current = queue.pop(0)
        path = root / current
        if not path.is_file():
            continue
        for module in _load_time_imports(ast.parse(path.read_text(), filename=current)):
            if module == "mps" or module.startswith("mps."):
                chain = [current]
                while (parent := parents[chain[-1]]) is not None:
                    chain.append(parent)
                return [*reversed(chain), module]
            if module.split(".")[0] != "flows":
                continue
            relative = module.replace(".", "/")
            for candidate in (f"{relative}.py", f"{relative}/__init__.py"):
                if candidate not in parents and (root / candidate).is_file():
                    parents[candidate] = current
                    queue.append(candidate)
    return None


def validate_deployments(config: dict, *, root: Path, release_pin: str | None = None):
    """Return (errors, unverified). YAML anchors are expanded by safe_load.

    An explicit deployment pull (including []) replaces the top-level pull.
    Work-pool job variables are already resolved by YAML anchors; they do not
    implicitly inherit the similarly named entry in definitions.work_pools.
    """
    errors: list[str] = []
    unverified: list[str] = []
    if not isinstance(config, dict) or not isinstance(config.get("deployments"), list):
        return ["config: deployments must be a list"], []
    if release_pin is not None and not re.fullmatch(r"@[0-9a-f]{40}", release_pin):
        errors.append("release: MPS_PIN must be @ followed by a full 40-character commit SHA")
    seen = set()
    for dep in config["deployments"]:
        if not isinstance(dep, dict) or not isinstance(dep.get("name"), str):
            errors.append("config: each deployment needs a name")
            continue
        name = dep["name"]
        if name in seen:
            errors.append(f"{name}: duplicate deployment name")
        seen.add(name)
        entry = dep.get("entrypoint")
        if not isinstance(entry, str) or not entry:
            errors.append(f"{name}: entrypoint must be a nonempty string")
            continue
        pull = dep.get("pull", config.get("pull"))
        if pull is not None and not isinstance(pull, list):
            errors.append(f"{name}: pull must be a list or null")
            continue
        pool = dep.get("work_pool") or {}
        if not isinstance(pool, dict) or not isinstance(pool.get("job_variables", {}), dict):
            errors.append(f"{name}: work_pool and job_variables must be mappings")
            continue
        job = pool.get("job_variables", {})
        env = job.get("env") or {}
        packages = job.get("local_packages") or []
        command = job.get("command") or ""
        if (
            not isinstance(env, dict)
            or not isinstance(packages, list)
            or not all(isinstance(p, str) for p in packages)
            or not isinstance(command, str)
        ):
            errors.append(f"{name}: invalid env, local_packages, or command shape")
            continue
        try:
            args = shlex.split(command)
        except ValueError:
            errors.append(f"{name}: command has invalid quoting")
            continue
        requirements = list(packages)
        for i, arg in enumerate(args):
            if arg == "--with":
                if i + 1 == len(args):
                    errors.append(f"{name}: --with needs a package")
                else:
                    requirements.append(args[i + 1])
        wheels = [p for p in requirements if p.endswith(".whl")]
        git_packages = [m for p in requirements if (m := GIT_PACKAGE.fullmatch(p))]
        mps_wheels = [p for p in wheels if Path(p).name.startswith("mps-")]
        supplied_files = job.get("image") or job.get("working_dir") or dep.get("path")
        wheel_only = (
            bool(mps_wheels or (wheels and entry.startswith("flows.")))
            and not pull
            and not supplied_files
        )
        if job.get("environment_mode") == "image":
            image = job.get("image")
            if not isinstance(image, str) or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
                errors.append(f"{name}: image mode requires an immutable image digest")
            if job.get("requirements") != [] or job.get("local_packages") != []:
                errors.append(
                    f"{name}: image mode requires explicit empty requirements/local_packages"
                )
            if pull or not MODULE.fullmatch(entry):
                errors.append(
                    f"{name}: image mode requires pull: [] and a dotted module entrypoint"
                )
            unverified.append(
                f"{name}: image availability, installed module bytes and runtime imports"
            )
        elif wheel_only:
            if not MODULE.fullmatch(entry):
                errors.append(f"{name}: wheel-only deployment requires a dotted module entrypoint")
            else:
                module_path = entry.rsplit(".", 1)[0].replace(".", "/") + ".py"
                manifest = root / "packages/mps/pyproject.toml"
                includes = tomllib.loads(manifest.read_text())["tool"]["hatch"]["build"]["targets"][
                    "wheel"
                ]["force-include"]
                if module_path not in includes.values():
                    errors.append(f"{name}: entrypoint module is not declared in the mps wheel")
                if not mps_wheels:
                    errors.append(f"{name}: flow module requires the mps wheel")
            unverified.append(f"{name}: wheel contents, availability and runtime imports")
        elif ":" in entry:
            source, function = entry.rsplit(":", 1)
            if not source.endswith(".py") or not function.isidentifier():
                errors.append(f"{name}: invalid file entrypoint")
            elif pull and git_packages and not (root / source).is_file():
                errors.append(f"{name}: source entrypoint file is missing from this checkout")
            elif (
                pull
                and not any(MPS_PACKAGE.fullmatch(r) for r in requirements)
                and not mps_wheels
                and (chain := mps_import_chain(root, source))
            ):
                errors.append(
                    f"{name}: loading the flow imports {chain[-1]} via "
                    f"{' -> '.join(chain[:-1])}, but the command does not install mps"
                )
            if not pull:
                unverified.append(f"{name}: file must be supplied by worker path or image")
        elif not MODULE.fullmatch(entry):
            errors.append(f"{name}: invalid module entrypoint")
        else:
            unverified.append(f"{name}: module installation is not a recognized wheel route")
        if git_packages:
            if len(git_packages) != 1:
                errors.append(f"{name}: expected one my-prefect-server package requirement")
            for package in git_packages:
                pin = package.group(1)
                if pin != PIN_TEMPLATE and not re.fullmatch(r"@[0-9a-f]{40}", pin):
                    errors.append(
                        f"{name}: package must use MPS_PIN or an explicit full commit SHA"
                    )
                if pull and env.get("MPS_PIN") != pin:
                    errors.append(f"{name}: job env.MPS_PIN must match the package revision")
        elif pull:
            unverified.append(f"{name}: source/package binding is not a recognized Git route")
    if any(
        dep.get("pull", config.get("pull"))
        for dep in config["deployments"]
        if isinstance(dep, dict)
    ):
        unverified.append("pull steps: arbitrary scripts and remote revisions are not executed")
    return errors, unverified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "prefect.yaml")
    parser.add_argument("--release", action="store_true", help="require the CI/manual MPS_PIN")
    args = parser.parse_args()
    try:
        config = yaml.safe_load(args.config.read_text())
        errors, unverified = validate_deployments(
            config,
            root=ROOT,
            release_pin=os.environ.get("MPS_PIN", "") if args.release else None,
        )
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        # Do not dump config or parser excerpts: job variables can contain secrets.
        print(
            f"INVALID: cannot read deployment configuration ({type(exc).__name__})", file=sys.stderr
        )
        return 1
    for error in errors:
        print(f"INVALID: {error}", file=sys.stderr)
    for warning in unverified:
        print(f"UNVERIFIED: {warning}")
    print(f"Static deployment contracts: {len(errors)} error(s); runtime readiness not tested.")
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
