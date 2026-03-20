import { query } from '$lib/server/db';
import type { Card, DashboardStats } from '$lib/types';

interface ActionRow {
	source: string;
	repo: string;
	identifier: string;
	kind: string;
	title: string;
	url: string;
	author: string;
	labels: string[];
	importance_score: number;
	updated: string;
}

export async function load() {
	const [stats] = await query<DashboardStats>(`
		SELECT
			count(*)::INT as tracked,
			count(*) FILTER (WHERE state = 'open')::INT as open,
			count(*) FILTER (WHERE reactions_total > 0)::INT as with_reactions,
			count(DISTINCT repo)::INT as repos
		FROM raw_github_issues
	`);

	const rows = await query<ActionRow>(`
		SELECT source, repo, identifier, kind, title, url,
			author, labels, importance_score, updated
		FROM hub_action_items
		ORDER BY importance_score DESC
		LIMIT 200
	`);

	const cards: Card[] = rows.map((r) => ({
		id: `${r.source}:${r.repo}#${r.identifier}`,
		source: r.source,
		kind: r.kind,
		title: r.title,
		url: r.url,
		score: r.importance_score,
		updated: r.updated,
		tags: Array.isArray(r.labels) ? r.labels : [],
		meta: {
			repo: r.repo,
			number: r.identifier,
			user: r.author
		}
	}));

	return { stats, cards };
}
