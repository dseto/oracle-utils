-- Dependencias DIRETAS de N objetos de um owner numa unica consulta: irma em
-- lote de deps_direct.sql, usada pela BFS por nivel (agrupa a fila inteira
-- do nivel por owner e faz UMA chamada, em vez de um round-trip por objeto).
-- Mesma projecao de deps_direct.sql MAIS d.name (o objeto de ORIGEM de cada
-- linha) -- sem essa coluna nao da para reagrupar as dependencias por objeto
-- no cliente depois do fetch em lote.
-- Padrao de bind :object_list + INSTR copiado de tab_columns.sql.
-- Binds: :owner (obrigatorio), :object_list (opcional; lista separada por
--        virgula de nomes de objeto em maiusculas, ex.: 'PKG_A,PKG_B';
--        NULL = todos os objetos do owner).
-- Compativel 19c. ALL_DEPENDENCIES (nao DBA_).
SELECT d.name,
       d.referenced_owner,
       d.referenced_name,
       d.referenced_type,
       d.dependency_type
FROM   all_dependencies d
WHERE  d.owner = UPPER(:owner)
AND    d.referenced_type NOT IN ('NON-EXISTENT')
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || d.name || ',') > 0 )
ORDER  BY d.name, d.referenced_owner, d.referenced_name;
