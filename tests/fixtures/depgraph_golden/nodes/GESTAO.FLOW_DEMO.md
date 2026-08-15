# GESTAO.FLOW_DEMO
- tipo: PACKAGE | status: VALID | plscope: sim

## Chama (outbound)
- GESTAO.FLOW_DEMO_LOG (CALL)
- SYS.STANDARD (CALL)

## Tabelas acessadas
- W:INSERT L6 -> GESTAO.FLOW_DEMO_LOG (cols: MSG)

## SQL Dinâmico
- L28 [exact] -> GESTAO.FLOW_DEMO_LOG
  trecho (L1-L53):
  ```
  PACKAGE BODY flow_demo AS
  
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
  ```
- L31 [partial] -> GESTAO.FLOW_DEMO_LOG
  trecho (L1-L53):
  ```
  PACKAGE BODY flow_demo AS
  
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
  ```
