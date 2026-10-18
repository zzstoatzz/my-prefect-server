# Bot merge identity and mirror

Nate authorized a dedicated Gardener merge identity with access limited to
`zzstoatzz.io/bot`, retaining the human merge gate, after requesting a check of
recent repository work. Bot main `26d93fc` still had the stale full-atlas cache;
the approved third round of pull `3mxge6p7esx3q` had not landed or changed.
Recent tag-maintenance and memory-schema repairs were reviewed independently;
they do not replace the cache patch.

The old `tangled-merge-ssh-key` was recorded as exposed in the canonical secrets
ledger. It was replaced before registration, with the replacement stored in
the encrypted store and synchronized to the existing Prefect Secret block.
Their hashes match. The replacement public fingerprint is
`SHA256:SDVCpcS5RXOgyH6lalyoUgZAxfT9ga9JPGlVIN5PMsk`; its record is
`at://did:plc:7vx7exykq2zfxjxxejovrymi/sh.tangled.publicKey/3my5th2orcv22`.

The knot ACL grants Gardener collaboration only on bot's repository DID,
`did:plc:ebajxjsw7iuwstddnxt6lt3m`. No knot membership or other repository
permission was added. A dry-run push using only the replacement key succeeds
for bot and fails for my-prefect-server. Neither probe creates a Git ref.
The existing human Resume gate is unchanged; this permission grant does not
approve any pull on Nate's behalf.

The merge controller previously mirrored only my-prefect-server. Bot requires
both Tangled and GitHub pushes, so bot now uses the same existing mirror path.
All 14 controller tests pass, along with focused lint and formatting checks.
The rebuilt wheel SHA256 is
`2bc3a35c35d21fbe68147508754807c41ed6a1663533ad2fba7c3b5203b49e95`;
the uploaded HeavyPad artifact matches that hash after Nate completed Tailscale
SSH reauthentication. The deployment now pins that verified wheel.
A new controller run must test the approved patch against current main before
offering human approval. Local revalidation of the approved patch on bot main
`26d93fc` passed all 22 frontend tests and Svelte checks.
