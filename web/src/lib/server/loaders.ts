import { query } from '$lib/server/db';
import { loadBriefing } from '$lib/server/briefing';
import type { Card, DashboardStats } from '$lib/types';

export type { Card, DashboardStats };
export { loadBriefing };

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

export async function loadCards(): Promise<Card[]> {
	const rows = await query<ActionRow>(`
		SELECT source, repo, identifier, kind, title, url,
			author, labels, importance_score, updated
		FROM hub_action_items
		ORDER BY importance_score DESC
		LIMIT 200
	`);

	return rows.map((r) => ({
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
}

export async function loadStats(): Promise<DashboardStats> {
	const [stats] = await query<DashboardStats>(`
		SELECT
			count(*)::INT as tracked,
			count(*) FILTER (WHERE state = 'open')::INT as open,
			count(*) FILTER (WHERE reactions_total > 0)::INT as with_reactions,
			count(DISTINCT repo)::INT as repos
		FROM raw_github_issues
	`);
	return stats;
}
