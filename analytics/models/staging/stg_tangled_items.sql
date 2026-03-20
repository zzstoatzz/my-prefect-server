-- dedup by at_uri, keep most recent fetch; exclude comments (context, not action items)
SELECT DISTINCT ON (at_uri)
    repo, kind, title, body, url, at_uri,
    author_did, author_handle, created_at,
    parent_uri, fetched_at
FROM {{ source('raw', 'raw_tangled_items') }}
WHERE kind IN ('issue', 'pr')
ORDER BY at_uri, fetched_at DESC
