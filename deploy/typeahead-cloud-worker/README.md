# Typeahead cloud publisher

This is the original-server worker for `typeahead-index`; HeavyPad now runs the
deployment on the `typeahead-ephemeral` pool (see
[automatic refresh](../../docs/automatic-typeahead-refresh.md)). The worker runs
under a dedicated unprivileged process. Checkpoint download, import, artifact
upload and checksum readback stay in the cloud. The PLC identity deployment
remains on hold.

Both the worker and future flow-run commands pin Prefect 3.8.2, matching the
server. The worker is limited to one flow, one CPU and 3 GiB maximum memory,
with low CPU/I/O priority. Tantivy preparation runs under `INDEXER_HOME` on the
host local SSD. Streamed publication output enforces an 8 GiB remaining-space
reserve on `INDEX_BUILD_ROOT`.
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

`prefect.yaml` keeps this worker's inactive definition; `prefect.heavypad.yaml`
owns the live deployment and schedule. Publication writes immutable generations
under `releases/search-tantivy/` and changes no pointer. The rollout controller
installs only onto the inactive Tantivy slot with independent storage, checks
admission and queries, and conditionally updates the routing record. Serving
compute and disk never perform bulk installation. See
[automatic refresh](../../docs/automatic-typeahead-refresh.md) for qualification,
cadence, retention and recovery.

Land source/configuration on main before registering the selected deployment with
an exact `MPS_PIN`. Verify the live schedule, next queued run and its parameters
after registration. Do not restore the home publisher or enable PLC identity as
part of snapshot refresh. A failed automatic preparation is finished with
`resume_rollout`; never save it into the deployment's scheduled defaults.

The cloud Rclone build rejects its post-upload HEAD request with HTTP 501.
`RCLONE_S3_NO_HEAD=true` avoids that incompatible request. The publisher
still reads every uploaded object back and checks its full SHA-256 and size;
this verification is mandatory before the manifest is published. Real small-object
probes verified clean upload/readback with this setting and were deleted.
The cloud transfer cap is 80 MiB/s; the old home-transfer cap is not retained.

The pool and worker both need a concurrency limit of one. On the operator checkout,
run `just prefect work-pool set-concurrency-limit typeahead-cloud-pool 1` after
creating the pool, then inspect the live pool limit. Deployment registration does
not replace this pool setting. The worker service also retains `--limit 1`.
