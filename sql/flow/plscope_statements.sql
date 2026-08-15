-- Statements SQL embutidos no PL/SQL (PL/Scope 12.2+/19c): tipo e texto.
-- Fonte para: DML que dispara trigger, EXECUTE IMMEDIATE, funcoes em SQL.
-- Requer PLSCOPE_SETTINGS com STATEMENTS:ALL.
-- Binds: :owner, :object_name
SELECT s.line,
       s.type             AS stmt_type,
       s.sql_id,
       s.has_into_record,
       s.text
FROM   all_statements s
WHERE  s.owner = UPPER(:owner)
AND    s.object_name = UPPER(:object_name)
ORDER  BY s.line;
