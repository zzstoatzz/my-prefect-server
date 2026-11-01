-- per day and kind: opened, closed, merged, and what was open at the end of the day by age.
-- an item closed and reopened counts as open across its closed gap.
WITH days AS (
    SELECT unnest(range(
        coalesce(
            (SELECT min(created_day) FROM {{ ref('stg_fastmcp_items') }}),
            timezone('America/Chicago', now())::DATE
        ),
        timezone('America/Chicago', now())::DATE + 1,
        INTERVAL 1 DAY
    ))::DATE AS day
),
kinds AS (SELECT unnest(['issue', 'pr']) AS kind),
flow AS (
    SELECT d.day, k.kind,
        count(*) FILTER (WHERE i.created_day = d.day) AS opened,
        count(*) FILTER (WHERE i.closed_day = d.day AND i.merged_at IS NULL) AS closed_unmerged,
        count(*) FILTER (WHERE i.merged_day = d.day) AS merged,
        count(*) FILTER (WHERE i.created_day <= d.day AND (i.closed_day IS NULL OR i.closed_day > d.day)) AS open_end,
        count(*) FILTER (WHERE i.created_day <= d.day AND (i.closed_day IS NULL OR i.closed_day > d.day)
            AND d.day - i.created_day < 7) AS open_lt_7d,
        count(*) FILTER (WHERE i.created_day <= d.day AND (i.closed_day IS NULL OR i.closed_day > d.day)
            AND d.day - i.created_day BETWEEN 7 AND 29) AS open_7_30d,
        count(*) FILTER (WHERE i.created_day <= d.day AND (i.closed_day IS NULL OR i.closed_day > d.day)
            AND d.day - i.created_day BETWEEN 30 AND 89) AS open_30_90d,
        count(*) FILTER (WHERE i.created_day <= d.day AND (i.closed_day IS NULL OR i.closed_day > d.day)
            AND d.day - i.created_day >= 90) AS open_gt_90d
    FROM days d
    CROSS JOIN kinds k
    LEFT JOIN {{ ref('stg_fastmcp_items') }} i ON i.kind = k.kind AND i.created_day <= d.day
    GROUP BY d.day, k.kind
)
SELECT * FROM flow ORDER BY day, kind
