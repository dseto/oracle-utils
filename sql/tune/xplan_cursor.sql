-- Plano real do cursor em cache, com stats de execucao, outline e binds peeked.
-- Binds: :sql_id (13 chars). child_number NULL = todos os childs.
SELECT plan_table_output
FROM   TABLE(DBMS_XPLAN.DISPLAY_CURSOR(:sql_id, NULL, 'ALLSTATS LAST +OUTLINE +PEEKED_BINDS'));
