-- bootstrap: ensure table exists even before the email fetch has ever run
{{ config(pre_hook=[
    "CREATE TABLE IF NOT EXISTS raw_emails (message_id VARCHAR PRIMARY KEY, subject VARCHAR, sender_name VARCHAR, sender_address VARCHAR, snippet VARCHAR, received_at VARCHAR, unread BOOLEAN, mailbox VARCHAR, fetched_at TIMESTAMP DEFAULT now(), is_bulk BOOLEAN DEFAULT false)",
    "ALTER TABLE raw_emails ADD COLUMN IF NOT EXISTS is_bulk BOOLEAN DEFAULT false"
]) }}

-- dedup by message_id, keep most recent fetch (unread flag changes over time)
SELECT DISTINCT ON (message_id)
    message_id, subject, sender_name, sender_address,
    snippet, received_at, unread,
    COALESCE(is_bulk, false) AS is_bulk,
    mailbox, fetched_at
FROM {{ source('raw', 'raw_emails') }}
ORDER BY message_id, fetched_at DESC
