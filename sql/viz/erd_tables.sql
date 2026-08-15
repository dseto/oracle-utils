-- Colunas, tipos e nullable das tabelas de um schema, para montar entidades do ERD.
-- Binds: :owner (obrigatorio), :table_list (opcional; lista separada por virgula,
--        ex.: 'CLIENTE,PEDIDO,ITEM_PEDIDO'; NULL = todas as tabelas do owner).
-- Fallback ALL_: all_tab_columns / all_tables (se ORA-00942 em DBA_).
-- Compativel 19c.
SELECT c.table_name,
       c.column_id,
       c.column_name,
       c.data_type,
       c.data_length,
       c.data_precision,
       c.data_scale,
       c.nullable
FROM   dba_tab_columns c
JOIN   dba_tables t
       ON t.owner = c.owner AND t.table_name = c.table_name
WHERE  c.owner = UPPER(:owner)
AND    ( :table_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:table_list), ' ', '') || ',',
                  ',' || c.table_name || ',') > 0 )
ORDER  BY c.table_name, c.column_id;
