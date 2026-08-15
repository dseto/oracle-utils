-- Binds: :owner (owner das tabelas), :table_list (lista separada por virgula)
-- Triggers (habilitados E desabilitados) sobre uma lista de tabelas/views,
-- com evento, timing e status.
-- Irma de triggers_for_tables.sql, que filtra
-- t.status = 'ENABLED' DE PROPOSITO: aquela query alimenta o diagrama de
-- execucao do plsql-flow (grao subprograma), onde um trigger desabilitado
-- nao dispara em runtime e nao deve aparecer no caminho de execucao.
-- Esta query existe separada porque o grafo de dependencias (plsqlflow
-- depgraph, grao objeto) trata um trigger desabilitado como dependencia
-- ESTRUTURAL real -- alguem pode reabilita-lo a qualquer momento, entao ele
-- entra no grafo como no, com o status registrado (DepNode.trigger_status),
-- em vez de desaparecer da extracao. Ver docs/plano-oracle-dependency-graph.md,
-- secao 4.4.
-- NAO editar triggers_for_tables.sql para remover o filtro: graph.py/report.py
-- (modulos congelados por golden test do contrato plsqlflow-py) dependem do
-- conjunto de linhas ENABLED que ela devolve hoje.
-- Cobre INSTEAD OF (views). Cruzar triggering_event com o DML detectado.
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
ORDER  BY t.table_name, t.trigger_name;
