WITH scored AS (
    SELECT
        e.*,
        -- email goes stale fast: decay over 7 days, not 30
        GREATEST(0.0, 1.0 - DATEDIFF('day', e.received_at::DATE, CURRENT_DATE) / 7.0) AS recency_score,
        -- unread is the whole point of surfacing email
        CASE WHEN e.unread THEN 1.0 ELSE 0.2 END AS unread_score
    FROM {{ ref('stg_emails') }} e
    WHERE e.received_at != ''
)
SELECT *,
    ROUND(0.6 * recency_score + 0.4 * unread_score, 4) AS importance_score
FROM scored
