-- Chamadas resolvidas pelo compilador (PL/Scope): quem chama o que, com linha.
-- usage='CALL' cobre procedures/functions; a juncao com signature liga o call
-- ao subprograma DECLARADO (resolve overload e escopo de package).
-- Requer objeto compilado com PLSCOPE_SETTINGS='IDENTIFIERS:ALL'.
-- Binds: :owner, :object_name
SELECT c.line,
       c.col,
       c.name             AS called_name,
       c.object_name      AS calling_object,
       d.owner            AS decl_owner,
       d.object_name      AS decl_object,
       d.object_type      AS decl_object_type,
       d.type             AS decl_type,
       d.line             AS decl_line
FROM   all_identifiers c
JOIN   all_identifiers d
       ON d.signature = c.signature
      AND d.usage = 'DECLARATION'
WHERE  c.owner = UPPER(:owner)
AND    c.object_name = UPPER(:object_name)
AND    c.usage = 'CALL'
ORDER  BY c.line, c.col;
