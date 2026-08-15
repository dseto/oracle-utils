-- Locks relevantes (quem bloqueia ou quem espera), decodificando tipo e modo.
-- block = 1: este lock esta bloqueando outra sessao. request > 0: sessao esperando.
-- ctime = segundos desde que o lock foi obtido/solicitado.
-- Binds: nenhum.
SELECT l.sid,
       s.serial#,
       s.username,
       l.type,
       DECODE(l.type,
              'TX', 'Transaction (row lock / ITL / etc.)',
              'TM', 'DML enqueue (table lock)',
              'UL', 'User-defined (DBMS_LOCK)',
              'JI', 'Materialized view refresh',
              l.type || ' (ver documentacao)') lock_type_desc,
       DECODE(l.lmode,
              0, 'NONE',
              1, 'NULL',
              2, 'ROW SHARE (SS)',
              3, 'ROW EXCLUSIVE (SX)',
              4, 'SHARE (S)',
              5, 'SHARE ROW EXCLUSIVE (SSX)',
              6, 'EXCLUSIVE (X)') mode_held,
       DECODE(l.request,
              0, 'NONE',
              1, 'NULL',
              2, 'ROW SHARE (SS)',
              3, 'ROW EXCLUSIVE (SX)',
              4, 'SHARE (S)',
              5, 'SHARE ROW EXCLUSIVE (SSX)',
              6, 'EXCLUSIVE (X)') mode_requested,
       l.id1,
       l.id2,
       l.block,
       l.ctime seconds
FROM   v$lock l,
       v$session s
WHERE  l.sid = s.sid
AND   (l.block = 1 OR l.request > 0)
ORDER BY l.block DESC, l.ctime DESC;
