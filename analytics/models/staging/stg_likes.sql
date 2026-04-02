-- bootstrap: ensure table exists even before likes have been ingested
{{ config(pre_hook="CREATE TABLE IF NOT EXISTS raw_likes (at_uri VARCHAR PRIMARY KEY, subject_uri VARCHAR, created_at VARCHAR, fetched_at TIMESTAMP DEFAULT now())") }}

-- dedup by at_uri, keep most recent fetch
SELECT DISTINCT ON (at_uri)
    at_uri, subject_uri, created_at, fetched_at
FROM {{ source('raw', 'raw_likes') }}
ORDER BY at_uri, fetched_at DESC
