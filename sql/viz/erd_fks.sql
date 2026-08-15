-- Foreign keys (tipo R) com constraint pai resolvida: tabela pai + colunas dos dois lados.
-- fk_nullable = 'Y' se alguma coluna da FK aceita NULL (relacionamento opcional no ERD).
-- Binds: :owner (obrigatorio), :table_list (opcional; filtra tabela filha; NULL = todas).
-- Fallback ALL_: all_constraints / all_cons_columns / all_tab_columns (se ORA-00942 em DBA_).
-- Compativel 19c.
SELECT fk.table_name        AS child_table,
       fk.constraint_name   AS fk_name,
       fkc.position,
       fkc.column_name      AS child_column,
       pk.table_name        AS parent_table,
       pkc.column_name      AS parent_column,
       tc.nullable          AS child_col_nullable,
       fk.delete_rule
FROM   dba_constraints fk
JOIN   dba_constraints pk
       ON pk.owner = fk.r_owner AND pk.constraint_name = fk.r_constraint_name
JOIN   dba_cons_columns fkc
       ON fkc.owner = fk.owner AND fkc.constraint_name = fk.constraint_name
JOIN   dba_cons_columns pkc
       ON pkc.owner = pk.owner AND pkc.constraint_name = pk.constraint_name
       AND pkc.position = fkc.position
JOIN   dba_tab_columns tc
       ON tc.owner = fk.owner AND tc.table_name = fk.table_name
       AND tc.column_name = fkc.column_name
WHERE  fk.owner = UPPER(:owner)
AND    fk.constraint_type = 'R'
AND    ( :table_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:table_list), ' ', '') || ',',
                  ',' || fk.table_name || ',') > 0 )
ORDER  BY fk.table_name, fk.constraint_name, fkc.position;
