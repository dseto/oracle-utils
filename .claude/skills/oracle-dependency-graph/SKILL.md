---
name: oracle-dependency-graph
description: Materializa em disco o fechamento transitivo de dependências de um objeto Oracle 19c (grão OBJETO — BFS sobre ALL_DEPENDENCIES, enriquecido com PL/Scope, triggers e SQL dinâmico classificado) para o Claude Code consultar depois por grep, sem reabrir conexão a cada pergunta. Usar quando o usuário pedir para "gerar/persistir o grafo de dependências", "montar o mapa completo de X para consultar depois", antes de uma sessão longa de perguntas repetidas sobre impacto/uso de um objeto, ou quando outra skill (dep-graph, plsql-flow, plsql-review, oracle-dba) precisar responder "quem usa X" / "quem escreve na tabela Y" e um grafo em `oracle-graph/` já existir ou valer a pena gerar.
---

# /oracle-dependency-graph — grafo de dependências persistido em disco

## Objetivo

As demais skills de dependência (`/dep-graph`, `/plsql-flow`) reconsultam o
banco a cada pergunta e devolvem uma resposta efêmera (mermaid no chat).
Esta skill faz o oposto: roda a extração **uma única vez** por objeto-raiz e
grava o fechamento transitivo completo em `oracle-graph/<OWNER>.<RAIZ>/` —
um arquivo por nó, um arquivo de arestas, um índice. Depois disso, "quem usa
X" ou "quem escreve na tabela Y" vira um `grep` no disco, sem SQL, sem
conexão, sem gastar contexto reabrindo o banco a cada pergunta da mesma
sessão (ou de sessões futuras, enquanto o grafo não estiver desatualizado —
ver "Quando regenerar" abaixo).

## Quando usar esta skill em vez de outra (resumo)

| Skill | Fonte | Escopo | Saída | Consumidor |
|---|---|---|---|---|
| `/dep-graph` | `*_DEPENDENCIES`, 1 round-trip | profundidade baixa | mermaid no chat | Humano, olhar rápido |
| `/plsql-flow` | PL/Scope por **subprograma** | 1 cadeia de execução | mermaid + JSON | Humano + LLM (residual) |
| `/oracle-dependency-graph` (esta) | BFS `ALL_DEPENDENCIES` + PL/Scope + triggers + SQL dinâmico, grão **objeto** | fechamento transitivo completo | `nodes/*.md` + `edges.jsonl` + `INDEX.md` em disco | **Claude Code via grep**, sem reconsultar banco |

Regra prática: pergunta pontual/visual → `/dep-graph` ou `/plsql-flow`.
Vai precisar responder várias perguntas de impacto sobre o mesmo objeto (ou
outra skill/agente vai precisar disso) → gere o grafo aqui uma vez e
consuma por grep depois. Detalhe completo em
[docs/plano-oracle-dependency-graph.md](../../../docs/plano-oracle-dependency-graph.md)
(seção 1).

## Como gerar

```
python -m plsqlflow depgraph OWNER.OBJETO --conn <alias>
```

Subcomando do pacote `plsqlflow/` já usado por `/plsql-flow` (mesma conexão,
mesmo mecanismo de credencial). **Senha nunca em argumento de linha de
comando** — `--conn <alias>` lê `tools/flow-connections.json` (gitignored) e
a senha vem de `PLSQLFLOW_PWD_<ALIAS>`; sem `--conn`, usa as variáveis de
ambiente diretas `PLSQLFLOW_USER`/`PLSQLFLOW_PWD`/`PLSQLFLOW_DSN`. O alvo é
**sempre `owner.objeto`, sem subprograma** — o grão aqui é o objeto, não a
chamada individual (diferente do modo flow).

Flags (todas opcionais, default entre parênteses):
- `--output DIR` (`./oracle-graph`) — diretório base; o grafo é gravado em
  `<output>/<OWNER>.<OBJETO>/`.
- `--stop-schemas LISTA` (`SYS,SYSTEM`) — schemas de fronteira, separados
  por vírgula; viram nó folha sem expansão.
- `--dynamic-window N` (`30`) — linhas de `ALL_SOURCE` ao redor de cada
  ocorrência de SQL dinâmico, para o trecho embutido no node `.md`.
- `--max-objects N` (`500`) e `--max-depth N` (`20`) — caps de segurança da
  BFS; estouro nunca trunca em silêncio, vira aviso + seção de truncamento
  no `INDEX.md`.

Exit codes (não usar 2 — reservado ao erro de uso do `argparse`):

| código | significado | grafo foi gravado? |
|---|---|---|
| 0 | sucesso | sim, completo |
| 3 | há objeto(s) sem PL/Scope disponível — `recompile.sql` gerado | sim, **parcial** (não é falha) |
| 4 | raiz `owner.objeto` inexistente/inválida (0, 1 ou 3+ partes; ou não encontrada em `ALL_OBJECTS`) | não |
| 5 | erro de conexão (`db.ConnectionConfigError` ou `oracledb.Error`) | não |

