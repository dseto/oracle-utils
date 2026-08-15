-- Binds capturados do cursor (captura periodica, pode nao ter todos).
-- Binds: :sql_id
SELECT child_number, name, position, datatype_string,
       was_captured, last_captured, value_string
FROM   v$sql_bind_capture
WHERE  sql_id = :sql_id
ORDER  BY child_number, position;
