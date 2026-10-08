# Gardener private images

`gardener-registry.exe.xyz` is an ordinary persistent exe.dev VM tagged
`gardener-infra`. It is separate from disposable `prefect-*` job VMs. Its HTTPS
proxy stays private, with no shares. exe.dev permits same-account boot-image
pulls through that proxy; the worker's existing SSH identity supplies account
ownership. There is no registry password to distribute to jobs or Prefect.

The registry container uses
`registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373`,
port `8000:5000`, restart policy `always`, and the bind mount
`/var/lib/gardener-registry:/var/lib/registry`. Docker is enabled at VM boot.
Keep the private proxy enabled; do not publish port 8000 through a public share.

The original installation commands, run on the registry VM, are:

```sh
sudo mkdir -p /var/lib/gardener-registry
sudo systemctl enable --now docker
sudo docker run -d --name gardener-registry --restart=always \
  -p 8000:5000 -v /var/lib/gardener-registry:/var/lib/registry \
  registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373
```

## Releases

`images.json` records immutable image digests and original wheel hashes. Each
image installs its deployment's exact wheel, locks dependencies at build time,
verifies installed source bytes against that wheel, and prepares agent tools.
The Prefect runtime remains pinned to the bootstrap worker's `3.7.7` requirement.

```sh
just gardener-wheel-image WHEEL WHEEL_SHA BASE_IMAGE LOCAL_IMAGE BUILD_CONTEXT
just gardener-publish LOCAL_IMAGE RELEASE_TAG
```

Publishing uses pinned-host-key SSH to load and push on the registry VM. Tags
are convenient labels; deployments reference digests. Retain existing images
until no deployment or rollback uses them. Never delete the registry VM as part
of job cleanup.

Saved build contexts (wheel, `pyproject.toml`, `uv.lock`, verification script)
are on heavypad under `/home/stoat/gardener-images/releases/DEPLOYMENT`. They
provide a second copy of the wheel and dependency lock independently of the
registry disk. Image layers remain cached in the operator's Docker store too.
On the registry VM, the base is available as `localhost:8000/gardener@DIGEST`
using the digest in `images.json`. Local builds can use the base already loaded
in Docker; ordinary Docker clients do not inherit exe.dev's host-side pull
authentication. Rebuild using the saved lock; the build script preserves an
existing compatible lock. This is recoverable storage, not
a highly available registry. The account needs an active exe.dev subscription.

## Rollback

Before switching a deployment, save its full live API object privately.
The rollout snapshots live on heavypad in
`/home/stoat/gardener-images/rollbacks/DEPLOYMENT-before.json` (mode 0600).

To revert one deployment, retrieve that snapshot through SSH, extract only
`job_variables` with `jq '{job_variables}'`, and pass the result as a file to
`just prefect api PATCH /deployments/DEPLOYMENT_ID --data @FILE`.
Also restore that deployment's `prefect.yaml` job variables to its previous
wheel path, omitting image mode and image, so later registration agrees.
Already-running jobs retain their submitted configuration.

The original wheels remain at
`/home/stoat/phi-spike-worker/releases/WHEEL_SHA/mps-0.1.0-py3-none-any.whl`.
The worker stays on heavypad throughout this rollout.
