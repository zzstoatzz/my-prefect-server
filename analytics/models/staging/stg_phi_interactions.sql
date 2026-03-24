-- dedup by interaction_id, keep most recent fetch
SELECT DISTINCT ON (interaction_id)
    handle, interaction_id, content, created_at, fetched_at
FROM {{ source('raw', 'raw_phi_interactions') }}
ORDER BY interaction_id, fetched_at DESC
