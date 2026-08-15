-- Hierarquia de object types + metodos com OVERRIDING (dispatch dinamico).
-- Para uma chamada a metodo de um supertipo, todo subtipo com OVERRIDING
-- e candidato em runtime -> arestas tracejadas 'override?' no grafo.
-- method_type: PUBLIC (comum), MAP ou ORDER; inherited = veio do supertipo.
-- Binds: :owner, :type_name (supertipo de partida)
SELECT t.type_name,
       t.supertype_name,
       t.final,
       t.instantiable,
       m.method_name,
       m.method_no,
       m.method_type,
       m.overriding,
       m.inherited
FROM   all_types t
LEFT JOIN all_type_methods m
       ON m.owner = t.owner AND m.type_name = t.type_name
WHERE  t.owner = UPPER(:owner)
START  WITH t.owner = UPPER(:owner) AND t.type_name = UPPER(:type_name)
CONNECT BY PRIOR t.type_name = t.supertype_name
       AND t.owner = UPPER(:owner)
ORDER  BY t.type_name, m.method_no;
