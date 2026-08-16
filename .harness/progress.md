# Claude Progress

Contrato: `depgraph-granular`

_Demanda ENCERRADA por `harness finish`._

## Features

| id | desc | status |
| --- | --- | --- |
| T-01 | Cada chamada e cada comando SQL passa a ser atribuido ao subprograma exato que o executa, incluindo subprograma aninhado | done |
| T-02 | As consultas ao dicionario passam a trazer os dados que permitem a atribuicao por subprograma | done |
| T-03 | O mapa passa a ser gerado a partir de uma procedure especifica, descendo recursivamente por todas as chamadas ate o fim do processo, mesmo havendo referencia circular | done |
| T-04 | Um objeto sem PL/Scope no meio da cadeia deixa de ser um buraco no mapa: a travessia continua atraves dele, com o motivo declarado | done |
| T-05 | O mapa passa a mostrar, por subprograma, quais tabelas e colunas le e escreve, qual estado de package compartilha e quais triggers dispara | done |
| T-06 | Objeto compilado pela metade deixa de ser reportado como coberto: falta de STATEMENTS:ALL passa a aparecer como ponto cego | done |
| T-07 | Toda referencia circular do processo aparece nomeada no mapa, em vez de virar um marcador solto de aresta | done |
| T-08 | O mapa passa a declarar o que cobriu e o que nao cobriu, com contagem que fecha, e o modo antigo continua intacto | done |

## Última atualização

<!-- harness:auto -->
- 2026-08-16T10:13:36.528879+00:00 — T-07 verificado (exit_code 0) — .harness/evidence/depgraph-granular/T-07.json
- 2026-08-16T14:27:12.133303+00:00 — T-04 verificado (exit_code 0) — .harness/evidence/depgraph-granular/T-04.json
- 2026-08-16T14:27:21.714095+00:00 — T-03 verificado (exit_code 0) — .harness/evidence/depgraph-granular/T-03.json
- 2026-08-16T15:17:21.665032+00:00 — T-05 verificado (exit_code 0) — .harness/evidence/depgraph-granular/T-05.json
- 2026-08-16T15:17:27.470587+00:00 — T-08 verificado (exit_code 0) — .harness/evidence/depgraph-granular/T-08.json
<!-- /harness:auto -->


_(vazio — demanda encerrada; o próximo `compile-session` regenera este arquivo a partir do contrato novo. A prova do que foi entregue está em `.harness/evidence/`.)_
