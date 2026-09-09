# Public Evergreen and private Hub

Evergreen remains the public project and status site at https://nate.tngl.io/.
Hub remains the private operational dashboard at https://hub.waow.tech/.
The attempted redirect and Hub project-page replacement were rejected and rolled
back. Do not repeat that consolidation or remove public routes without checking
consumers and obtaining agreement on the public/private boundary.

`costs/costs` writes the cost snapshot that Evergreen consumes through the public
Hub costs API and its Worker proxy. Fleet checks use the packaged inventory in
`packages/mps/src/mps/projects.json`, including Evergreen's public homepage.
Evergreen publishes `services.json`; its existing Worker offers `/status` and
legacy proxy routes. Keep these public contracts available.

Fleet's Evergreen check verifies the app is present, rejects meta redirects or
login pages even with HTTP 200, and validates the public JSON inventory. Network
operations retain task retries. Findings return Completed(name="Degraded") and
reach phi through the existing Logfire push alert. Full results remain in Prefect
artifacts. The fleet notification links to public Evergreen, not private Hub.

The website inventory and packaged fleet inventory are currently separate;
sharing their data safely is unfinished work, not grounds to replace either UI.
The fleet inventory includes four retained checks beyond Evergreen's 46 checks.

The prior Hub image is `atcr.io/zzstoatzz.io/hub:1d6985f`; its exact web source and
manifest have been restored on this branch. The existing presence routes and
credentials are preserved. No Cloudflare Access policy was changed.

## Discord

Flow failures use the existing Prefect automation with status, run name, and a
link to the run's error and logs. Fleet messages include up to five bounded
findings plus the public status link. Machine consumers retain the full array.
The Logfire flow-failure alert still pushes to phi, but no longer sends a duplicate
Discord table. Other Logfire alert types have not yet been reformatted.
