-- bootstrap: ensure table exists even before liked posts have been resolved
{{ config(pre_hook="CREATE TABLE IF NOT EXISTS raw_liked_posts (subject_uri VARCHAR PRIMARY KEY, author_handle VARCHAR, author_did VARCHAR, text VARCHAR, created_at VARCHAR, liked_at VARCHAR, embed_type VARCHAR, embed_text VARCHAR, fetched_at TIMESTAMP DEFAULT now())") }}

-- dedup by subject_uri, keep most recent fetch
SELECT DISTINCT ON (subject_uri)
    subject_uri, author_handle, author_did, text,
    created_at, liked_at, embed_type, embed_text, fetched_at
FROM {{ source('raw', 'raw_liked_posts') }}
ORDER BY subject_uri, fetched_at DESC
