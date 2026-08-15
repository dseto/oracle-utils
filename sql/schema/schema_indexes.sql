-- Indices do schema com lista de colunas agregada via LISTAGG.
-- Base para diff de indices (ausentes, unicidade ou colunas divergentes).
-- Nomes gerados divergem entre schemas: comparar por tabela+colunas+uniqueness.
-- Indices function-based: coluna aparece como SYS_NC...; conferir expressao em all_ind_expressions.
-- Binds: :owner
-- Fallback DBA_: dba_indexes / dba_ind_columns.
SELECT i.table_name, i.index_name, i.index_type, i.uniqueness,
       i.status, i.visibility,
       LISTAGG(c.column_name, ', ') WITHIN GROUP (ORDER BY c.column_position) AS columns
FROM   all_indexes i
JOIN   all_ind_columns c
       ON c.index_owner = i.owner AND c.index_name = i.index_name
WHERE  i.owner = UPPER(:owner)
AND    i.index_type != 'LOB'
GROUP  BY i.table_name, i.index_name, i.index_type, i.uniqueness,
          i.status, i.visibility
ORDER  BY i.table_name, i.index_name;
