-- fails when the fastmcp sync has not seen any activity for a day
SELECT max(updated_at) AS newest
FROM {{ ref('stg_fastmcp_items') }}
HAVING max(updated_at) < now() - INTERVAL 1 DAY
