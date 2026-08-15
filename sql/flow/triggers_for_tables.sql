-- Triggers habilitados sobre uma lista de tabelas/views, com evento e timing.
-- Usado apos detectar DML estatico (ou dinamico resolvido) sobre a tabela.
-- Cobre INSTEAD OF (views). Cruzar triggering_event com o DML detectado.
-- Binds: :owner (owner das tabelas), :table_list (lista separada por virgula)
SELECT t.table_owner,
       t.table_name,
       t.trigger_name,
       t.trigger_type,
       t.triggering_event,
       t.when_clause,
       t.status
FROM   all_triggers t
WHERE  t.table_owner = UPPER(:owner)
AND    INSTR(',' || REPLACE(UPPER(:table_list), ' ', '') || ',',
             ',' || t.table_name || ',') > 0
AND    t.status = 'ENABLED'
ORDER  BY t.table_name, t.trigger_name;
