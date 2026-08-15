-- Fonte de N objetos PL/SQL (camada lexica) de um owner numa unica consulta:
-- irma em lote de fetch_source.sql, usada pela BFS por nivel e pelo
-- enriquecimento em lote. Detectar codigo wrapped: primeira linha do body
-- contem 'wrapped' -> analise lexica impossivel.
-- Mesma projecao de fetch_source.sql MAIS s.name (o objeto de ORIGEM de cada
-- linha) -- sem essa coluna nao da para reagrupar as linhas de fonte por
-- objeto no cliente depois do fetch em lote.
-- Padrao de bind :object_list + INSTR copiado de tab_columns.sql.
-- Binds: :owner (obrigatorio), :object_list (opcional; lista separada por
--        virgula de nomes de objeto em maiusculas; NULL = todos os objetos
--        do owner), :object_type (opcional; PACKAGE BODY, PROCEDURE,
--        FUNCTION, TRIGGER, TYPE BODY; NULL = todos os types dos objetos).
-- Compativel 19c. ALL_SOURCE (nao DBA_).
SELECT s.name,
       s.type,
       s.line,
       s.text
FROM   all_source s
WHERE  s.owner = UPPER(:owner)
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || s.name || ',') > 0 )
AND    ( :object_type IS NULL OR s.type = UPPER(:object_type) )
ORDER  BY s.name, s.type, s.line;
