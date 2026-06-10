import { query } from '$lib/server/db';
import { loadBriefing } from '$lib/server/briefing';
import type { Card, DashboardStats, SpendSummary } from '$lib/types';

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

export async function loadSpendSummary(): Promise<SpendSummary> {
	try {
		const [totals] = await query<{
			total_24h: number;
			total_7d: number;
			total_all: number;
			requests_24h: number;
		}>(`
			SELECT
				coalesce(sum(total_cost_usd) FILTER (WHERE recorded_at >= now() - INTERVAL '24 hours'), 0)::DOUBLE AS total_24h,
				coalesce(sum(total_cost_usd) FILTER (WHERE recorded_at >= now() - INTERVAL '7 days'), 0)::DOUBLE AS total_7d,
				coalesce(sum(total_cost_usd), 0)::DOUBLE AS total_all,
				coalesce(sum(request_count) FILTER (WHERE recorded_at >= now() - INTERVAL '24 hours'), 0)::INT AS requests_24h
			FROM raw_llm_spend
		`);

		const by_flow_7d = await query<SpendSummary['by_flow_7d'][number]>(`
			SELECT
				coalesce(nullif(flow_name, ''), 'unknown') AS flow_name,
				sum(total_cost_usd)::DOUBLE AS cost_usd,
				sum(input_tokens)::INT AS input_tokens,
				sum(output_tokens)::INT AS output_tokens,
				sum(request_count)::INT AS requests
			FROM raw_llm_spend
			WHERE recorded_at >= now() - INTERVAL '7 days'
			GROUP BY 1
			ORDER BY cost_usd DESC
			LIMIT 8
		`);

		const recent = await query<SpendSummary['recent'][number]>(`
			SELECT
				recorded_at::VARCHAR AS recorded_at,
				coalesce(nullif(flow_name, ''), 'unknown') AS flow_name,
				task_name,
				model,
				total_cost_usd::DOUBLE AS cost_usd,
				input_tokens::INT AS input_tokens,
				output_tokens::INT AS output_tokens
			FROM raw_llm_spend
			ORDER BY recorded_at DESC
			LIMIT 12
		`);

		return { ...totals, by_flow_7d, recent };
	} catch {
		return {
			total_24h: 0,
			total_7d: 0,
			total_all: 0,
			requests_24h: 0,
			by_flow_7d: [],
			recent: []
		};
	}
}
