# Plano: /plsql-flow determinístico (v2)

## Objetivo

Reescrever o núcleo do /plsql-flow como pacote Python determinístico. LLM sai do
caminho crítico (extração, grafo, mermaid) e fica só onde julgamento é
insubstituível. Resultado: mesma entrada → mesmo diagrama, testável com
`assert grafo == esperado`, sem risco de erro de leitura de dicionário.

## Divisão determinístico vs LLM

| Etapa | Hoje | v2 | Justificativa |
|---|---|---|---|
| Resolver alvo + overloads (ALL_ARGUMENTS) | LLM | **Python** | matching de aridade/tipo é regra |
| Sinônimos (cadeia, PUBLIC, db_link) | LLM | **Python** | iteração mecânica |
| Camada A — PL/Scope (calls + statements) | LLM | **Python** | dados já resolvidos pelo compilador |
| Triggers por DML + timing/evento | LLM | **Python** | join de dicionário |
| Cascata FK (ON DELETE) | LLM | **Python** | join de dicionário |
| Hierarquia de types + OVERRIDING | LLM | **Python** | listar candidatos é determinístico |
| Anti-loop (visited set, recursão, max_depth) | LLM | **Python** | algoritmo BFS/DFS |
| Orçamento de nós + colapso | LLM | **Python** | regra numérica |
| Geração mermaid + legenda | LLM | **Python** | template |
| Estatísticas (nós/arestas/profundidade/%) | LLM | **Python** | contagem |
| SQL dinâmico — constant folding puro (literais + constantes de spec) | LLM | **Python** | concatenação de literais é resolvível por parsing simples |
| SQL dinâmico — string montada em variável | LLM | **LLM (opcional)** | exige análise de fluxo de dados; Python marca `irresoluvel`, LLM só se usuário pedir palpite |
| Camada B — léxica sem PL/Scope | LLM | **Python parcial + LLM residual** | tokenização + filtro por ALL_DEPENDENCIES é mecânico; ambiguidade de escopo restante vai pra LLM com lista fechada de candidatos |
| Narrativa (pontos cegos comentados, recomendações) | LLM | **LLM** | é texto interpretativo |
| Modo dinâmico HPROF (overlay) | LLM | **Python** | join DBMSHP_* × grafo estático |

Cobertura esperada: com PL/Scope disponível (caso recomendado), 100% do grafo
sai determinístico. Sem PL/Scope, Python entrega grafo com nós marcados
`confianca: alta|media|baixa` e LLM só arbitra os `baixa`.

## Arquitetura

```
tools/plsqlflow/                  # pacote Python (novo)
  __init__.py
  cli.py                          # entry: python -m plsqlflow <owner.pkg.proc> [opções]
  db.py                           # conexão python-oracledb thin (19c/21c), fallback DBA_->ALL_
  queries.py                      # SQL embutido = mesmos textos de sql/flow/*.sql (fonte única: lê os .sql)
  extract.py                      # executa queries, devolve dataclasses tipadas
  resolve.py                      # alvo, overloads, sinônimos
  lexical.py                      # camada B: strip comments/literals, tokenizador, candidatos
  dynsql.py                       # constant folding de EXECUTE IMMEDIATE/OPEN FOR/DBMS_SQL
  graph.py                        # BFS com visited set, recursão, triggers, cascade, OO, colapso
  mermaid.py                      # grafo -> flowchart TD + legenda
  report.py                       # JSON de saída: {graph, stats, blind_spots, unresolved}
tests/
  test_plsqlflow_unit.py          # offline: fixtures JSON com resultados de query gravados
  test_plsqlflow_golden.py        # offline: FLOW_DEMO gravado -> mermaid golden file exato
  test_plsqlflow_live.py          # opcional (marker @live): roda contra dev real
```

Pontos de projeto:

- **Fonte única de SQL**: `queries.py` carrega os arquivos `sql/flow/*.sql`
  existentes (não duplica texto). Os `.sql` continuam servindo o fallback
  manual/MCP e os testes de convenção atuais seguem valendo.
- **Saída dupla**: stdout humano (mermaid + tabelas) e `--json` (grafo completo
  serializado) — a skill lê o JSON, a pessoa lê o mermaid.
- **Sem dependências novas obrigatórias**: grafo em dicts puros (networkx
  desnecessário para BFS + visited). `oracledb` já instalado.
- **Compatibilidade 19c**: nenhum SQL novo; mesmos textos já validados.
- **ASCII-only** em qualquer `.ps1`; `.py` pode UTF-8 mas manter mensagens de
  log ASCII para console PS 5.1.

## Conexão do Python ao banco

