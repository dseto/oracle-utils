-- Child cursors do sql_id: planos distintos, bind sensitivity (ACS).
-- Binds: :sql_id
SELECT child_number, plan_hash_value, executions,
       ROUND(elapsed_time/GREATEST(executions,1)/1000,1) AS elapsed_ms_per_exec,
       buffer_gets/GREATEST(executions,1) AS gets_per_exec,
       is_bind_sensitive, is_bind_aware, is_shareable,
       optimizer_env_hash_value
FROM   v$sql
WHERE  sql_id = :sql_id
ORDER  BY child_number;
