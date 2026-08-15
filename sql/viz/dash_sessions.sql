-- Sessoes de usuario agrupadas por status e wait_class - card de sessoes do dashboard.
-- Binds: nenhum.
-- Requer privilegio em V$SESSION (SELECT_CATALOG_ROLE ou grant direto).
-- Se ORA-00942: sem fallback ALL_ para views V$ - reportar limitacao no dashboard.
-- Compativel 19c.
SELECT s.status,
       NVL(s.wait_class, 'None') AS wait_class,
       COUNT(*)                  AS sessions
FROM   v$session s
WHERE  s.type = 'USER'
GROUP  BY s.status, NVL(s.wait_class, 'None')
ORDER  BY sessions DESC;
