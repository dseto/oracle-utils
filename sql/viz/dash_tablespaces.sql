-- Uso percentual de tablespaces - barras de ocupacao do dashboard.
-- used_percent ja considera autoextend (tablespace_size = tamanho maximo possivel).
-- Binds: nenhum.
-- Requer DBA_TABLESPACE_USAGE_METRICS (privilegio de dicionario).
-- Se ORA-00942: sem equivalente ALL_ - reportar limitacao no dashboard
--   (alternativa manual: dba_data_files + dba_free_space, mesmos privilegios).
-- Compativel 19c.
SELECT m.tablespace_name,
       ROUND(m.used_space * t.block_size / 1024 / 1024, 1)      AS used_mb,
       ROUND(m.tablespace_size * t.block_size / 1024 / 1024, 1) AS max_mb,
       ROUND(m.used_percent, 1)                                 AS used_pct,
       t.contents,
       t.status
FROM   dba_tablespace_usage_metrics m
JOIN   dba_tablespaces t
       ON t.tablespace_name = m.tablespace_name
ORDER  BY m.used_percent DESC;
