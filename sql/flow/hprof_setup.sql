-- MODO DINAMICO /plsql-flow - setup DBMS_HPROF (hierarchical profiler).
-- SCRIPT ENTREGUE AO USUARIO - a skill NUNCA executa isto sozinha.
-- Modo TABELA (12.2+/19c): START_PROFILING (function) retorna o runid e o
-- STOP_PROFILING grava direto nas tabelas DBMSHP_* - NAO existe ANALYZE por
-- runid; DBMS_HPROF.ANALYZE(location, filename) e so para o modo arquivo
-- de trace (nao usado aqui).
-- Efeitos colaterais: cria tabelas DBMSHP_* no schema do usuario.
-- Binds: nenhum (substituir <usuario> antes de rodar).

-- 1) Como DBA:
-- GRANT EXECUTE ON DBMS_HPROF TO <usuario>;

-- 2) Como o usuario alvo (cria DBMSHP_FUNCTION_INFO, DBMSHP_PARENT_CHILD_INFO, DBMSHP_RUNS):
BEGIN
  DBMS_HPROF.CREATE_TABLES(force_it => TRUE);
END;
/

-- 3) Wrapper de execucao (exemplo - ajustar chamada e parametros):
-- DECLARE
--   l_runid NUMBER;
-- BEGIN
--   l_runid := DBMS_HPROF.START_PROFILING;      -- function: ja retorna o runid
--   <owner>.<package>.<procedure>(<parametros>); -- CHAMADA REAL: efeitos colaterais reais
--   DBMS_HPROF.STOP_PROFILING;                   -- grava nas DBMSHP_* deste runid
--   DBMS_OUTPUT.PUT_LINE('runid=' || l_runid);
-- END;
-- /
-- 4) Consultar o caminho executado: sql/flow/hprof_report.sql com :runid.
