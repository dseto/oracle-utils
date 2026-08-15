-- Inventario de objetos de um schema: tipo, nome e status.
-- Base para diff de inventario entre dois schemas (ausentes/sobrando).
-- Binds: :owner
-- Fallback DBA_: dba_objects (mesmas colunas, filtrar owner).
SELECT object_type, object_name, status
FROM   all_objects
WHERE  owner = UPPER(:owner)
AND    object_type NOT IN ('LOB', 'TABLE PARTITION', 'INDEX PARTITION',
                           'LOB PARTITION', 'TABLE SUBPARTITION', 'INDEX SUBPARTITION')
AND    object_name NOT LIKE 'BIN$%'
ORDER  BY object_type, object_name;
