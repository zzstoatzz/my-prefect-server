import { query } from '$lib/server/db';
import type { DiscoveryPoolEntry, DiscoveryPoolPost } from '$lib/types';

/** A single liked-post row as it comes back from the DB. */
interface LikedRow {
	author_handle: string;
	author_did: string;
	subject_uri: string;
	text: string;
	liked_at: string;
}

/**
 * Discovery pool: authors whose posts the operator has liked recently,
 * grouped with up to N sample posts each. Generic — not specific to
 * any one consumer agent.
 *
 * @param windowDays  how many days back to look at likes
 * @param maxAuthors  cap on distinct authors returned
 * @param samplesPerAuthor  cap on sample posts per author
 */
export async function loadDiscoveryPool(
	windowDays = 7,
	maxAuthors = 30,
	samplesPerAuthor = 3
): Promise<DiscoveryPoolEntry[]> {
	const rows = await query<LikedRow>(`
		SELECT author_handle, author_did, subject_uri, text, liked_at
		FROM raw_liked_posts
		WHERE liked_at >= (now() - INTERVAL '${windowDays} days')::VARCHAR
		  AND author_handle != ''
		ORDER BY liked_at DESC
	`);

	const byAuthor = new Map<string, { did: string; posts: DiscoveryPoolPost[] }>();
	for (const r of rows) {
		const existing = byAuthor.get(r.author_handle);
		if (existing) {
			if (existing.posts.length < samplesPerAuthor) {
				existing.posts.push({
					uri: r.subject_uri,
					text: r.text ?? '',
					liked_at: r.liked_at
				});
			}
		} else {
			byAuthor.set(r.author_handle, {
				did: r.author_did,
				posts: [
					{
						uri: r.subject_uri,
						text: r.text ?? '',
						liked_at: r.liked_at
					}
				]
			});
		}
	}

	// We need the FULL liked count per author (not just the samples we kept).
	const counts = await query<{ author_handle: string; n: number }>(`
		SELECT author_handle, count(*)::INT AS n
		FROM raw_liked_posts
		WHERE liked_at >= (now() - INTERVAL '${windowDays} days')::VARCHAR
		  AND author_handle != ''
		GROUP BY author_handle
	`);
	const countByHandle = new Map(counts.map((c) => [c.author_handle, c.n]));

	const entries: DiscoveryPoolEntry[] = Array.from(byAuthor.entries()).map(
		([handle, { did, posts }]) => ({
			handle,
			did,
			likes_in_window: countByHandle.get(handle) ?? posts.length,
			last_liked_at: posts[0]?.liked_at ?? '',
			sample_posts: posts
		})
	);

	entries.sort((a, b) => {
		if (b.likes_in_window !== a.likes_in_window) {
			return b.likes_in_window - a.likes_in_window;
		}
		return b.last_liked_at.localeCompare(a.last_liked_at);
	});

	return entries.slice(0, maxAuthors);
}
