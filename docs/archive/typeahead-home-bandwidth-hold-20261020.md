# Typeahead home bandwidth hold

October 20, 2026: set the schedules for `typeahead-index` and
`typeahead-plc-identity` inactive in `prefect.yaml` while their bulk work moves
to existing cloud infrastructure. Preserve the cron expressions and all
parameters; no home worker or unrelated deployment is paused.

The index publisher's current qualified pair is about 23.3 GB. Upload plus
checksum readback is about 46.6 GB, before its multi-GB source export. Another
home publication would therefore consume tens of GB despite rate limiting.
HeavyPad is on the metered home network; its retained data remains available
for offline qualification without another bulk transfer.

Live Typeahead relay ingestion and the search overlay continue independently.
The previous snapshot keeps serving until a qualified replacement is published.
Neither this hold nor successful offline reconstruction completes the repair.

Before resuming automatic bulk runs, qualify the cloud executor, source and
artifact transfer budgets, current PDS policy, protected recovery pair, native
admission/replay, writer coordination and live ingestion/search gates. Then
update the authoritative work-pool/code/parameter configuration, land it in
main, register the selected deployments and verify their next queued runs.
Do not merely reactivate the old home schedules.

The live deployment IDs are `5d446811-c098-438e-b16c-f1ccd9da7e97`
(index) and `e8d460a1-56c5-4cc0-b31c-b1ead909fcc7` (PLC identity).
Use the repository's `just prefect` wrapper for API access. Pausing schedules
must be followed by checking already-scheduled runs; cancel only runs belonging
to these two bulk deployments when necessary to enforce this hold.
