-- Chaves primarias (P) e unicas (U) das tabelas, com colunas em ordem de posicao.
-- Usado para marcar PK/UK nos atributos do erDiagram.
-- Binds: :owner (obrigatorio), :table_list (opcional; lista separada por virgula; NULL = todas).
-- Fallback ALL_: all_constraints / all_cons_columns (se ORA-00942 em DBA_).
-- Compativel 19c.
SELECT c.table_name,
       c.constraint_name,
       c.constraint_type,
       cc.position,
       cc.column_name
FROM   dba_constraints c
JOIN   dba_cons_columns cc
       ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
WHERE  c.owner = UPPER(:owner)
AND    c.constraint_type IN ('P', 'U')
AND    c.status = 'ENABLED'
AND    ( :table_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:table_list), ' ', '') || ',',
                  ',' || c.table_name || ',') > 0 )
ORDER  BY c.table_name, c.constraint_type, c.constraint_name, cc.position;
