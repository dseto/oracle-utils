-- Localiza sql_id em cache a partir de fragmento do texto.
-- Binds: :txt (fragmento do SQL, sem % nas pontas — adicionados aqui)
SELECT sql_id, child_number, plan_hash_value, executions,
       ROUND(elapsed_time/GREATEST(executions,1)/1000,1) AS elapsed_ms_per_exec,
       buffer_gets/GREATEST(executions,1) AS gets_per_exec,
       SUBSTR(sql_text,1,120) AS sql_text
FROM   v$sql
WHERE  UPPER(sql_text) LIKE '%'||UPPER(:txt)||'%'
AND    sql_text NOT LIKE '%v$sql%'
ORDER  BY last_active_time DESC
FETCH FIRST 20 ROWS ONLY;
