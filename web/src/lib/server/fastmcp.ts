import { query } from '$lib/server/db';
import type { FastmcpDay, FastmcpStuckPr, FastmcpTriage, FastmcpWeek } from '$lib/types';

/**
 * fastmcp triage state for agents watching throughput over weeks: daily flow
 * and open backlog by age, contributor PRs by opening week (split by whether
 * the triage reviewer saw them), and the PRs stuck waiting on a maintainer.
 * Built by the dbt models in analytics/models/fastmcp/.
 */
export async function loadFastmcpTriage(
	days: number,
	weeks: number,
	stuckLimit: number
): Promise<FastmcpTriage> {
	const dayRows = await query<FastmcpDay>(`
		SELECT day::VARCHAR AS day, kind, opened::INT AS opened, merged::INT AS merged,
		       closed_unmerged::INT AS closed_unmerged, open_end::INT AS open_end,
		       open_lt_7d::INT AS open_lt_7d, open_7_30d::INT AS open_7_30d,
		       open_30_90d::INT AS open_30_90d, open_gt_90d::INT AS open_gt_90d
		FROM fastmcp_daily
		WHERE day > current_date - ${days}
		ORDER BY day, kind
	`);
	const weekRows = await query<FastmcpWeek>(`
		SELECT week::VARCHAR AS week, triage_reviewed, prs::INT AS prs, merged::INT AS merged,
		       still_open::INT AS still_open, old_enough_7d::INT AS old_enough_7d,
		       responded_7d::INT AS responded_7d, old_enough_14d::INT AS old_enough_14d,
		       merged_14d::INT AS merged_14d,
		       median_hours_to_first_response, median_hours_to_merge
		FROM fastmcp_weekly
		WHERE week > current_date - ${weeks * 7}
		ORDER BY week, triage_reviewed
	`);
	const stuck = await query<FastmcpStuckPr>(`
		SELECT number::INT AS number, title, author, strftime(timezone('UTC', created_at), '%Y-%m-%dT%H:%M:%SZ') AS created_at,
		       age_days::INT AS age_days, triage_reviewed, reason,
		       strftime(timezone('UTC', last_maintainer_activity_at), '%Y-%m-%dT%H:%M:%SZ')
		           AS last_maintainer_activity_at, url
		FROM fastmcp_stuck
		ORDER BY age_days DESC
		LIMIT ${stuckLimit}
	`);
	return {
		repo: 'PrefectHQ/fastmcp',
		as_of: dayRows.at(-1)?.day ?? null,
		days: dayRows,
		weeks: weekRows,
		stuck
	};
}
