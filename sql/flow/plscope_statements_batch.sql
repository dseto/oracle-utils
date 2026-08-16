-- Statements SQL embutidos no PL/SQL (PL/Scope 12.2+/19c) de N objetos de um
-- owner numa unica consulta: irma em lote de plscope_statements.sql, usada
-- pela BFS por nivel e pelo enriquecimento em lote (classificacao de SQL
-- dinamico e acesso a tabela).
-- Mesma projecao de plscope_statements.sql MAIS s.object_name (o objeto de
-- ORIGEM de cada linha) -- sem essa coluna nao da para reagrupar os
-- statements por objeto no cliente depois do fetch em lote.
-- Binds: :owner (obrigatorio), :object_list (opcional; lista separada por
--        virgula de nomes de objeto em maiusculas; NULL = todos os objetos
--        do owner).
-- usage_id/usage_context_id (contrato depgraph-granular, T-02): o elo com a
-- arvore de identificadores de plscope_tree_batch.sql -- ALL_STATEMENTS
-- participa da MESMA arvore de contexto PL/Scope (mesmo espaco de
-- usage_id), so que numa view separada. Um statement (INSERT, EXECUTE
-- IMMEDIATE etc.), que nao aparece em ALL_IDENTIFIERS, e atribuido ao
-- subprograma envolvente subindo por usage_context_id exatamente como uma
-- CALL seria.
-- object_type: mesmo motivo de plscope_tree_batch.sql -- PACKAGE e PACKAGE
-- BODY sao arvores separadas que compartilham o mesmo espaco de usage_id;
-- sem object_type na linha nao da para saber a qual das duas um usage_id
-- pertence.
-- Requer PLSCOPE_SETTINGS com STATEMENTS:ALL.
-- Padrao de bind :object_list + INSTR copiado de tab_columns.sql.
-- Compativel 19c: usage_id/usage_context_id existem em ALL_STATEMENTS desde
-- 12.2, dentro da baseline. ALL_STATEMENTS (nao DBA_).
SELECT s.object_name,
       s.line,
       s.type             AS stmt_type,
       s.sql_id,
       s.has_into_record,
       s.text,
       s.object_type,
       s.usage_id,
       s.usage_context_id
FROM   all_statements s
WHERE  s.owner = UPPER(:owner)
AND    ( :object_list IS NULL
         OR INSTR(',' || REPLACE(UPPER(:object_list), ' ', '') || ',',
                  ',' || s.object_name || ',') > 0 )
ORDER  BY s.object_name, s.line;
