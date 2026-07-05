-- bootstrap: ensure table exists even before the classifier has ever run
{{ config(pre_hook="CREATE TABLE IF NOT EXISTS raw_email_classifications (message_id VARCHAR PRIMARY KEY, category VARCHAR, classified_at TIMESTAMP DEFAULT now())") }}

SELECT message_id, category, classified_at
FROM {{ source('raw', 'raw_email_classifications') }}
