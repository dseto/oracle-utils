-- Top 10 eventos de espera nao-idle desde o startup - card de waits do dashboard.
-- Binds: nenhum.
-- Requer privilegio em V$SYSTEM_EVENT (SELECT_CATALOG_ROLE ou grant direto).
-- Se ORA-00942: sem fallback ALL_ para views V$ - mostrar aviso textual no dashboard.
-- Valores sao acumulados desde o startup da instancia (nao e delta).
-- Compativel 19c.
SELECT e.event,
       e.wait_class,
       e.total_waits,
       ROUND(e.time_waited_micro / 1e6, 1)                              AS time_waited_sec,
       ROUND(e.time_waited_micro / 1000 / GREATEST(e.total_waits, 1), 2) AS avg_wait_ms
FROM   v$system_event e
WHERE  e.wait_class <> 'Idle'
ORDER  BY e.time_waited_micro DESC
FETCH  FIRST 10 ROWS ONLY;
