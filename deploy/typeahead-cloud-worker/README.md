# Typeahead cloud publisher

The index build runs on the existing Hetzner orchestration host, under a dedicated
unprivileged process worker. Bulk source export, artifact upload and checksum
readback stay in the cloud. The PLC identity deployment remains on hold.

Both the worker and future flow-run commands pin Prefect 3.8.2, matching the
server. The worker is limited to one flow, one CPU and 3 GiB maximum memory,
with low CPU/I/O priority. Active SQLite work runs on the host local SSD, with
at least 96 GiB free before a build. Completed generations move to the dedicated
150 GB volume only after copies match their published checksums; publication
failure preserves active data. Streamed build output also enforces an 8 GiB
remaining-space reserve. The compiler cache survives source checkouts.
Its API credential is a root-only
`/etc/typeahead-cloud-worker.env` containing `PREFECT_API_URL` and
`PREFECT_API_AUTH_STRING`, derived from the existing operator configuration.
Runtime Turso and R2 credentials remain Prefect Secret-block references.

On the verified host, attach and format a volume before running:

```sh
bash deploy/typeahead-cloud-worker/install.sh MPS_COMMIT VOLUME_ID
```

The installer checks that the volume is mounted, preserves other data, installs
rclone/uv and the checksum-pinned Zig 0.17.0 toolchain, and starts the systemd
worker from a committed MPS revision. Do not put credential values in arguments
or logs. Inspect `systemctl status prefect-typeahead-worker` and its journal.

`prefect.yaml` is authoritative. Initial qualification preserves the existing
`pds-compact-31ec142-20261020` builder pin, full corpus and 10,000-actor PDS cutoff,
but publishes to `staging/cloud-publisher-20261025/latest.json`. The schedule is
inactive and production publication disarmed until full cloud publication,
native replay, protected recovery and public freshness pass. Register only the
selected deployment for manual qualification, with an exact `MPS_PIN`.

After qualification, update production channel/arming and the active schedule in
`prefect.yaml`, land on main, register and verify the queued run. Do not restore
the home publisher. Build source and the staged pair must remain available if
publication fails; do not repeat a successful full source export unnecessarily.
Backend binary rollout and front-door routing changes are separate work and
remain on hold pending Nate's explicit authorization.

A recovery run may pass `reuse_source` with an existing absolute SQLite checkpoint
path. This selects the native reader's existing `INDEX_REUSE_SOURCE` path and
avoids another source export. The default remains `None`, so scheduled runs export
a fresh checkpoint. Validate and record the preserved input before using it.
