-- Fixture FLOW_DEMO para validacao end-to-end da skill /plsql-flow.
-- DDL DE TESTE - rodar apenas em ambiente dev descartavel (XE local).
-- Casos cobertos: recursao mutua (PROC_A<->PROC_B), EXECUTE IMMEDIATE
-- resolvivel (literal) e irresolvivel (variavel), trigger em DML,
-- overload (CALC_OVERLOAD number/varchar2).
-- Binds: nenhum.

ALTER SESSION SET PLSCOPE_SETTINGS = 'IDENTIFIERS:ALL, STATEMENTS:ALL';

CREATE TABLE flow_demo_log (
  id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  msg       VARCHAR2(200),
  criado_em TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE flow_demo_audit (
  id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  log_id    NUMBER,
  acao      VARCHAR2(30),
  criado_em TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE OR REPLACE TRIGGER trg_flow_demo_log
AFTER INSERT ON flow_demo_log
FOR EACH ROW
BEGIN
  INSERT INTO flow_demo_audit (log_id, acao) VALUES (:NEW.id, 'INSERT_LOG');
END;
/

CREATE OR REPLACE PACKAGE flow_demo AS
  PROCEDURE main(p_n IN NUMBER, p_tag IN VARCHAR2);
  PROCEDURE proc_a(p_n IN NUMBER);
  PROCEDURE proc_b(p_n IN NUMBER);
  FUNCTION calc_overload(p_val IN NUMBER) RETURN NUMBER;
  FUNCTION calc_overload(p_val IN VARCHAR2) RETURN NUMBER;
END flow_demo;
/

CREATE OR REPLACE PACKAGE BODY flow_demo AS

  PROCEDURE log_msg(p_msg IN VARCHAR2) IS
  BEGIN
    -- DML estatico: dispara trg_flow_demo_log
    INSERT INTO flow_demo_log (msg) VALUES (p_msg);
  END log_msg;

  PROCEDURE proc_a(p_n IN NUMBER) IS
  BEGIN
    log_msg('proc_a n=' || p_n);
    IF p_n > 0 THEN
      proc_b(p_n - 1);  -- recursao mutua: A -> B -> A
    END IF;
  END proc_a;

  PROCEDURE proc_b(p_n IN NUMBER) IS
  BEGIN
    IF p_n > 0 THEN
      proc_a(p_n - 1);  -- fecha o ciclo B -> A
    END IF;
  END proc_b;

  PROCEDURE run_dynamic(p_tag IN VARCHAR2) IS
    v_sql VARCHAR2(400);
  BEGIN
    -- dinamico RESOLVIVEL: string 100% literal
    EXECUTE IMMEDIATE 'INSERT INTO flow_demo_log (msg) VALUES (''dyn-fixo'')';
    -- dinamico IRRESOLVIVEL: string montada com variavel/parametro
    v_sql := 'INSERT INTO flow_demo_log (msg) VALUES (''' || p_tag || ''')';
    EXECUTE IMMEDIATE v_sql;
  END run_dynamic;

  FUNCTION calc_overload(p_val IN NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN p_val * 2;
  END calc_overload;

  FUNCTION calc_overload(p_val IN VARCHAR2) RETURN NUMBER IS
  BEGIN
    RETURN LENGTH(p_val);
  END calc_overload;

  PROCEDURE main(p_n IN NUMBER, p_tag IN VARCHAR2) IS
    v_x NUMBER;
  BEGIN
    proc_a(p_n);
    run_dynamic(p_tag);
    v_x := calc_overload(p_n);    -- overload NUMBER
    v_x := calc_overload(p_tag);  -- overload VARCHAR2
  END main;

END flow_demo;
/
