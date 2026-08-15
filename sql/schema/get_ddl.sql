-- DDL de um objeto via DBMS_METADATA.GET_DDL, sem clausulas de storage/tablespace,
-- para comparacao textual limpa entre schemas.
-- Leitura pura: SET_TRANSFORM_PARAM afeta apenas a sessao corrente, nada no banco.
-- Binds: :object_type (ex.: TABLE, INDEX, VIEW, PACKAGE), :object_name, :owner
-- Rodar o bloco PL/SQL uma vez por sessao; depois o SELECT quantas vezes precisar.
BEGIN
  DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE',              FALSE);
  DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'TABLESPACE',           FALSE);
  DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES',   FALSE);
  DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR',        TRUE);
  DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'PRETTY',               TRUE);
END;
/

SELECT DBMS_METADATA.GET_DDL(UPPER(:object_type), UPPER(:object_name), UPPER(:owner)) AS ddl
FROM   dual;
