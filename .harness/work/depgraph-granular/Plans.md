# Plans: depgraph-granular

Desenho de referencia: docs/plano-depgraph-granular.md (aprovado).

Ondas de execucao: T-01/T-02/T-06 em paralelo (arquivos disjuntos) -> T-03 ->
T-04/T-05/T-07 em paralelo -> T-08. Os modulos foram separados de proposito
para que as tarefas da mesma onda nunca escrevam no mesmo arquivo.

## [T-01] Cada chamada e cada comando SQL passa a ser atribuido ao subprograma exato que o executa, incluindo subprograma aninhado

- files: `plsqlflow/attribute.py`, `tests/test_attribute.py`, `tests/fixtures/plscope_tree.json`
- verify: `pytest tests/test_attribute.py -q`

Modulo puro (sem banco). Monta a arvore de contexto PL/Scope
(`usage_id`/`usage_context_id`, identifiers UNIAO statements) e resolve, para
cada CALL e cada statement, o `DEFINITION` de PROCEDURE/FUNCTION mais proximo
subindo a arvore. Entrega tambem a resolucao do DESTINO de cada CALL por
`signature` (o que permite atravessar packages depois).

Regras obrigatorias: subprograma aninhado vira caminho completo
(`OUTER.INNER`), nunca fundido no pai; sobrecarga recebe sufixo `#<n>`
deterministico (posicao da signature ordenada por linha de declaracao) e
carrega a `signature` como campo; o que nao tem subprograma envolvente pousa
em `__INIT__` (bloco de inicializacao) ou `__SPEC__` (declaracao de nivel de
package) -- NUNCA e descartado.

Golden do teste: as 11 atribuicoes da secao 2 de docs/plano-depgraph-granular.md
(fixture sintetica reproduzindo GESTAO.FLOW_DEMO), incluindo as duas
sobrecargas de CALC_OVERLOAD resolvidas por signature distinta.

## [T-02] As consultas ao dicionario passam a trazer os dados que permitem a atribuicao por subprograma

- files: `sql/flow/plscope_tree_batch.sql`, `sql/flow/plscope_statements_batch.sql`, `plsqlflow/extract.py`, `tests/test_extract_plscope_tree.py`, `plsqlflow/queries.py`
- verify: `pytest tests/test_extract_plscope_tree.py -q`

`plscope_tree_batch.sql` (novo): arvore crua de ALL_IDENTIFIERS para N objetos
de um owner numa chamada -- projecao `object_name, object_type, usage_id,
usage_context_id, line, col, name, type, usage, signature`. Mesmo padrao de
bind `:owner` + `:object_list` com INSTR ja usado por
`plscope_statements_batch.sql`/`tab_columns.sql`.

`plscope_statements_batch.sql` (editado): acrescentar `usage_id` e
`usage_context_id` a projecao existente -- e o que liga o statement a arvore
de identificadores. Nao remover nenhuma coluna atual.

`extract.py`: dataclass da nova linha + funcao de fetch, seguindo o padrao dos
`fetch_*_batch` existentes e reusando `chunk_names`.

Compatibilidade 19c obrigatoria: `usage_id`/`usage_context_id`/`signature`
existem desde 11g em ALL_IDENTIFIERS e desde 12.2 em ALL_STATEMENTS. Usar
`ALL_` (nunca `DBA_`), sem sintaxe 21c+.

## [T-03] O mapa passa a ser gerado a partir de uma procedure especifica, descendo recursivamente por todas as chamadas ate o fim do processo, mesmo havendo referencia circular

- files: `plsqlflow/procgraph.py`, `tests/test_procgraph_bfs.py`, `tests/fixtures/procgraph_demo.json`
- verify: `pytest tests/test_procgraph_bfs.py -q`
- depends: T-01, T-02

