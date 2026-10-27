"""Encrypt the old control-plane history on its host and verify a direct R2 copy."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path

REMOTE = r"""
import datetime,hashlib,json,os,pathlib,shutil,sqlite3,subprocess,sys,tarfile,tempfile
os.umask(0o077)
credentials=json.load(sys.stdin)
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
root=pathlib.Path('/var/lib/prefect-retirement')
root.mkdir(mode=0o700,exist_ok=True)
archive=root/(stamp+'.tar.gz.age')
def save(command,path):
    with path.open('wb') as out:subprocess.run(command,stdout=out,check=True)
with tempfile.TemporaryDirectory(prefix='snapshot-',dir=root) as directory:
    snapshot=pathlib.Path(directory)
    print('Creating PostgreSQL history dump',flush=True)
    save(['kubectl','-n','prefect','exec','deployment/prefect-postgres','--',
          'pg_dump','-U','prefect','-d','prefect','-Fc'],snapshot/'postgres.dump')
    result=subprocess.run(['pg_restore','--list',str(snapshot/'postgres.dump')],
                          stdout=subprocess.PIPE,check=True)
    (snapshot/'postgres.contents').write_bytes(result.stdout)
    for namespace in ['prefect','monitoring','cert-manager']:
        save(['kubectl','-n',namespace,'get',
          'deployment,statefulset,service,ingress,secret,configmap,pvc','-o','yaml'],
          snapshot/(namespace+'.yaml'))
    save(['kubectl','get','clusterissuer','-o','yaml'],snapshot/'issuers.yaml')
    save(['kubectl','get','ingressroute','-A','-o','yaml'],snapshot/'ingressroutes.yaml')
    storage=pathlib.Path('/var/lib/rancher/k3s/storage')
    push=next(storage.glob('*_prefect_prefect-push-data'))/'prefect-push.db'
    with sqlite3.connect('file:'+str(push)+'?mode=ro',uri=True) as source:
        with sqlite3.connect(snapshot/'prefect-push.db') as target:source.backup(target)
    subprocess.run(['kubectl','-n','prefect','exec','deployment/prefect-redis-durable',
                    '--','redis-cli','SAVE'],check=True,stdout=subprocess.DEVNULL)
    redis=next(storage.glob('*_prefect_prefect-redis-data'))/'dump.rdb'
    shutil.copy2(redis,snapshot/'redis.rdb')
    results=next(storage.glob('*_prefect_prefect-results'))
    with tarfile.open(snapshot/'results.tar.gz','w:gz') as tar:
        tar.add(results,arcname='results')
    files={p.name:p.stat().st_size for p in snapshot.iterdir()}
    (snapshot/'manifest.json').write_text(json.dumps({'created':stamp,'files':files},indent=2))
    recipients=['age195mg0pvtljlk2k66w4ng48y0ywxq6550mr6u574ad83k63ernc5qmgy8qw',
                'age15dxn35sc73nw9cz75sf8wkm48zznexd6p0va80vn27jzu0m93ylsncse65']
    command=['age']
    for recipient in recipients:command+=['-r',recipient]
    with archive.open('wb') as out:
        encrypt=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out)
        with tarfile.open(fileobj=encrypt.stdin,mode='w|gz') as tar:
            tar.add(snapshot,arcname='control-plane')
        encrypt.stdin.close()
        if encrypt.wait():raise RuntimeError('Archive encryption failed')
env={**os.environ,'RCLONE_CONFIG_RETIREMENT_TYPE':'s3',
     'RCLONE_CONFIG_RETIREMENT_PROVIDER':'Cloudflare',
     'RCLONE_CONFIG_RETIREMENT_ENDPOINT':credentials['r2_backup_endpoint'],
     'RCLONE_CONFIG_RETIREMENT_ACCESS_KEY_ID':credentials['r2_backup_access_key_id'],
     'RCLONE_CONFIG_RETIREMENT_SECRET_ACCESS_KEY':credentials['r2_backup_secret_access_key']}
key='retirement/hetzner-prefect/'+stamp+'.tar.gz.age'
destination='retirement:'+credentials['r2_backup_bucket']+'/'+key
subprocess.run(['rclone','copyto',str(archive),destination,'--immutable',
                '--s3-no-check-bucket'],env=env,check=True)
def digest(stream):
    value=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()
with archive.open('rb') as source:expected=digest(source)
download=subprocess.Popen(['rclone','cat',destination],env=env,stdout=subprocess.PIPE)
actual=digest(download.stdout)
if download.wait() or actual!=expected:raise RuntimeError('Offsite archive hash mismatch')
print(json.dumps({'created':stamp,'object_key':key,'sha256':expected,
 'encrypted_bytes':archive.stat().st_size,'files':files,'offsite_hash_verified':True,
 'full_restore_verified':False}),flush=True)
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    credentials = subprocess.check_output(
        [
            "sops",
            "decrypt",
            "--output-type",
            "json",
            "--extract",
            '["heavypad_prefect"]',
            str(Path.home() / "tangled.org/zzstoatzz.io/secrets/prod.yaml"),
        ],
        text=True,
    )
    data = {k: v for k, v in json.loads(credentials).items() if k.startswith("r2_backup_")}
    result = subprocess.check_output(
        ["ssh", "-o", "BatchMode=yes", args.host, "python3 -c " + shlex.quote(REMOTE)],
        input=json.dumps(data),
        text=True,
    )
    receipt = json.loads(result.splitlines()[-1])
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
