import { json } from '@sveltejs/kit';
import { loadFastmcpTriage } from '$lib/server/fastmcp';

/**
 * fastmcp triage state for agents watching throughput and stuck work.
 * Public GitHub data only.
 *
 * Query params:
 *   days         — daily rows to return (default 90, max 730)
 *   weeks        — weekly cohorts to return (default 16, max 104)
 *   stuck_limit  — cap on stuck PRs, oldest first (default 50, max 200)
 */
export async function GET({ url }) {
	const clamp = (name: string, fallback: number, max: number) =>
		Math.min(max, Math.max(1, Math.trunc(Number(url.searchParams.get(name) ?? fallback)) || fallback));
	return json(
		await loadFastmcpTriage(clamp('days', 90, 730), clamp('weeks', 16, 104), clamp('stuck_limit', 50, 200))
	);
}
