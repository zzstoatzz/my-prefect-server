-- bootstrap: ensure table exists even before the email fetch has ever run
{{ config(pre_hook="CREATE TABLE IF NOT EXISTS raw_emails (message_id VARCHAR PRIMARY KEY, subject VARCHAR, sender_name VARCHAR, sender_address VARCHAR, snippet VARCHAR, received_at VARCHAR, unread BOOLEAN, mailbox VARCHAR, fetched_at TIMESTAMP DEFAULT now())") }}

-- dedup by message_id, keep most recent fetch (unread flag changes over time)
SELECT DISTINCT ON (message_id)
    message_id, subject, sender_name, sender_address,
    snippet, received_at, unread, mailbox, fetched_at
FROM {{ source('raw', 'raw_emails') }}
ORDER BY message_id, fetched_at DESC
