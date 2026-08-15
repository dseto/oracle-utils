-- Amostragem manual de V$SESSION - alternativa LIVRE ao ASH (sem Diagnostics Pack).
-- Executar repetidamente (ex.: a cada 5-10 segundos durante alguns minutos)
-- e acumular os resultados; cada execucao e um sample da atividade instantanea.
-- Exclui a propria sessao e sessoes background.
-- Binds: nenhum.
SELECT TO_CHAR(SYSDATE, 'HH24:MI:SS') sample_time,
       s.sid,
       s.serial#,
       s.username,
       s.sql_id,
       CASE WHEN s.state = 'WAITING' THEN s.event ELSE 'ON CPU' END activity,
       CASE WHEN s.state = 'WAITING' THEN s.wait_class ELSE 'ON CPU' END wait_class,
       s.seconds_in_wait,
       s.blocking_session
FROM   v$session s
WHERE  s.status = 'ACTIVE'
AND    s.type = 'USER'
AND    s.sid <> SYS_CONTEXT('USERENV', 'SID')
AND    NOT (s.state = 'WAITING' AND s.wait_class = 'Idle')
ORDER BY s.sid;
