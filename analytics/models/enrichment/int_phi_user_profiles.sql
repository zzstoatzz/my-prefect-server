WITH obs_counts AS (
    SELECT
        handle,
        COUNT(DISTINCT observation_id) AS observation_count,
        MIN(created_at) AS first_observation,
        MAX(created_at) AS latest_observation
    FROM {{ ref('stg_phi_observations') }}
    GROUP BY handle
),
tag_stats AS (
    SELECT
        handle,
        tag,
        COUNT(*) AS tag_count
    FROM (
        SELECT handle, UNNEST(tags) AS tag
        FROM {{ ref('stg_phi_observations') }}
    )
    GROUP BY handle, tag
),
top_tags AS (
    SELECT
        handle,
        LIST(tag ORDER BY tag_count DESC)[:5] AS top_tags
    FROM tag_stats
    GROUP BY handle
),
ix_stats AS (
    SELECT
        handle,
        COUNT(*) AS interaction_count,
        MIN(created_at) AS first_interaction,
        MAX(created_at) AS last_interaction
    FROM {{ ref('stg_phi_interactions') }}
    GROUP BY handle
)
SELECT
    o.handle,
    o.observation_count,
    COALESCE(i.interaction_count, 0) AS interaction_count,
    o.first_observation AS first_seen,
    i.last_interaction,
    t.top_tags,
    GREATEST(0.0, 1.0 - DATEDIFF('day',
        COALESCE(i.last_interaction, o.latest_observation)::DATE,
        CURRENT_DATE) / 30.0
    ) AS recency_score
FROM obs_counts o
LEFT JOIN top_tags t ON o.handle = t.handle
LEFT JOIN ix_stats i ON o.handle = i.handle
WHERE o.observation_count >= 3
