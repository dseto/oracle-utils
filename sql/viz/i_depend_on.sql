-- Referenciados por um objeto (o que o objeto USA) - dependencias para baixo.
-- Percorre a arvore de dependencias via CONNECT BY NOCYCLE.
-- Binds: :owner, :object_name, :max_depth (profundidade maxima; sugerido 3).
-- Fallback ALL_: all_dependencies (se ORA-00942 em DBA_).
-- Filtra referencias a objetos de SYS/SYSTEM/PUBLIC (STANDARD, DBMS_*, etc.) para reduzir ruido.
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
WHERE  d.referenced_owner NOT IN ('SYS', 'SYSTEM', 'PUBLIC')
START  WITH d.owner = UPPER(:owner)
       AND  d.name  = UPPER(:object_name)
CONNECT BY NOCYCLE
       d.owner = PRIOR d.referenced_owner
       AND d.name = PRIOR d.referenced_name
       AND d.type = PRIOR d.referenced_type
       AND LEVEL <= TO_NUMBER(:max_depth)
ORDER  BY depth, d.referenced_owner, d.referenced_name;
