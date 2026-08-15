# Claude Progress

Contrato: `depgraph-scale`

_Demanda ENCERRADA por `harness finish`._

## Features

| id | desc | status |
| --- | --- | --- |
| T-01 | Percorrer as dependências consulta o banco uma vez por nível da busca em vez de uma vez por objeto, sem mudar o grafo resultante | done |
| T-02 | O limite de tamanho e a fronteira de schema continuam se comportando exatamente como antes quando a busca passa a ser em lote | done |
| T-03 | Classificar acessos a tabela e SQL dinâmico busca fonte e statements em lotes por schema, com o mesmo resultado de antes | done |
| T-04 | Acima de um limiar de nós o índice vira um sumário com os objetos mais conectados, e o fechamento transitivo é separado por schema | done |
| T-05 | O grafo aceita várias raízes num resultado único, o limite de objetos pode ser ampliado ou desligado, e a skill documenta o uso em sistemas gigantes | done |

## Última atualização

_(vazio — demanda encerrada; o próximo `compile-session` regenera este arquivo a partir do contrato novo. A prova do que foi entregue está em `.harness/evidence/`.)_
