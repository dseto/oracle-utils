-- Dependentes de um objeto (quem USA o objeto) - analise de impacto: o que quebra se eu alterar X.
-- Percorre a arvore de dependencias para cima via CONNECT BY NOCYCLE.
-- Binds: :owner, :object_name, :max_depth (profundidade maxima; sugerido 3).
-- Fallback ALL_: all_dependencies (se ORA-00942 em DBA_).
-- Atencao: SQL dinamico (EXECUTE IMMEDIATE, DBMS_SQL) NAO aparece em *_dependencies.
-- Compativel 19c.
SELECT LEVEL              AS depth,
       d.owner,
       d.name,
       d.type,
       d.referenced_owner,
       d.referenced_name,
       d.referenced_type
FROM   dba_dependencies d
WHERE  d.owner NOT IN ('SYS', 'SYSTEM', 'PUBLIC')
START  WITH d.referenced_owner = UPPER(:owner)
       AND  d.referenced_name  = UPPER(:object_name)
CONNECT BY NOCYCLE
       d.referenced_owner = PRIOR d.owner
       AND d.referenced_name = PRIOR d.name
       AND d.referenced_type = PRIOR d.type
       AND LEVEL <= TO_NUMBER(:max_depth)
ORDER  BY depth, d.owner, d.name;
