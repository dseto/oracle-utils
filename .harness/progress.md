# Claude Progress

Contrato: `plsqlflow-py`

_Demanda ENCERRADA por `harness finish`._

## Features

| id | desc | status |
| --- | --- | --- |
| T-01 | Pacote conecta no Oracle em modo thin e extrai o dicionario de forma tipada e somente-leitura | done |
| T-02 | Diagrama reflete o caminho real de execucao: recursao marcada sem loop, overload certo, triggers e cascata FK incluidos, subtipos OVERRIDING como candidatos | done |
| T-03 | SQL dinamico com literais e constantes vira aresta resolvida; montado em variavel vira ponto cego explicito; fallback sem PL/Scope entrega candidatos com nivel de confianca | done |
| T-04 | Mesmo alvo gera sempre o mesmo diagrama: CLI produz mermaid+JSON e o resultado do FLOW_DEMO bate byte a byte com o golden file | done |
| T-05 | Skill /plsql-flow passa a usar o script primeiro e reserva o assistente ao residual, com evidencia real contra o banco dev | done |

## Última atualização

_(vazio — demanda encerrada; o próximo `compile-session` regenera este arquivo a partir do contrato novo. A prova do que foi entregue está em `.harness/evidence/`.)_
