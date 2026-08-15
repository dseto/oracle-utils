-- FKs com ON DELETE CASCADE/SET NULL apontando para uma tabela:
-- DELETE no pai executa acao nos filhos e pode disparar triggers deles.
-- Binds: :owner (owner da tabela pai), :table_name (tabela pai)
SELECT fk.owner        AS child_owner,
       fk.table_name   AS child_table,
       fk.constraint_name,
       fk.delete_rule
FROM   all_constraints pk
JOIN   all_constraints fk
       ON fk.r_owner = pk.owner
      AND fk.r_constraint_name = pk.constraint_name
WHERE  pk.owner = UPPER(:owner)
AND    pk.table_name = UPPER(:table_name)
AND    pk.constraint_type IN ('P', 'U')
AND    fk.constraint_type = 'R'
AND    fk.delete_rule IN ('CASCADE', 'SET NULL')
ORDER  BY fk.owner, fk.table_name;
