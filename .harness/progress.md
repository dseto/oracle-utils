# Claude Progress

Contrato: `plsql-flow`

_Demanda ENCERRADA por `harness finish`._

## Features

| id | desc | status |
| --- | --- | --- |
| T-01 | DBA consegue consultar toda a base estática do grafo (chamadas, triggers, tipos, sinônimos, overloads) com queries prontas da biblioteca | done |
| T-02 | Usuário invoca /plsql-flow com procedure+parâmetros e recebe diagrama mermaid do caminho completo, com casos complexos marcados e proteção anti-loop | done |
| T-03 | Ambiente dev tem package de demonstração FLOW_DEMO cobrindo recursão mútua, SQL dinâmico, trigger e overload para validar a skill | done |
| T-04 | Usuário pode ativar modo dinâmico (DBMS_HPROF) e ver o caminho realmente executado sobreposto ao grafo estático | done |
| T-05 | Skill validada de ponta a ponta contra FLOW_DEMO: ciclo detectado sem travar, trigger e SQL dinâmico no diagrama, overload resolvido, evidência gravada | done |

## Última atualização

_(vazio — demanda encerrada; o próximo `compile-session` regenera este arquivo a partir do contrato novo. A prova do que foi entregue está em `.harness/evidence/`.)_
