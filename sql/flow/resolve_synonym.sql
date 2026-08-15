-- Resolve sinonimo (incluindo PUBLIC) ate o objeto base.
-- Sinonimo em cadeia: reexecutar com o resultado ate table_owner nao ser
-- dono de outro sinonimo de mesmo nome (a skill controla a iteracao).
-- db_link preenchido = objeto remoto -> folha externa, nao recursar.
-- Binds: :owner (schema que enxerga o sinonimo), :name
SELECT s.owner        AS synonym_owner,
       s.synonym_name,
       s.table_owner  AS base_owner,
       s.table_name   AS base_name,
       s.db_link
FROM   all_synonyms s
WHERE  s.synonym_name = UPPER(:name)
AND    s.owner IN (UPPER(:owner), 'PUBLIC')
ORDER  BY CASE s.owner WHEN 'PUBLIC' THEN 2 ELSE 1 END;
