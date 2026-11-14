# atcr pushes rejected after the PDS move — November 14, 2026

The UI bundle `prefect-ui:b498352` (ui-v2-fork b498352, the scatter plot window
fix) runs in production, but the atcr registry does not hold that tag yet. The
image was built on HeavyPad and imported into `k3d-prefect-home-server-0`
directly. Until the registry push succeeds, a rebuilt cluster or a pruned node
cannot pull the UI image.

## What happened

nate's PDS moved from `pds.zzstoatzz.io` to `zds.zzstoatzz.io` at 03:08Z (PLC
audit log). This had two effects on atcr:

1. App passwords stay on the PDS that made them, so the `ATCR_APP_PASSWORD`
   spindle secret and the `atcr-creds` pull secret stopped working, and
   `docker login` answered `unauthorized`. A new `atcr` app password was made on zds.
   It is stored as `atproto.zzstoatzz-io.atcr-app-password`; the secrets store
   README records the details. The spindle row and `atcr-creds` both use it now,
   and `docker login` succeeds.
2. The move also rotated the account's `#atproto` signing key, from
   `…FKXFjS` to `…nQquRi`. The hold that pushes go to,
   `us-chi1.cove.seamark.dev` (`did:plc:wnbpdx4bn5uts5yd2o2g3gnm`), cached the
   old DID document. It rejects every user service token with
   `service token authentication failed: signature verification failed:
   cryptographic signature invalid`. A token from zds verifies against the key
   PLC now publishes, and not against the old one. `hold01.atcr.io` names that hold
   as its successor, so switching `defaultHold` does not help.

In atcr's source (`pkg/hold/pds/auth.go`), `ValidateServiceToken` reads the
issuer key through the identity directory, which caches entries for 24 hours.
It never purges on failure. `ValidateAppviewToken` does: it calls
`InvalidateIdentity` and retries once. The hold also does not consume
`#identity` events, so nothing on our side can refresh it. The entry expires
no later than 2026-11-15T03:08Z.

## Finish

After 2026-11-15T03:08Z, rerun the publish workflow:

```bash
tg pipeline retry 3n2aw3vx7no22 -R zzstoatzz.io/prefect-server -w publish-ui-image.yml
```

The registry then holds the same tag that the node already has. Nothing in the
cluster needs to change.

## Upstream report

Not filed. A draft for `tangled.org/evan.jarrett.net/at-container-registry`:

> **Hold rejects user service tokens for up to 24h after the user's signing key rotates**
>
> After a PDS migration rotated my `#atproto` key (PLC updated 2026-11-14T03:08Z),
> every push to `us-chi1.cove.seamark.dev` failed at the hold with
> `service token authentication failed: signature verification failed:
> cryptographic signature invalid`. A service token from my new PDS verifies
> against the key in my current PLC document. `ValidateServiceToken` in
> `pkg/hold/pds/auth.go` resolves the issuer key through the 24h identity cache,
> and does not purge and retry on a signature failure the way
> `ValidateAppviewToken` does. Mirroring that retry for user tokens would fix
> it. Until then, restarting the hold clears the stale entry.
