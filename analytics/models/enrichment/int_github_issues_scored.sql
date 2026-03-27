WITH scored AS (
    SELECT
        i.*,
        -- recency: decay over 30 days, max 1.0
        GREATEST(0.0, 1.0 - DATEDIFF('day', updated_at::DATE, CURRENT_DATE) / 30.0) AS recency_score,
        -- engagement: log scale capped at 1.0
        LEAST(1.0, LN(1 + comments + reactions_total) / LN(50)) AS engagement_score,
        -- label signal multipliers
        CASE
            WHEN list_contains(labels, 'bug') THEN 1.5
            WHEN list_contains(labels, 'good first issue') THEN 0.7
            ELSE 1.0
        END AS label_multiplier,
        -- contributor weight (from seed)
        COALESCE(kc.weight, 1.0) AS contributor_weight
    FROM {{ ref('stg_github_issues') }} i
    LEFT JOIN {{ ref('known_contributors') }} kc ON i."user" = kc.login
)
SELECT *,
    -- additive blend: recency is primary signal, engagement boosts
    ROUND(
        (0.7 * recency_score + 0.3 * engagement_score)
        * label_multiplier
        * contributor_weight,
        4
    ) AS importance_score
FROM scored
WHERE state = 'open'
