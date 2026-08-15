-- Indices da tabela com colunas, seletividade e clustering factor.
-- clustering_factor proximo de num_rows da tabela = indice ruim para range scan.
-- Binds: :owner, :table_name
-- Fallback ALL_: all_indexes / all_ind_columns.
SELECT i.index_name, i.uniqueness, i.status, i.visibility,
       i.distinct_keys, i.clustering_factor, i.leaf_blocks, i.last_analyzed,
       LISTAGG(c.column_name, ', ') WITHIN GROUP (ORDER BY c.column_position) AS columns
FROM   dba_indexes i
JOIN   dba_ind_columns c
       ON c.index_owner = i.owner AND c.index_name = i.index_name
WHERE  i.table_owner = UPPER(:owner)
AND    i.table_name  = UPPER(:table_name)
GROUP  BY i.index_name, i.uniqueness, i.status, i.visibility,
       i.distinct_keys, i.clustering_factor, i.leaf_blocks, i.last_analyzed
ORDER  BY i.index_name;