Motor novo de travessia cuja fila carrega SUBPROGRAMAS. Semeadura: raiz de 3
partes (`owner.objeto.subprograma`) enfileira aquele subprograma; raiz de 2
partes enfileira todos os subprogramas publicos da spec. Na primeira visita a
um OBJETO, puxa a arvore PL/Scope inteira dele de uma vez (cacheada) e usa
T-01 para atribuir; cada CALL atribuida resolve o alvo por `signature` e
enfileira `(owner, objeto, subprograma)` exato -- e assim a travessia
atravessa packages.

Profundidade total por default (sem cap implicito); `--max-depth`/
`--max-objects` ficam opt-in e, se acionados, registram truncamento item a
item em `not_expanded`.

TERMINACAO (requisito duro): visited-set GLOBAL chaveado pela identidade do no
e marcado no ENQUEUE, nunca no processamento. Reencontro com no ja visto gera
somente a aresta (o ciclo fica visivel no grafo) e nao reenfileira. O motor
deve expor o seam que T-05 e T-08 consomem: chamada a
`procgraph_access.expand_access(...)` (T-03 entrega stub devolvendo lista
vazia) e o `__INIT__` do package entrando na fila junto com o primeiro
subprograma alcancado daquele package.

Prova: golden da cadeia alcancavel a partir de FLOW_DEMO.MAIN com ZERO aresta
falsa (MAIN nao pode aparecer chamando LENGTH nem escrevendo em
FLOW_DEMO_LOG), e fixture com CALL mutua entre dois packages que precisa
TERMINAR sob timeout curto do teste, com o ciclo presente no grafo.

## [T-04] Um objeto sem PL/Scope no meio da cadeia deixa de ser um buraco no mapa: a travessia continua atraves dele, com o motivo declarado

- files: `plsqlflow/procgraph.py`, `tests/test_procgraph_fallback.py`
- verify: `pytest tests/test_procgraph_fallback.py -q`
- depends: T-03

Quando a cadeia alcanca objeto PL/SQL sem PL/Scope utilizavel
(IDENTIFIERS e/ou STATEMENTS ausentes) ou wrapped, a atribuicao por
subprograma e impossivel -- mas a travessia nao para e nao omite: o objeto e
expandido em grao OBJETO reusando `depgraph._DepGraphEngine` (nao
reimplementar fronteira/sinonimo/db_link/caps), o no fica marcado
`grain: object` com o motivo, e tudo que ele alcanca continua na travessia.

O `_DepGraphEngine` reusado precisa COMPARTILHAR o visited-set do motor fino:
objeto ja visto pela travessia fina nao pode ser reexpandido pelo fallback, e
vice-versa -- essa e a mesma invariante de terminacao do T-03.

Prova (anti-omissao): fixture com cadeia A(com PL/Scope) -> B(sem PL/Scope) ->
C(com PL/Scope); C TEM que aparecer no grafo, e B tem que estar marcado com o
motivo. Objeto recompilavel entra na lista de recompilacao.

## [T-05] O mapa passa a mostrar, por subprograma, quais tabelas e colunas le e escreve, qual estado de package compartilha e quais triggers dispara

- files: `plsqlflow/procgraph_access.py`, `tests/test_procgraph_access.py`
- verify: `pytest tests/test_procgraph_access.py -q`
- depends: T-03

Implementa o seam `expand_access(...)` que T-03 deixou como stub. Tres
entregas:

1. READ/WRITE por subprograma com COLUNAS VINDAS DO COMPILADOR
   (identificadores de tipo COLUMN atribuidos ao statement, que por sua vez
   esta atribuido ao subprograma), substituindo a extracao por regex de
   `depgraph_enrich._extract_write_cols` neste caminho. Nao apagar a funcao
   antiga: o modo nao-granular continua usando-a.
2. Estado de package: variavel/constante/cursor cujo walk termina no PACKAGE
   (e nao num subprograma) e estado compartilhado -- vira no sintetico
   `__STATE__` com arestas STATE_READ/STATE_WRITE dizendo quais subprogramas
   leem e quais escrevem.
3. Trigger de tabela escrita entra na travessia COMO SUBPROGRAMA (trigger e
   objeto PL/Scope), nao como folha.

