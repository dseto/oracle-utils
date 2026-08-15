-- Verifica se os objetos da cadeia foram compilados com PL/Scope.
-- IDENTIFIERS:ALL habilita all_identifiers; STATEMENTS:ALL habilita all_statements.
-- Objetos sem essa configuracao caem na camada lexica (all_source).
-- Binds: :owner, :object_list (lista separada por virgula; NULL = todos do owner)
SELECT owner,
       name,
       type,
       plscope_settings
FROM   all_plsql_object_settings
WHERE  owner = UPPER(:owner)
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || name || ',') > 0 )
ORDER  BY name, type;
