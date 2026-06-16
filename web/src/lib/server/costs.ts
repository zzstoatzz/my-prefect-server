// loads the latest io.zzstoatzz.cost.snapshot record from the public PDS.
// no auth — the same com.atproto.repo.listRecords path hub already uses for
// tangled.org data. the costs flow (my-prefect-server) writes these daily.

import type { InfraCostSnapshot } from '$lib/types';

const PDS_HOST = process.env.PDS_HOST ?? 'https://pds.zzstoatzz.io';
const COST_REPO = process.env.COST_REPO ?? 'zzstoatzz.io';
const COLLECTION = 'io.zzstoatzz.cost.snapshot';

interface ListRecordsResponse {
	records: Array<{ uri: string; value: InfraCostSnapshot }>;
}

let cached: { at: number; data: InfraCostSnapshot | null } | null = null;
const TTL_MS = 5 * 60 * 1000;

export async function loadInfraCosts(): Promise<InfraCostSnapshot | null> {
	if (cached && Date.now() - cached.at < TTL_MS) return cached.data;

	const url =
		`${PDS_HOST}/xrpc/com.atproto.repo.listRecords` +
		`?repo=${encodeURIComponent(COST_REPO)}&collection=${COLLECTION}&limit=1`;

	try {
		// rkey is YYYY-MM-DD; listRecords returns newest rkey first, so limit=1
		// is the most recent snapshot.
		const resp = await fetch(url);
		if (!resp.ok) throw new Error(`PDS ${resp.status}`);
		const body = (await resp.json()) as ListRecordsResponse;
		const data = body.records[0]?.value ?? null;
		cached = { at: Date.now(), data };
		return data;
	} catch (err) {
		console.warn('failed to load infra costs:', err);
		// serve stale on error rather than blanking the panel
		return cached?.data ?? null;
	}
}
