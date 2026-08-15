-- Uso da Fast Recovery Area (FRA): limite, usado, reclaimable e percentuais.
-- pct_used_net desconta o espaco reclaimable (o que realmente pressiona a FRA).
-- Se space_limit = 0 ou nenhuma linha, a FRA nao esta configurada nesta instancia.
-- Binds: nenhum.
SELECT name,
       ROUND(space_limit / 1024 / 1024)                                          mb_limit,
       ROUND(space_used / 1024 / 1024)                                           mb_used,
       ROUND(space_reclaimable / 1024 / 1024)                                    mb_reclaimable,
       ROUND(space_used * 100 / NULLIF(space_limit, 0), 1)                       pct_used,
       ROUND((space_used - space_reclaimable) * 100 / NULLIF(space_limit, 0), 1) pct_used_net,
       number_of_files
FROM   v$recovery_file_dest;

-- Detalhe por tipo de arquivo (archivelog, backup, flashback log, etc.).
SELECT file_type,
       percent_space_used,
       percent_space_reclaimable,
       number_of_files
FROM   v$recovery_area_usage
ORDER BY percent_space_used DESC;