Exit 3 não é erro operacional: o pipeline é somente-leitura e nunca roda
`recompile.sql` sozinho — ele só entrega o script para o DBA revisar e
decidir (`ALTER ... COMPILE PLSCOPE_SETTINGS='IDENTIFIERS:ALL,
STATEMENTS:ALL'`). Objetos sem PL/Scope entram no grafo mesmo assim, com
`plscope: não` e arestas vindas só de `ALL_DEPENDENCIES` (sem linha).

## Formato da saída

```
oracle-graph/<OWNER>.<RAIZ>/
├── INDEX.md          # raiz, estatísticas, fechamento transitivo, PONTOS CEGOS
├── edges.jsonl        # 1 aresta por linha, ordenada por (from_ref, to_ref, edge_type, line)
├── nodes/
│   └── <OWNER>.<OBJETO>.md   # nome sanitizado: fora de [A-Za-z0-9_] vira "_", tudo maiúsculo
├── recompile.sql      # só existe se houver objeto sem PL/Scope
└── meta.json           # chain_hash, extractor_version, params — SEM timestamp de relógio
```

Rodar duas vezes contra o mesmo estado de banco produz os mesmos bytes em
todo arquivo (`encoding="utf-8"`, `newline="\n"`, ordenação estável) — é
critério de aceite testado (`tests/test_depgraph_render.py`).

### Template do node `.md` (seções na ordem fixa; seção vazia é omitida)

```markdown
# OWNER.NOME
- tipo: <TYPE> | status: VALID|INVALID|UNKNOWN | plscope: sim|não [| trigger_status: ENABLED|DISABLED] [| source: linhas X-Y]

## Chama (outbound)
- OWNER.ALVO (CALL)

## Chamado por (inbound)
- OWNER.ORIGEM (CALL)

## Tabelas acessadas
- W:INSERT L6 -> OWNER.TABELA (cols: COL1, COL2)
- R L12 <- OWNER.OUTRO_OBJETO

## Colunas
- COLUNA TIPO NULL|NOT NULL

## Triggers ativados
- OWNER.TRIGGER evento:INSERT status:ENABLED

## SQL Dinâmico
- L31 [partial] -> OWNER.TABELA
  trecho (L1-L53):
  ```
  ...linhas de ALL_SOURCE ao redor da ocorrência...
  ```
```

`## Tabelas acessadas` mistura outbound (o nó PL/SQL lê/escreve a tabela,
seta `->`) e inbound (a tabela é lida/escrita por outro objeto, seta `<-`)
na mesma seção — arestas ficam duplicadas nos dois nós de propósito
(redundância para grep bidirecional sem join). `## Colunas` só aparece em
nós `TABLE`. `## Triggers ativados` só aparece quando há escrita associada.
`## SQL Dinâmico` só aparece quando o nó tem ocorrência de `EXECUTE
IMMEDIATE`/similar.

## Padrões de grep de consumo

`edges.jsonl` é `json.dumps(..., sort_keys=True)` sem `separators`
customizado — cada linha tem espaço depois de `:` (`"to_ref": "..."`, não
`"to_ref":"..."`). Os nomes de campo REAIS são `from_ref`/`to_ref` (não
`from`/`to`), `edge_type`, `line`, `op`, `cols`, `dynamic`, `confidence`,
`context`, `snippet_ref`. Confira sempre contra
[tests/fixtures/depgraph_golden/edges.jsonl](../../../tests/fixtures/depgraph_golden/edges.jsonl)
se tiver dúvida — é o exemplo real gerado pelo pipeline.

### 1. Localizar um nó
O nome do arquivo já é a chave — não precisa abrir para saber se existe:
```
oracle-graph/<RAIZ>/nodes/OWNER.OBJETO.md
```
Bash/Git Bash: `ls oracle-graph/<RAIZ>/nodes/ | grep -i "OWNER.OBJETO"`
PowerShell: `Get-ChildItem oracle-graph\<RAIZ>\nodes -Filter "OWNER.OBJETO.md"`

### 2. Impacto reverso — "quem usa X" (quem chama/lê/escreve o objeto X)
```
grep '"to_ref": "OWNER.OBJETO"' oracle-graph/<RAIZ>/edges.jsonl
```
Cada linha que casar é uma aresta que aponta PARA `OWNER.OBJETO` —
`from_ref` da linha é quem usa. Equivalente mais legível: abrir
`nodes/OWNER.OBJETO.md` e ler `## Chamado por (inbound)` (chamadas) e as
linhas `<-` de `## Tabelas acessadas` (leituras/escritas).

