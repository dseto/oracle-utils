-- Atividade recente via ASH (ultimos :minutes minutos).
-- ATENCAO: V$ACTIVE_SESSION_HISTORY exige licenca Oracle Diagnostics Pack.
--   Sem licenca, usar samples_vsession.sql (amostragem manual, livre).
-- Binds: :minutes (janela em minutos; ex.: 15) - usado nos tres blocos.
-- 1 sample ASH ~= 1 segundo de atividade de 1 sessao (AAS aproximado = samples/segundos).

-- Bloco 1: atividade por wait class / evento.
SELECT CASE WHEN session_state = 'ON CPU' THEN 'ON CPU' ELSE wait_class END wait_class,
       CASE WHEN session_state = 'ON CPU' THEN 'ON CPU' ELSE event      END event,
       COUNT(*) samples,
       ROUND(COUNT(*) * 100 / SUM(COUNT(*)) OVER (), 1) pct
FROM   v$active_session_history
WHERE  sample_time > SYSTIMESTAMP - NUMTODSINTERVAL(:minutes, 'MINUTE')
GROUP BY CASE WHEN session_state = 'ON CPU' THEN 'ON CPU' ELSE wait_class END,
         CASE WHEN session_state = 'ON CPU' THEN 'ON CPU' ELSE event      END
ORDER BY samples DESC;

-- Bloco 2: top 10 sql_id por samples (candidatos a /sql-tune).
SELECT sql_id,
       COUNT(*) samples,
       ROUND(COUNT(*) * 100 / SUM(COUNT(*)) OVER (), 1) pct,
       SUM(CASE WHEN session_state = 'ON CPU' THEN 1 ELSE 0 END) cpu_samples,
       SUM(CASE WHEN session_state = 'WAITING' THEN 1 ELSE 0 END) wait_samples
FROM   v$active_session_history
WHERE  sample_time > SYSTIMESTAMP - NUMTODSINTERVAL(:minutes, 'MINUTE')
AND    sql_id IS NOT NULL
GROUP BY sql_id
ORDER BY samples DESC
FETCH FIRST 10 ROWS ONLY;

-- Bloco 3: top 10 sessoes por samples.
SELECT session_id sid,
       session_serial# serial#,
       user_id,
       COUNT(*) samples,
       COUNT(DISTINCT sql_id) distinct_sqls,
       MAX(sample_time) last_seen
FROM   v$active_session_history
WHERE  sample_time > SYSTIMESTAMP - NUMTODSINTERVAL(:minutes, 'MINUTE')
GROUP BY session_id, session_serial#, user_id
ORDER BY samples DESC
FETCH FIRST 10 ROWS ONLY;
