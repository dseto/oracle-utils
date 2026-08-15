-- Fonte de um objeto PL/SQL (camada lexica). Detectar codigo wrapped:
-- primeira linha do body contem 'wrapped' -> analise lexica impossivel.
-- Binds: :owner, :object_name, :object_type (PACKAGE BODY, PROCEDURE, FUNCTION,
--        TRIGGER, TYPE BODY; NULL = todos os types do objeto)
SELECT s.type,
       s.line,
       s.text
FROM   all_source s
WHERE  s.owner = UPPER(:owner)
AND    s.name = UPPER(:object_name)
AND    ( :object_type IS NULL OR s.type = UPPER(:object_type) )
ORDER  BY s.type, s.line;
