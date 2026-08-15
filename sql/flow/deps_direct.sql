-- Dependencias DIRETAS de um objeto (espaco de busca da resolucao lexica):
-- o compilador ja registrou todo objeto referenciado estaticamente.
-- NAO inclui alvos de SQL dinamico (tratados a parte pela skill).
-- Binds: :owner, :object_name
SELECT d.referenced_owner,
       d.referenced_name,
       d.referenced_type,
       d.dependency_type
FROM   all_dependencies d
WHERE  d.owner = UPPER(:owner)
AND    d.name = UPPER(:object_name)
AND    d.referenced_type NOT IN ('NON-EXISTENT')
ORDER  BY d.referenced_owner, d.referenced_name;