Prova: comparacao contra o resultado do regex na fixture (colunas do
compilador precisam ser iguais ou mais completas, nunca menos), fixture com
package com estado, e ciclo de trigger entre duas tabelas (T1 -> TRG1 -> T2 ->
TRG2 -> T1) que precisa TERMINAR com o ciclo declarado.

## [T-06] Objeto compilado pela metade deixa de ser reportado como coberto: falta de STATEMENTS:ALL passa a aparecer como ponto cego

- files: `plsqlflow/depgraph.py`, `tests/test_depgraph_plscope_settings.py`, `plsqlflow/depgraph_render.py`
- verify: `pytest tests/test_depgraph_plscope_settings.py -q`

Correcao do defeito de omissao silenciosa: `_DepGraphEngine.has_plscope`
(plsqlflow/depgraph.py:439-447) devolve True vendo so `IDENTIFIERS:ALL`. Um
objeto assim tem ALL_STATEMENTS vazio, entao todo acesso a tabela e todo SQL
dinamico dele somem do grafo sem entrar em PONTOS CEGOS nem em
needs_recompile -- exatamente o modo de falha que este contrato existe para
impedir.

Reportar as duas capacidades SEPARADAS (identifiers / statements). Objeto sem
STATEMENTS:ALL entra em needs_recompile e em PONTOS CEGOS. Tarefa
independente das demais -- pode landar primeiro.

Prova: teste de regressao em que objeto so com IDENTIFIERS:ALL NAO pode sair
classificado como coberto.

## [T-07] Toda referencia circular do processo aparece nomeada no mapa, em vez de virar um marcador solto de aresta

- files: `plsqlflow/procgraph_cycles.py`, `tests/test_procgraph_cycles.py`
- verify: `pytest tests/test_procgraph_cycles.py -q`
- depends: T-03

Funcao pura de deteccao de componentes fortemente conexos (Tarjan) sobre as
arestas do grafo de subprogramas, devolvendo os grupos de forma
DETERMINISTICA (ordenacao estavel dos grupos e dos membros -- duas execucoes
com a mesma entrada dao a mesma saida). Para migracao, um ciclo inter-package
e acoplamento que decide fronteira de servico: precisa aparecer nomeado, nao
sumir.

Cobre recursao direta, recursao mutua entre subprogramas do mesmo package,
ciclo atravessando packages e ciclo de trigger. Prova com a recursao mutua
PROC_A/PROC_B ja existente na fixture mais um ciclo inter-package.

## [T-08] O mapa passa a declarar o que cobriu e o que nao cobriu, com contagem que fecha, e o modo antigo continua intacto

- files: `plsqlflow/procgraph_render.py`, `plsqlflow/cli.py`, `tests/test_procgraph_render.py`, `tests/test_cli_granular.py`
- verify: `pytest tests/test_procgraph_render.py tests/test_cli_granular.py -q`
- depends: T-03, T-04, T-05, T-07

Saida em disco no grao subprograma (um .md por subprograma, edges.jsonl,
meta.json, INDEX.md), mais duas secoes novas no INDEX: `## COBERTURA` e
`## Ciclos` (consumindo T-07).

COBERTURA e obrigacao de prova, nao relatorio:

```
objetos alcancados = grao subprograma + grao objeto (por motivo) + folhas de fronteira
calls por objeto   = atribuidos a subprograma + atribuidos a __INIT__/__SPEC__ + nao atribuidos
statements idem
```

Soma que nao fecha e ERRO, nao aviso -- o grafo nao e gravado como valido.

CLI: `--granular` liga o modo; alvo de 3 partes (`owner.objeto.subprograma`)
passa a ser valido QUANDO `--granular` estiver ligado; alvo de 2 partes
semeia a spec inteira. Sem a flag, o parsing e a saida ficam IDENTICOS aos
atuais (alvo de 3 partes continua sendo erro no modo objeto).

Mermaid nao entra por default (centenas de nos = ilegivel); se implementado,
so atras de flag e por particao.

Prova inclui o teste de nao-regressao: sem `--granular`, saida byte a byte
igual a atual.
