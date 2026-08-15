-- Metricas agregadas de execucao do sql_id.
-- Binds: :sql_id
SELECT sql_id, plan_hash_value, executions,
       ROUND(elapsed_time/GREATEST(executions,1)/1000,1)  AS elapsed_ms_per_exec,
       ROUND(cpu_time/GREATEST(executions,1)/1000,1)      AS cpu_ms_per_exec,
       buffer_gets/GREATEST(executions,1)                 AS gets_per_exec,
       disk_reads/GREATEST(executions,1)                  AS reads_per_exec,
       rows_processed/GREATEST(executions,1)              AS rows_per_exec,
       last_active_time
FROM   v$sqlstats
WHERE  sql_id = :sql_id;
