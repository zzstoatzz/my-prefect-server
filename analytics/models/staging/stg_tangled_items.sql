-- bootstrap: ensure table exists even before tangled-items flow has run
{{ config(pre_hook="CREATE TABLE IF NOT EXISTS raw_tangled_items (repo VARCHAR, kind VARCHAR, title VARCHAR, body VARCHAR, url VARCHAR, at_uri VARCHAR PRIMARY KEY, author_did VARCHAR, author_handle VARCHAR, created_at VARCHAR, parent_uri VARCHAR, fetched_at TIMESTAMP DEFAULT now())") }}

-- dedup by at_uri, keep most recent fetch; exclude comments (context, not action items)
SELECT DISTINCT ON (at_uri)
    repo, kind, title, body, url, at_uri,
    author_did, author_handle, created_at,
    parent_uri, fetched_at
FROM {{ source('raw', 'raw_tangled_items') }}
WHERE kind IN ('issue', 'pr')
ORDER BY at_uri, fetched_at DESC
