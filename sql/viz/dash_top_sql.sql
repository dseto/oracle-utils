-- Top 10 SQL por tempo total (elapsed) em V$SQLSTATS - tabela de top SQL do dashboard.
-- Binds: nenhum.
-- Requer privilegio em V$SQLSTATS (SELECT_CATALOG_ROLE ou grant direto).
-- Se ORA-00942: avisar no dashboard que top SQL nao esta disponivel (sem fallback ALL_).
-- FETCH FIRST e 12c+, ok para baseline 19c.
-- Compativel 19c.
SELECT sql_id,
       executions,
       ROUND(elapsed_time / 1e6, 2)                            AS elapsed_sec_total,
       ROUND(elapsed_time / 1e6 / GREATEST(executions, 1), 4)  AS elapsed_sec_per_exec,
       ROUND(cpu_time / 1e6, 2)                                AS cpu_sec_total,
       buffer_gets,
       disk_reads,
       rows_processed,
       SUBSTR(REPLACE(REPLACE(sql_text, CHR(10), ' '), CHR(13), ' '), 1, 120) AS sql_text_120
FROM   v$sqlstats
ORDER  BY elapsed_time DESC
FETCH  FIRST 10 ROWS ONLY;
