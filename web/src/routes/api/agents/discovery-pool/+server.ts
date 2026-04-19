import { json } from '@sveltejs/kit';
import { loadDiscoveryPool } from '$lib/server/discovery';

/**
 * Generic discovery pool: authors whose posts the operator has liked recently.
 * Consumers (any agent) can use this as a high-signal pool of people to
 * pay attention to. Filtering by per-consumer relevance happens client-side.
 *
 * Query params:
 *   window_days       — how many days back to consider (default 7)
 *   max_authors       — cap on distinct authors (default 30)
 *   samples_per_author — cap on sample posts per author (default 3)
 */
export async function GET({ url }) {
	const windowDays = Math.min(
		90,
		Math.max(1, Number(url.searchParams.get('window_days') ?? 7))
	);
	const maxAuthors = Math.min(
		100,
		Math.max(1, Number(url.searchParams.get('max_authors') ?? 30))
	);
	const samplesPerAuthor = Math.min(
		10,
		Math.max(1, Number(url.searchParams.get('samples_per_author') ?? 3))
	);

	const entries = await loadDiscoveryPool(windowDays, maxAuthors, samplesPerAuthor);
	return json(entries);
}