- `python-oracledb` modo thin (sem Oracle Client). Testado: 4.0.2 instalado.
- Credenciais NUNCA em linha de comando. Resolução em ordem:
  1. `--conn <alias>` → lê `tools/flow-connections.json` (**gitignored**),
     formato `{"dev": {"user": "gestao", "dsn": "localhost:1521/XEPDB1"}}` +
     senha em variável `PLSQLFLOW_PWD_<ALIAS>` ou prompt interativo.
  2. Variáveis `PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN`.
- Guarda de leitura: sessão abre com `SET TRANSACTION READ ONLY`? Não —
  suficiente: o pacote só executa os SELECTs de `queries.py`; nenhum caminho
  de código monta SQL a partir de entrada do usuário (identificadores passam
  por bind ou por validação `^[A-Za-z0-9_$#]+$`).

## Contrato de saída (JSON)

```json
{
  "root": {"owner": "GESTAO", "object": "FLOW_DEMO", "sub": "PROC_A", "overload": null},
  "nodes": [{"id": "...", "kind": "proc|func|table|view|trigger|dynsql|remote|wrapped",
             "confidence": "compiler|lexical|heuristic", "line": 12}],
  "edges": [{"from": "...", "to": "...", "kind": "static|dynamic|trigger|cascade|override|recursion",
             "label": "..."}],
  "stats": {"nodes": 12, "edges": 15, "depth": 5, "pct_plscope": 100},
  "blind_spots": [{"type": "dynsql_unresolved", "at": "GESTAO.FLOW_DEMO:31", "fragment": "v_sql"}],
  "mermaid": "flowchart TD\n..."
}
```

## Nova divisão de trabalho na SKILL.md

1. Skill roda `python -m plsqlflow <alvo> --conn dev --json` (Bash tool).
2. Exit 0 + `blind_spots` vazio → apresentar mermaid + stats direto. Zero LLM no grafo.
3. `blind_spots` não vazio → LLM comenta cada um (é a parte interpretativa) e,
   só se usuário pedir, tenta heurística sobre SQL dinâmico irresoluvel.
4. Sem PL/Scope → script entrega grafo `lexical` com lista `unresolved`;
   LLM arbitra só esses itens (lista fechada, não re-deriva o grafo).
5. Grafo grande → Artifact continua igual (mermaid pronto vem do script).

## Testes (a diferença real da v2)

- **Golden test**: resultados das queries contra FLOW_DEMO gravados como
  fixtures JSON (uma vez, com script auxiliar `--dump-fixtures`). Teste roda
  offline: fixtures → grafo → mermaid, comparado byte a byte com golden file.
  Cobre: recursão mútua marcada, trigger no grafo, overload resolvido por
  assinatura, dinâmico irresoluvel marcado, contagens exatas (12 nós, prof. 5).
- **Unit**: dynsql folding (literal puro, literal+constante, variável),
  lexical tokenizer (comentários, literais, keywords), colapso de orçamento,
  cadeia de sinônimos, validação de identificador.
- **Live (marker `live`, excluído do default)**: contra dev, confere que
  fixtures não apodreceram (mesmas contagens).
- Testes de convenção existentes (22) continuam passando sem alteração.

## Riscos e mitigação

- **Parser léxico PL/SQL incompleto** (camada B): escopo explícito — não é
  parser completo, é tokenizador + filtro por ALL_DEPENDENCIES; o residual vai
  documentado para LLM. Critério de aceite mede só o comportamento do fixture.
- **Senha/rede**: `flow-connections.json` gitignored + senha via env; runtime
  floor do harness continua bloqueando segredos em repo.
- **Python 3.14 local vs 3.9 no pyproject**: código alvo 3.9+ (sem match,
  sem features novas) para não quebrar outras máquinas.

## Fases (candidatas a tarefas de contrato)

- T-01 `db.py` + `queries.py` + `extract.py` (conexão thin, carga dos .sql, dataclasses) — unit com fixtures.
- T-02 `resolve.py` + `graph.py` + anti-loop + triggers/cascade/OO — golden parcial.
- T-03 `dynsql.py` (folding) + `lexical.py` (camada B mecânica) — unit.
- T-04 `mermaid.py` + `report.py` + `cli.py` + `--dump-fixtures` — golden completo byte a byte.
- T-05 SKILL.md v2 (script primeiro, LLM residual) + teste live opcional + evidência e2e contra dev.

## Não-objetivos

- Parser PL/SQL completo (gramática ANTLR) — fora.
- Resolver dispatch OO em runtime — impossível estaticamente; segue candidatos.
- Executar HPROF automaticamente — segue script entregue, confirmação humana.
- UI/visualização além de mermaid/Artifact.
