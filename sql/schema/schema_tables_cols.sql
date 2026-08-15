-- Colunas de todas as tabelas do schema: tipo, tamanho, nullable e default.
-- Base para diff de estrutura (tipo/tamanho/nullable/default divergentes).
-- char_used: B = length semantics BYTE, C = CHAR (relevante em diff de charset).
-- data_default e LONG: comparar com cautela (SQLcl trunca; usar get_ddl.sql em caso de duvida).
-- Binds: :owner
-- Fallback DBA_: dba_tab_columns.
SELECT c.table_name, c.column_id, c.column_name,
       c.data_type,
       c.data_length, c.data_precision, c.data_scale, c.char_length, c.char_used,
       c.nullable, c.data_default
FROM   all_tab_columns c
JOIN   all_tables t
       ON t.owner = c.owner AND t.table_name = c.table_name
WHERE  c.owner = UPPER(:owner)
ORDER  BY c.table_name, c.column_id;
