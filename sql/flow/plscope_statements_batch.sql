-- Statements SQL embutidos no PL/SQL (PL/Scope 12.2+/19c) de N objetos de um
-- owner numa unica consulta: irma em lote de plscope_statements.sql, usada
-- pela BFS por nivel e pelo enriquecimento em lote (classificacao de SQL
-- dinamico e acesso a tabela).
-- Mesma projecao de plscope_statements.sql MAIS s.object_name (o objeto de
-- ORIGEM de cada linha) -- sem essa coluna nao da para reagrupar os
-- statements por objeto no cliente depois do fetch em lote.
-- Requer PLSCOPE_SETTINGS com STATEMENTS:ALL.
-- Padrao de bind :object_list + INSTR copiado de tab_columns.sql.
-- Binds: :owner (obrigatorio), :object_list (opcional; lista separada por
--        virgula de nomes de objeto em maiusculas; NULL = todos os objetos
--        do owner).
-- Compativel 19c. ALL_STATEMENTS (nao DBA_).
SELECT s.object_name,
       s.line,
       s.type             AS stmt_type,
       s.sql_id,
       s.has_into_record,
       s.text
FROM   all_statements s
WHERE  s.owner = UPPER(:owner)
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || s.object_name || ',') > 0 )
ORDER  BY s.object_name, s.line;
