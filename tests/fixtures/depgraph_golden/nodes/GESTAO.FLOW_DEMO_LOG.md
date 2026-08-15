# GESTAO.FLOW_DEMO_LOG
- tipo: TABLE | status: VALID | plscope: não

## Chamado por (inbound)
- GESTAO.FLOW_DEMO (CALL)
- GESTAO.TRG_FLOW_DEMO_LOG (CALL)

## Tabelas acessadas
- W:INSERT L6 <- GESTAO.FLOW_DEMO (cols: MSG)

## Colunas
- ID NUMBER NOT NULL
- MSG VARCHAR2(200) NULL
- CRIADO_EM TIMESTAMP(6) NULL

## Triggers ativados
- GESTAO.TRG_FLOW_DEMO_LOG evento:INSERT status:ENABLED
