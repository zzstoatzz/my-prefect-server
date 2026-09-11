# Revision-bound Phi reviews

The live patch → Phi review → Pi revision → fresh review cycle passed on September 10, 2026 UTC. Human merge approval was exercised, but the authorized push failed because the dedicated merge SSH key is not registered with Tangled. Nothing merged.

The contract binds every verdict to a pull URI, record CID, and round index. Patch reads use the captured record; comments require its CID and reject changed records. A revision published after comment validation cannot make an old verdict apply to a new round. Revision publication uses `swapRecord` to avoid overwriting another writer. Merge approval keys include the CID, target, branch, round, and patch; the flow checks identity again before pushing.

Live evidence:

- Proposal: `b019eefe-2f12-4d5f-bde5-af0dc812a369`.
- Gardener pull: `at://did:plc:7vx7exykq2zfxjxxejovrymi/sh.tangled.repo.pull/3mv4v6e5b5aw2`.
- Phi requested UTF-8 byte-count regression coverage on round 0.
- Pi revision: `5ab746b5-12a0-4deb-a65f-33c06cdea730`, terminal Revised, Sprite deleted.
- Fresh Phi approval: `current-review-verdict.json`, round index 1, CID `bafyreihgdnqge6q3vhh7d46oauygf6vellxjzhuvshns7cwb2dcu6phv7a`.
- Isolated test run `8ee64d45-ff5b-45e3-a491-d7c4e4640d40`: 215 tests passed; result artifact readable after Sprite deletion.
- Human gate `efa3f625-9fc1-4ffa-bad8-b4755c422a69`: suspended, approved by the operator, resumed, retested, failed at push. Both validation Sprites were deleted.

Repository tests execute as UID 2000 inside a separate Sprite, without the merge key, Prefect credentials, inference socket, or host filesystem. Package installation has public network access. A live boundary probe verified those restrictions. Test receipts bind the base SHA and patch hash and survive Sprite deletion; the merge flow refuses missing, failed, or mismatched receipts.

The Tangled MCP revision-binding patch and Phi review prompts are deployed. Local contract coverage includes changed records, later rounds, stale verdicts, and revision publication races. Merge-flow tests currently pass (14 tests). The compact approval summary links to the full patch/review/test evidence; mobile rendering remains unverified.

Remaining: authorize and configure merge-key registration, verify the approved merge without weakening revision checks, restore the merge automation, persist deployment configuration, and complete source/inventory closeout. Model routing is explicitly deferred.
