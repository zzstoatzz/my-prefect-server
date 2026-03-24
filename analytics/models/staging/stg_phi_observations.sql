-- dedup by observation_id, keep most recent fetch
SELECT DISTINCT ON (observation_id)
    handle, observation_id, content, tags, created_at, fetched_at
FROM {{ source('raw', 'raw_phi_observations') }}
ORDER BY observation_id, fetched_at DESC
