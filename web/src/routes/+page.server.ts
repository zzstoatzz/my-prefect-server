import { query } from '$lib/server/db';

interface Stats {
	tracked: number;
	open: number;
	with_reactions: number;
	repos: number;
}

interface ActionItem {
	repo: string;
	number: number;
	type: string;
	title: string;
	url: string;
	user: string;
	labels: string;
	comments: number;
	reactions_total: number;
	importance_score: number;
	updated_at: string;
}

export async function load() {
	const [stats] = await query<Stats>(`
		SELECT
			count(*)::INT as tracked,
			count(*) FILTER (WHERE state = 'open')::INT as open,
			count(*) FILTER (WHERE reactions_total > 0)::INT as with_reactions,
			count(DISTINCT repo)::INT as repos
		FROM raw_github_issues
	`);

	const items = await query<ActionItem>(`
		SELECT repo, number, type, title, url, "user",
			array_to_string(labels, ', ') AS labels,
			comments, reactions_total, importance_score, updated_at
		FROM github_action_items
		ORDER BY importance_score DESC
	`);

	return { stats, items };
}
