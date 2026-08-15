-- Objetos invalidos: resumo por owner/tipo e detalhe dos primeiros 100.
-- Binds: nenhum.
-- Fallback: se DBA_OBJECTS der ORA-00942, trocar por ALL_OBJECTS.
SELECT owner,
       object_type,
       COUNT(*) qtd
FROM   dba_objects
WHERE  status = 'INVALID'
GROUP BY owner, object_type
ORDER BY owner, object_type;

-- Detalhe (limitado a 100 para nao poluir a saida).
SELECT owner,
       object_name,
       object_type,
       last_ddl_time
FROM   dba_objects
WHERE  status = 'INVALID'
ORDER BY owner, object_type, object_name
FETCH FIRST 100 ROWS ONLY;
