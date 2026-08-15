-- Estatisticas da tabela: volume, frescor, staleness.
-- Binds: :owner, :table_name
-- Fallback: se DBA_TAB_STATISTICS der ORA-00942, trocar por ALL_TAB_STATISTICS.
SELECT owner, table_name, num_rows, blocks, avg_row_len,
       sample_size, last_analyzed, stale_stats, stattype_locked
FROM   dba_tab_statistics
WHERE  owner = UPPER(:owner)
AND    table_name = UPPER(:table_name)
AND    object_type = 'TABLE';
