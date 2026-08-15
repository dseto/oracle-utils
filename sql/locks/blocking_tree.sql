-- Arvore de bloqueio: holders raiz no topo (tree_level 1), waiters indentados.
-- Raiz = sessao que bloqueia alguem e nao esta bloqueada por ninguem.
-- Binds: nenhum.
SELECT LPAD(' ', (LEVEL - 1) * 2) || sid tree_sid,
       LEVEL tree_level,
       serial#,
       username,
       status,
       sql_id,
       event,
       seconds_in_wait,
       blocking_session,
       row_wait_obj# obj_waited
FROM   v$session
START WITH blocking_session IS NULL
       AND sid IN (SELECT blocking_session
                   FROM   v$session
                   WHERE  blocking_session IS NOT NULL)
CONNECT BY PRIOR sid = blocking_session
ORDER SIBLINGS BY seconds_in_wait DESC;
