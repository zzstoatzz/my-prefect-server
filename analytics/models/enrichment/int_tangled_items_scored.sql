WITH scored AS (
    SELECT
        t.*,
        -- recency: decay over 30 days, max 1.0
        GREATEST(0.0, 1.0 - DATEDIFF('day', t.created_at::DATE, CURRENT_DATE) / 30.0) AS recency_score,
        -- no engagement data on PDS — flat 0.0
        0.0 AS engagement_score,
        -- contributor weight (from seed)
        COALESCE(kc.weight, 1.0) AS contributor_weight
    FROM {{ ref('stg_tangled_items') }} t
    LEFT JOIN {{ ref('known_contributors') }} kc ON t.author_handle = kc.login
)
SELECT *,
    -- same additive blend as github; engagement=0 means recency-only
    ROUND(
        (0.7 * recency_score + 0.3 * engagement_score)
        * contributor_weight,
        4
    ) AS importance_score
FROM scored
