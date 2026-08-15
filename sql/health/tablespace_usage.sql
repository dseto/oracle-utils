-- Uso de tablespaces permanentes: alocado, livre, pct usado e autoextend.
-- pct_used_max considera o limite maximo (maxbytes) quando autoextend = YES;
-- e o percentual que importa para o semaforo (alocado pode estar 100% e ainda crescer).
-- Tablespaces TEMP nao aparecem em DBA_FREE_SPACE (ver V$TEMP_SPACE_HEADER se preciso).
-- Binds: nenhum.
-- Fallback: DBA_DATA_FILES/DBA_FREE_SPACE exigem privilegio de dicionario;
--   sem ele nao ha equivalente ALL_ completo - avisar limitacao ao usuario.
SELECT df.tablespace_name,
       ROUND(df.bytes_alloc / 1024 / 1024)                                        mb_alloc,
       ROUND(NVL(fs.bytes_free, 0) / 1024 / 1024)                                 mb_free,
       ROUND((df.bytes_alloc - NVL(fs.bytes_free, 0)) / 1024 / 1024)              mb_used,
       ROUND((df.bytes_alloc - NVL(fs.bytes_free, 0)) * 100 / df.bytes_alloc, 1)  pct_used_alloc,
       ROUND(df.bytes_max / 1024 / 1024)                                          mb_max,
       ROUND((df.bytes_alloc - NVL(fs.bytes_free, 0)) * 100 / df.bytes_max, 1)    pct_used_max,
       df.autoext_files,
       df.total_files
FROM  (SELECT tablespace_name,
              SUM(bytes) bytes_alloc,
              SUM(CASE WHEN autoextensible = 'YES'
                       THEN GREATEST(maxbytes, bytes)
                       ELSE bytes END) bytes_max,
              SUM(CASE WHEN autoextensible = 'YES' THEN 1 ELSE 0 END) autoext_files,
              COUNT(*) total_files
       FROM   dba_data_files
       GROUP BY tablespace_name) df,
      (SELECT tablespace_name,
              SUM(bytes) bytes_free
       FROM   dba_free_space
       GROUP BY tablespace_name) fs
WHERE df.tablespace_name = fs.tablespace_name(+)
ORDER BY pct_used_max DESC;
