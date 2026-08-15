-- Resumo de constraints do schema: tipo, tabela, colunas, status e referencia (FK).
-- Base para diff de constraints (ausentes, status divergente, colunas diferentes).
-- constraint_type: P = PK, U = unique, R = FK, C = check.
-- Nomes gerados (SYS_C...) divergem entre schemas: comparar por tabela+tipo+colunas, nao por nome.
-- search_condition (check) e LONG: nao comparavel via SQL puro; conferir via get_ddl.sql se preciso.
-- Binds: :owner
-- Fallback DBA_: dba_constraints / dba_cons_columns.
SELECT c.table_name, c.constraint_name, c.constraint_type,
       c.status, c.validated, c.deferrable,
       (SELECT LISTAGG(cc.column_name, ', ') WITHIN GROUP (ORDER BY cc.position)
        FROM   all_cons_columns cc
        WHERE  cc.owner = c.owner AND cc.constraint_name = c.constraint_name) AS columns,
       c.r_owner,
       (SELECT rc.table_name
        FROM   all_constraints rc
        WHERE  rc.owner = c.r_owner AND rc.constraint_name = c.r_constraint_name) AS r_table_name,
       c.delete_rule
FROM   all_constraints c
WHERE  c.owner = UPPER(:owner)
AND    c.constraint_type IN ('P', 'U', 'R', 'C')
ORDER  BY c.table_name, c.constraint_type, c.constraint_name;
