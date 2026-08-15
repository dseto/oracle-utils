-- Foto atual: sessoes de usuario ativas agrupadas por wait class e evento.
-- Sessoes em estado WAITED... (ou seja, rodando em CPU) aparecem como ON CPU.
-- Binds: nenhum.
SELECT CASE WHEN s.state = 'WAITING' THEN s.wait_class ELSE 'ON CPU' END wait_class,
       CASE WHEN s.state = 'WAITING' THEN s.event      ELSE 'ON CPU' END event,
       COUNT(*) sessions
FROM   v$session s
WHERE  s.status = 'ACTIVE'
AND    s.type = 'USER'
AND    NOT (s.state = 'WAITING' AND s.wait_class = 'Idle')
GROUP BY CASE WHEN s.state = 'WAITING' THEN s.wait_class ELSE 'ON CPU' END,
         CASE WHEN s.state = 'WAITING' THEN s.event      ELSE 'ON CPU' END
ORDER BY sessions DESC;