### 3. Quem escreve na tabela Y
```
grep '"edge_type": "WRITE"' oracle-graph/<RAIZ>/edges.jsonl | grep '"to_ref": "OWNER.TABELA"'
```
Para filtrar por tipo de operação, acrescentar o `op` real
(`INSERT`/`UPDATE`/`DELETE`/`MERGE`):
```
grep '"edge_type": "WRITE"' oracle-graph/<RAIZ>/edges.jsonl | grep '"op": "UPDATE"' | grep '"to_ref": "OWNER.TABELA"'
```
Equivalente: abrir `nodes/OWNER.TABELA.md`, seção `## Tabelas acessadas`,
linhas `W:INSERT`/`W:UPDATE`/`W:DELETE`/`W:MERGE` com seta `<-` (a origem é
quem escreve).

### 4. Quem escreve numa coluna específica
`cols` é best-effort (parse do texto do statement — ausência não é erro,
plano seção 4.7) e vem como lista JSON, ex. `"cols": ["MSG"]`:
```
grep '"edge_type": "WRITE"' oracle-graph/<RAIZ>/edges.jsonl | grep '"cols":' | grep '"COLUNA"'
```
Sempre checar se `cols` veio vazio/ausente na linha antes de concluir que
nenhum escritor toca a coluna — pode ser limitação do parser, não ausência
real (ver seção de honestidade abaixo).

## Regra de honestidade obrigatória (pontos cegos)

Antes de afirmar cobertura ("nada mais usa X", "só esses escrevem em Y"), o
assistente **tem que** abrir a seção `## PONTOS CEGOS` de `INDEX.md`:
- **SQL dinâmico não resolvido** (`confidence: partial` ou `opaque` em
  arestas `DYNAMIC_SQL`) — o grafo capturou um trecho, não uma certeza; abrir
  o source (linha indicada, ou o trecho já embutido na seção `## SQL
  Dinâmico` do node) antes de afirmar o alvo real.
- **Objetos sem PL/Scope** (nó com `plscope: não`) — as arestas desse
  objeto vêm só de `ALL_DEPENDENCIES`, sem linha, sem granularidade de
  READ/WRITE por statement; tratar como piso, não teto.
- **Truncamento** (`### Truncamento` presente) — `not_expanded` lista
  objetos que a BFS não chegou a expandir; o grafo está incompleto por
  desenho (cap de segurança), não é bug.

