-- Estatisticas de colunas: num_distinct, nulls, histograma.
-- Colunas de predicado sem histograma + dados skewed = cardinalidade errada.
-- Binds: :owner, :table_name
-- Fallback ALL_: all_tab_col_statistics.
SELECT column_name, num_distinct, num_nulls, density,
       histogram, num_buckets, sample_size, last_analyzed
FROM   dba_tab_col_statistics
WHERE  owner = UPPER(:owner)
AND    table_name = UPPER(:table_name)
ORDER  BY column_name;
