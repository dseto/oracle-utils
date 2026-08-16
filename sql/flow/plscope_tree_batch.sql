-- Arvore crua de identificadores PL/Scope (ALL_IDENTIFIERS) de N objetos de
-- um owner numa unica consulta -- fundacao da atribuicao por subprograma
-- (contrato depgraph-granular). usage_id/usage_context_id (usage_id do PAI)
-- e o que permite, no cliente, subir a arvore ate o DEFINITION de
-- PROCEDURE/FUNCTION mais proximo e atribuir cada CALL/statement ao
-- subprograma que o envolve (plsqlflow/attribute.py, T-01). signature
-- resolve o DESTINO de uma CALL (overload e entre objetos), habilitando a
-- travessia recursiva inter-package sem heuristica.
-- Binds: :owner (obrigatorio), :object_list (opcional; lista separada por
--        virgula de nomes de objeto em maiusculas; NULL = todos os objetos
--        do owner).
-- object_type e OBRIGATORIO na projecao (nao so no filtro): PACKAGE e
-- PACKAGE BODY sao arvores SEPARADAS que compartilham o mesmo espaco de
-- usage_id -- sem object_type na linha o cliente nao distingue a qual das
-- duas arvores um usage_id pertence.
-- ALL_STATEMENTS participa da MESMA arvore de contexto (mesmo usage_id/
-- usage_context_id), mas fica numa view separada -- ver
-- plscope_statements_batch.sql, que ja traz usage_id/usage_context_id para o
-- cliente unir as duas fontes.
-- Mesmo padrao de bind :object_list + INSTR ja usado por
-- plscope_statements_batch.sql/tab_columns.sql.
-- Requer objeto compilado com PLSCOPE_SETTINGS contendo IDENTIFIERS:ALL.
-- Compativel 19c: usage_id/usage_context_id/signature existem em
-- ALL_IDENTIFIERS desde 11g. ALL_IDENTIFIERS (nao DBA_).
SELECT i.object_name,
       i.object_type,
       i.usage_id,
       i.usage_context_id,
       i.line,
       i.col,
       i.name,
       i.type,
       i.usage,
       i.signature
FROM   all_identifiers i
WHERE  i.owner = UPPER(:owner)
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || i.object_name || ',') > 0 )
ORDER  BY i.object_name, i.object_type, i.usage_id;
