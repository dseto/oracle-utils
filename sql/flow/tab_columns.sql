-- Colunas de tabelas de um schema: tipo, nullable e default, restringivel a
-- uma lista de tabelas.
-- Fusao de sql/schema/schema_tables_cols.sql (ALL_TAB_COLUMNS, data_default)
-- com o padrao de bind :table_list via INSTR de sql/viz/erd_tables.sql.
-- data_default e LONG: comparar com cautela (SQLcl trunca; usar get_ddl.sql
-- do schema-diff em caso de duvida).
-- Binds: :owner (obrigatorio), :table_list (opcional; lista separada por
--        virgula, ex.: 'CLIENTE,PEDIDO,ITEM_PEDIDO'; NULL = todas as
--        tabelas do owner).
-- Usa ALL_TAB_COLUMNS (nao DBA_) de proposito: o extrator roda com o
-- schema do usuario conectado, sem depender de privilegio DBA_.
-- Compativel 19c.
SELECT c.owner,
       c.table_name,
       c.column_id,
       c.column_name,
       c.data_type,
       c.data_length,
       c.data_precision,
       c.data_scale,
       c.char_used,
       c.nullable,
       c.data_default
FROM   all_tab_columns c
JOIN   all_tables t
       ON t.owner = c.owner AND t.table_name = c.table_name
WHERE  c.owner = UPPER(:owner)
AND    ( :table_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:table_list), ' ', '') || ',',
                  ',' || c.table_name || ',') > 0 )
ORDER  BY c.table_name, c.column_id;
