# Claude Progress

Contrato: `oracle-depgraph`

_Demanda ENCERRADA por `harness finish`._

## Features

| id | desc | status |
| --- | --- | --- |
| T-01 | Catalogo de objetos e colunas de tabela ficam disponiveis ao extrator, com data de ultimo DDL para detectar grafo desatualizado | done |
| T-02 | Percorrer as dependencias a partir da raiz visita cada objeto uma unica vez, para nas fronteiras do sistema e avisa quando atinge o limite de tamanho em vez de truncar calado | done |
| T-03 | Cada acesso a tabela vira aresta de leitura ou escrita com linha do fonte, e todo SQL dinamico aparece classificado como resolvido, parcial ou opaco | done |
| T-04 | Triggers das tabelas escritas entram no grafo com suas proprias dependencias, e regerar o grafo contra o mesmo banco produz arquivos identicos byte a byte | done |
| T-05 | Gerar o grafo pela linha de comando funciona sem quebrar o uso atual do pacote, nunca recebe senha por argumento e sinaliza cada situacao com um codigo de saida proprio | done |
| T-06 | O assistente sabe consultar o grafo por grep antes de reabrir conexao, e as skills e agentes existentes apontam para ele | done |

## Última atualização

_(vazio — demanda encerrada; o próximo `compile-session` regenera este arquivo a partir do contrato novo. A prova do que foi entregue está em `.harness/evidence/`.)_
