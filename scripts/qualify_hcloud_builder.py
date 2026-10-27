"""Create a real cloud VM, exercise supervisor failure paths, and verify cleanup."""

import argparse
import asyncio
import json
import os
import shlex
import tempfile
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from mps.hcloud_builder import resource_name
from mps.hcloud_worker import BuilderClient, BuilderConfiguration

PROBE = r"""
import json, pathlib, signal, subprocess, sys, tempfile, time
from importlib.util import spec_from_file_location, module_from_spec
payload=json.load(sys.stdin)
root=pathlib.Path(tempfile.mkdtemp(prefix='builder-qualification-'))
runtime=root/'runtime.py'
runtime.write_text(payload['runtime'])
spec=spec_from_file_location('runtime',runtime)
module=module_from_spec(spec)
spec.loader.exec_module(module)
outcomes={}
for name,code,timeout,expected in [
    ('success',"from pathlib import Path; Path('executed').write_text('once')",30,0),
    ('failure','raise SystemExit(37)',30,37),
    ('timeout','import time; time.sleep(120)',0.2,124),
    ('cancel','import time; time.sleep(120)',180,143),
]:
    case=root/name
    case.mkdir()
    (case/'runtime.py').write_text(payload['runtime'])
    (case/'config.json').write_text(json.dumps({
        'flow_run_id':payload['run_id'],
        'env':{},'argv':[sys.executable,'-c',code],
        'cwd':str(case),'timeout_seconds':timeout,
        'cgroup':'/sys/fs/cgroup/prefect-qualification-'+name,
    }))
    child=subprocess.Popen([sys.executable,str(case/'runtime.py')])
    if name=='cancel':
        deadline=time.monotonic()+10
        while not (case/'state.json').exists():
            assert child.poll() is None and time.monotonic()<deadline
            time.sleep(.05)
        time.sleep(.2)
        child.send_signal(signal.SIGTERM)
    child.wait(timeout=30)
    state=json.loads((case/'state.json').read_text())
    assert state['phase']=='exited',state
    assert state['exit_code']==expected,state
    assert not (case/'config.json').exists()
    before=(case/'state.json').read_bytes()
    module.execute(case)
    assert (case/'state.json').read_bytes()==before
    if name=='success':
        assert (case/'executed').read_text()=='once'
    outcomes[name]={'exit_code':state['exit_code'],'restart_did_not_replay':True}
import shutil
assert shutil.disk_usage('/var/lib/typeahead-indexer').free >= 104*2**30
assert pathlib.Path('/root/.local/opt/zig-x86_64-linux-0.17.0/zig').exists()
print(json.dumps({'outcomes':outcomes,'scratch_free_gib':round(shutil.disk_usage('/').free/2**30,2)}))
"""


async def main(output: Path):
    os.umask(0o077)
    if not os.environ.get("HCLOUD_TOKEN"):
        os.environ["HCLOUD_TOKEN"] = dotenv_values(".env")["HCLOUD_TOKEN"]
    identity = uuid4()
    name = resource_name(identity, 1)
    result = {"run_id": str(identity), "name": name}
    with tempfile.TemporaryDirectory(prefix="builder-qualification-") as directory:
        async with BuilderClient(
            Path(directory), "qualification", BuilderConfiguration(timeout_seconds=1800)
        ) as client:
            try:
                vm = await client.create(name, tags=["qualification", "flow-run-" + identity.hex])
                print("Cloud bootstrap ready: " + name, flush=True)
                result.update(
                    json.loads(
                        await client.run(
                            vm,
                            "python3 -c " + shlex.quote(PROBE),
                            stdin=json.dumps(
                                {
                                    "run_id": str(identity),
                                    "runtime": files("prefect_exe")
                                    .joinpath("runtime.py")
                                    .read_text(),
                                }
                            ).encode(),
                            timeout=150,
                        )
                    )
                )
            finally:
                receipt = Path(directory) / name / "resource.json"
                server = json.loads(receipt.read_text())["server"] if receipt.exists() else None
                await client.destroy(name)
                if server:
                    await client.hcloud.delete(server["id"], identity, receipt=server)
                    assert await client.hcloud.request("GET", f"servers/{server['id']}") is None
                    keys = await client.hcloud.request(
                        "GET", "ssh_keys", params={"name": name + "-key"}
                    )
                    assert keys is not None and not keys["ssh_keys"]
                    ip_id = server["public_net"]["ipv4"]["id"]
                    assert await client.hcloud.request("GET", f"primary_ips/{ip_id}") is None
                result["cleanup_verified"] = server is not None
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args().output))
