-- Quem DEPENDE de um conjunto de objetos: INVERSO de deps_direct.sql /
-- deps_direct_batch.sql (la :owner/:object_list sao a ORIGEM; aqui sao o
-- ALVO -- filtra por d.referenced_owner/d.referenced_name e devolve
-- d.owner/d.name, quem aponta pra eles).
-- GRAO OBJETO, nao subprograma: ALL_DEPENDENCIES nao tem essa granularidade
-- -- "o objeto X referencia o objeto Y", nunca "o subprograma P de X chama
-- o subprograma Q de Y". So SINALIZADOR de chamador fora do fechamento do
-- mapa (T-05, contrato dynsql-dossie), nunca enumeracao de call sites.
-- Visibilidade limitada ao usuario atual (ALL_DEPENDENCIES, nao DBA_):
-- dependente em outro schema, via GRANT+sinonimo, pode ficar invisivel
-- aqui -- o sinalizador nunca promete completude (backlog 4.3.1).
-- Binds: :owner (dono dos objetos ALVO, os referenciados), :object_list
--        (lista separada por virgula de nomes de objeto em maiusculas, ex.:
--        'PKG_A,PKG_B'; NULL = todo mundo que depende de qualquer objeto do
--        owner). Mesmo padrao de bind + INSTR de deps_direct_batch.sql.
-- Compativel 19c. ALL_DEPENDENCIES (nao DBA_).
SELECT d.owner,
       d.name,
       d.type,
       d.referenced_name,
       d.dependency_type
FROM   all_dependencies d
WHERE  d.referenced_owner = UPPER(:owner)
AND    d.referenced_type NOT IN ('NON-EXISTENT')
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || d.referenced_name || ',') > 0 )
ORDER  BY d.referenced_name, d.owner, d.name;
