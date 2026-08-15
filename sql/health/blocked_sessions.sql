-- Sessoes atualmente bloqueadas e o respectivo bloqueador direto.
-- Para a arvore completa de bloqueio, usar sql/locks/blocking_tree.sql.
-- Binds: nenhum.
SELECT s.sid,
       s.serial#,
       s.username,
       s.status,
       s.sql_id,
       s.event,
       s.seconds_in_wait,
       s.blocking_session,
       s.blocking_session_status,
       b.username  blocker_username,
       b.sql_id    blocker_sql_id,
       b.event     blocker_event,
       b.status    blocker_status
FROM   v$session s,
       v$session b
WHERE  s.blocking_session = b.sid(+)
AND    s.blocking_session IS NOT NULL
ORDER BY s.seconds_in_wait DESC;