**Nunca inferir uma dependência ausente do grafo** (ex.: "provavelmente
também usa Z") sem declarar explicitamente que é inferência **fora** do
grafo — o grafo diz onde não enxerga exatamente para isso não acontecer em
silêncio.

## Quando regenerar

`meta.json` não tem timestamp de relógio — tem `chain_hash`: SHA-256 sobre
`(owner, name, type, last_ddl_time)` ordenado de todos os nós. Se o banco
mudar (DDL em qualquer objeto do fechamento transitivo), o `chain_hash`
muda. Antes de confiar num grafo já existente para uma pergunta importante,
comparar o `chain_hash` atual contra uma nova extração é o único jeito
confiável de saber se ele ficou desatualizado — não há heurística de idade
de arquivo.

## Sistemas gigantes (10 a 50 mil objetos)

O caso real que motivou este contrato não é `GESTAO.FLOW_DEMO` (3 objetos):
é um subsistema inteiro. Três mudanças tornam isso viável.

### 1. Extração em lote — por que o grafo grande agora *gera*

Antes, cada objeto novo descoberto pela BFS custava uma ida ao banco
(`deps_direct`, um `SELECT` por objeto) — 20 mil objetos, 20 mil
round-trips, e a latência de rede domina o tempo total. Agora a BFS drena
a fila **um nível inteiro por vez**: agrupa os objetos pendentes por
`owner`, fatia em lotes (`extract.BATCH_CHUNK_SIZE` nomes por vez) e faz
uma única chamada `deps_direct_batch` por owner/lote em vez de uma por
objeto — round-trips caem de O(nós) para O(níveis × owners × lotes). O
mesmo vale para o enriquecimento (`statements`/`source` por owner em vez
de por objeto). Nada disso muda o grafo resultante nem o comando — é
transparente:

```
python -m plsqlflow depgraph GESTAO.PKG_GRANDE --conn dev --max-objects 0
```

### 2. `--index-split` — como ler o `INDEX.md` de um grafo grande

Acima de `--index-split` nós (default `1000`), `INDEX.md` deixa de listar
o fechamento transitivo inteiro (ninguém — nem o assistente — consegue
consumir uma lista de milhares de linhas) e vira um **sumário**:
estatísticas, `## PONTOS CEGOS` (completo, nunca truncado — é a garantia
de honestidade) e uma seção `## Hubs` com os 20 objetos de maior grau
(entrada + saída), o ponto de partida natural para entender um subsistema
desconhecido. O fechamento transitivo completo é particionado por schema
em `INDEX-<OWNER>.md` (um arquivo por owner que aparece no grafo).

Ordem de leitura recomendada para um grafo sumarizado:
1. `INDEX.md` — estatísticas + `## Hubs` (quem concentra as dependências)
   + `## PONTOS CEGOS` (honestidade antes de qualquer afirmação).
2. `INDEX-<OWNER>.md` do(s) schema(s) relevante(s) para a pergunta —
   fechamento completo daquele owner, não do grafo inteiro.
3. `nodes/<OWNER>.<OBJETO>.md` do objeto específico, como sempre.

Abaixo do limiar, o formato de `INDEX.md` não muda em nada — é o mesmo
`INDEX.md` plano de sempre. Ajustar o limiar (grafo pequeno que já merece
sumário, ou grande que ainda cabe numa lista só):

```
python -m plsqlflow depgraph GESTAO.PKG_GRANDE --conn dev --index-split 300
```

### 3. Multi-raiz — grafo único de subsistema

`depgraph` aceita mais de uma raiz — todas alimentam a **mesma** BFS e
saem num grafo único (um objeto alcançado por duas raízes vira um nó só,
não dois). Com mais de uma raiz, `--name` é **obrigatório** (a saída vai
para `<output>/<name>/` em vez de `<output>/<OWNER>.<OBJETO>/`):

```
python -m plsqlflow depgraph GESTAO.PKG_A GESTAO.PKG_B --conn dev --name modulo-financeiro
```

Com uma raiz só, `--name` continua opcional (comportamento de sempre sem
ele: saída em `<output>/<OWNER>.<OBJETO>/`); se informado, também rotula a
saída. `meta.json` registra todas as raízes em `params.roots` (lista), além
de `params.root_ref` (as mesmas raízes, separadas por vírgula, usado no
cabeçalho do `INDEX.md`).

### 4. `--max-objects` ampliado ou desligado

O cap de segurança default subiu de 500 para **5000** — o valor antigo
truncava sistemas reais no meio do fechamento transitivo com muita
frequência. `--max-objects 0` **desliga o cap de quantidade por completo**
(nenhum truncamento por número de objetos); o aviso por `--max-depth`
continua valendo normalmente — os dois caps são independentes. Combinar
com multi-raiz é o caso de uso central desta seção:

```
python -m plsqlflow depgraph GESTAO.PKG_A GESTAO.PKG_B GESTAO.PKG_C --conn dev --name modulo-financeiro --max-objects 0
```

Sem cap de quantidade, quem ainda limita o tamanho do grafo é
`--max-depth` (default `20`) e a fronteira de `--stop-schemas` — vale
revisar os dois antes de rodar contra um subsistema sem noção prévia do
tamanho do fechamento transitivo.

### 5. `oracle-graph/` fora do git

`oracle-graph/` já está no `.gitignore` do repositório — é cache derivado
do banco (regenerável a qualquer momento, ver "Quando regenerar" acima),
não é fonte. Em sistemas gigantes isso importa ainda mais: o volume de
`nodes/*.md` e `edges.jsonl` de um subsistema de milhares de objetos não
tem lugar num commit. Se precisar compartilhar um grafo específico com
outra pessoa/sessão, copie o diretório `oracle-graph/<RAIZ_OU_NOME>/`
manualmente para fora do controle de versão — nunca force-adicione com
`git add -f`.

## Fallback sem Python/`oracledb`

Mesmo padrão das demais skills do repositório: se o MCP do SQLcl e o
ambiente Python (`oracledb`) estiverem indisponíveis, apontar
[scripts/run-query.ps1](../../../scripts/run-query.ps1) para o usuário rodar
as queries de `sql/flow/` manualmente (`object_catalog.sql`,
`deps_direct.sql`, `plscope_calls.sql`, `plscope_statements.sql`,
`triggers_any_status.sql`, `tab_columns.sql`) e montar os arquivos à mão a
partir da saída. **O grafo resultante é parcial** — sem o pipeline
determinístico, não há classificação automática de SQL dinâmico nem
`chain_hash`; marcar isso explicitamente no `INDEX.md` montado manualmente.

## Regras

- Somente leitura: todo o subcomando executa apenas `SELECT` (via
  `db.run_query`); `recompile.sql` é gerado, nunca executado por este
  pipeline.
- Compatibilidade 19c: nenhuma query em `sql/flow/*.sql` usa sintaxe/feature
  21c+.
- Senha/DSN nunca em argumento de linha de comando — só `--conn <alias>` ou
  as variáveis de ambiente `PLSQLFLOW_USER`/`PLSQLFLOW_PWD`/`PLSQLFLOW_DSN`.
