-- Catalogo de objetos de um schema: tipo, nome, status e data do ultimo DDL.
-- Derivada de sql/schema/schema_objects.sql (mesmos filtros de BIN$/particao
-- reciclada no lixo), com OWNER, STATUS e LAST_DDL_TIME acrescentados --
-- LAST_DDL_TIME alimenta o chain_hash do grafo de dependencias (banco mudou
-- -> hash muda -> grafo em disco fica marcado como desatualizado).
-- Binds: :owner (obrigatorio), :object_list (opcional; lista separada por
--        virgula de nomes de objeto, ex.: 'FLOW_DEMO,FLOW_DEMO_LOG'; NULL =
--        todos os objetos do owner).
-- Fallback DBA_: dba_objects (mesmas colunas, filtrar owner).
-- Compativel 19c.
SELECT owner,
       object_name,
       object_type,
       status,
       last_ddl_time
FROM   all_objects
WHERE  owner = UPPER(:owner)
AND    object_type NOT IN ('LOB', 'TABLE PARTITION', 'INDEX PARTITION',
                           'LOB PARTITION', 'TABLE SUBPARTITION', 'INDEX SUBPARTITION')
AND    object_name NOT LIKE 'BIN$%'
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || object_name || ',') > 0 )
ORDER  BY object_type, object_name;
