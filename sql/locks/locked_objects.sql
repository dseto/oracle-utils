-- Objetos com lock DML ativo e as sessoes que os seguram.
-- Binds: nenhum.
-- Fallback: se DBA_OBJECTS der ORA-00942, trocar por ALL_OBJECTS.
SELECT lo.session_id sid,
       s.serial#,
       lo.oracle_username,
       lo.os_user_name,
       o.owner,
       o.object_name,
       o.object_type,
       DECODE(lo.locked_mode,
              0, 'NONE',
              1, 'NULL',
              2, 'ROW SHARE (SS)',
              3, 'ROW EXCLUSIVE (SX)',
              4, 'SHARE (S)',
              5, 'SHARE ROW EXCLUSIVE (SSX)',
              6, 'EXCLUSIVE (X)') locked_mode,
       s.sql_id,
       s.status,
       s.logon_time
FROM   v$locked_object lo,
       dba_objects o,
       v$session s
WHERE  lo.object_id = o.object_id
AND    lo.session_id = s.sid
ORDER BY o.owner, o.object_name, lo.session_id;
