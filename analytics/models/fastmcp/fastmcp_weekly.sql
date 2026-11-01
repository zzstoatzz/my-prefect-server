-- contributor PRs by the week they were opened, split by whether the triage reviewer saw them.
-- responded_7d and merged_14d only count PRs old enough to have had the chance. merges, not
-- closures: automation (the issue-link gate, auto-close) closes many contributor PRs.
SELECT
    date_trunc('week', created_day)::DATE AS week,
    triage_reviewed,
    count(*) AS prs,
    count(*) FILTER (WHERE merged_at IS NOT NULL) AS merged,
    count(*) FILTER (WHERE state = 'OPEN') AS still_open,
    count(*) FILTER (WHERE age_days >= 7) AS old_enough_7d,
    count(*) FILTER (WHERE age_days >= 7 AND hours_to_first_response <= 24 * 7) AS responded_7d,
    count(*) FILTER (WHERE age_days >= 14) AS old_enough_14d,
    count(*) FILTER (WHERE age_days >= 14 AND merged_at IS NOT NULL AND hours_to_resolution <= 24 * 14)
        AS merged_14d,
    median(hours_to_first_response) AS median_hours_to_first_response,
    median(hours_to_resolution) FILTER (WHERE merged_at IS NOT NULL) AS median_hours_to_merge
FROM {{ ref('fastmcp_pr_flow') }}
GROUP BY ALL
ORDER BY week, triage_reviewed
