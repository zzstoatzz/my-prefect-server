# Candidate cloud publication

This is a per-run override of deployment `a0c0ce9c-5bab-4bdc-8512-39984c322a74`,
using its existing `typeahead-ephemeral` hcloud publisher. It does not update the
deployment, native cadence, routes or serving machines. Do not submit until the
full benchmark result and remaining release gates have been reviewed.

Build the POST body from a fresh deployment readback. Preserve the full observed
`job_variables`, especially the complete `env` map of Prefect block references;
API nested-map merge behavior must not decide credential configuration. Change
only these fields (MPS_SHA is the immutable companion commit containing this doc):

```python
job = copy.deepcopy(deployment["job_variables"])
job["command"] = (
    "env PREFECT_PLUGINS_ENABLED=true PREFECT_PLUGINS_ALLOW=mps-secrets "
    "uv run --no-project "
    "--with 'my-prefect-server @ git+https://github.com/zzstoatzz/my-prefect-server.git@"
    + MPS_SHA + "' --with prefect==3.8.2 prefect flow-run execute"
)
job["env"].update({
    "MPS_PIN": "@" + MPS_SHA,
    "INDEX_TANTIVY_SOURCE_REVISION": "557021cebe0cf94dc7f4e90e0625b4a583bc3e49",
    "INDEX_TANTIVY_ADAPTER_SHA256": "ce99c13446f70cd889c89b8c5469eea351e1bb522d5057ccd40359a6c9fdad5c",
    "INDEX_TANTIVY_BINARY": "/var/lib/typeahead-indexer/work/code/typeahead-tantivy",
    "INDEX_TANTIVY_BINARY_SHA256": "d1cc0366ed3b28b34daca15d23ee6f06aa1d08844e483c357b80cdb778d5cfa2",
    "INDEX_TANTIVY_PREPARE_SECONDS": "36000",
    "INDEX_TANTIVY_MAX_SOURCE_BYTES": "8589934592",
    "INDEX_TANTIVY_SOURCE_MIN_INTERVAL_SECONDS": "0.5",
})
payload = {
    "name": "tantivy-candidate-publication-" + MPS_SHA[:12],
    "parameters": {
        "ref": "557021cebe0cf94dc7f4e90e0625b4a583bc3e49",
        "snapshot_engine": "tantivy", "snapshot_format": "compact",
        "automatic_refresh": False, "reuse_source": None,
        "resume_generation": None, "rollback": False, "resume_rollout": False,
    },
    "job_variables": job,
    "enforce_parameter_schema": False,
}
# POST /deployments/a0c0ce9c-5bab-4bdc-8512-39984c322a74/create_flow_run
```

The old deployment schema does not know `snapshot_engine`; bypassing its parameter
schema alone is insufficient. Both the executable package pin and `MPS_PIN` must
change so `cached_checkout` materializes the same candidate source. Preserve
cpx32/fsn1 and the existing 43,200-second job timeout. Concurrency remains one.
The source export, native twin build, Tantivy import and source SELECTs all run
on this cloud worker. No local path is assumed to exist on its fresh disk.

The fixed code key is
`releases/code/tantivy/d1cc0366ed3b28b34daca15d23ee6f06aa1d08844e483c357b80cdb778d5cfa2/typeahead-tantivy`.
Its published size is 10,028,384 bytes. A missing/wrong code artifact fails before
Tantivy import. The native source/twin stage uses the existing publisher’s `--immutable-only`
mode; neither the live source pointer nor routing is changed. Publication requires
actual source reconciliation completion;
budget/deadline failure cannot produce a successful manifest. The eight-GiB
cumulative allowance includes a two-GiB polling reserve and survives process
restart without resetting counters.

This command creates a new native source through the existing publisher; it does
not assume a prior ephemeral VM's archive persists. Retaining the native builder's
already-exported SQLite could avoid zeroed count/activity reindexing later, but
requires a separate complete snapshot/policy/tombstone contract. Its existing
`MAX(updated_at)` watermark cannot stand in for a completed authoritative sweep.
