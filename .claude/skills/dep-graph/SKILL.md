---
name: dep-graph
description: Gera grafo de dependências (mermaid flowchart) de um objeto Oracle 19c para análise de impacto — quem usa o objeto (o que quebra se eu alterar X) e o que o objeto usa, via *_DEPENDENCIES com profundidade controlada. Usar quando o usuário perguntar "o que quebra se eu alterar/dropar X", "quem usa essa tabela/view/package", "dependências do objeto", "análise de impacto", ou pedir grafo/mapa de dependências.
---

# /dep-graph — grafo de dependências e análise de impacto

## Entrada
- `owner` e `object_name` — obrigatórios.
- Direção (opcional): **dependentes** (quem usa o objeto — default para análise de impacto), **referenciados** (o que o objeto usa), ou ambas.
- `max_depth` (opcional, default 3). Profundidade alta em schemas acoplados explode o grafo — subir só se o usuário pedir.

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). Se MCP indisponível, pedir ao usuário a saída das queries de `sql/viz/` e montar o grafo offline.

**Vai fazer várias perguntas de impacto sobre o mesmo objeto, ou já existe `oracle-graph/` em disco?** Prefira [/oracle-dependency-graph](../oracle-dependency-graph/SKILL.md) — grava o fechamento transitivo uma vez e responde por grep, sem reconsultar o banco a cada pergunta. Esta skill (`/dep-graph`) é para uma olhada visual pontual (1 round-trip, mermaid no chat).

## Fluxo

### 1. Coletar dependências (somente SELECT)
- Impacto (quem usa): [deps_on_me.sql](../../../sql/viz/deps_on_me.sql) — binds `:owner`, `:object_name`, `:max_depth`.
- Consumo (o que usa): [i_depend_on.sql](../../../sql/viz/i_depend_on.sql) — mesmos binds; já filtra SYS/SYSTEM/PUBLIC para reduzir ruído.
- Queries usam `DBA_DEPENDENCIES`; se ORA-00942, trocar por `ALL_DEPENDENCIES` e avisar que só objetos acessíveis aparecem.
- Se o objeto não aparecer em nenhuma linha, confirmar existência/nome em `DBA_OBJECTS` (fallback `ALL_OBJECTS`) antes de concluir "sem dependências".

### 2. Montar o flowchart
- `graph LR` (esquerda→direita). Seta = "depende de", apontando do dependente para o referenciado: `PKG_VENDAS --> TB_PEDIDO`.
- ID do nó: `OWNER_NOME` sanitizado (sem `$`, `#`, `.`); rótulo legível: `NOME<br/>tipo`.
- Objeto raiz com destaque próprio (classe `root`).
- Cores por tipo via `classDef` + `class` (uma classe por tipo presente):
  ```
  classDef root fill:#f96,stroke:#333,stroke-width:2px
  classDef tabela fill:#4a90d9,color:#fff
  classDef vw fill:#7cb342,color:#fff
  classDef pkg fill:#ab47bc,color:#fff
  classDef trg fill:#ef5350,color:#fff
  classDef outro fill:#90a4ae,color:#fff
  ```
  TABLE→`tabela`, VIEW/MATERIALIZED VIEW→`vw`, PACKAGE/PACKAGE BODY/PROCEDURE/FUNCTION→`pkg`, TRIGGER→`trg`, demais→`outro`.
- Deduplicar nós e arestas (o CONNECT BY pode retornar o mesmo par por caminhos diferentes).
- **Limite de legibilidade**: acima de ~40 nós, cortar em profundidade menor ou agrupar por owner/prefixo e avisar; oferecer a lista completa em tabela.

### 3. Saída
```
## Impacto: <OWNER.OBJETO>
<bloco mermaid>
**Diretos** (nível 1): N objetos — <lista>
**Indiretos** (níveis 2..max): N objetos
**Atenção**: <triggers, jobs, views materializadas no caminho — itens que exigem recompilação/refresh>
```
- Sempre avisar: **dependências dinâmicas não aparecem** — SQL dinâmico (`EXECUTE IMMEDIATE`, `DBMS_SQL`), referências em jobs externos, aplicações cliente e database links não são registrados em `*_DEPENDENCIES`. O grafo é o piso do impacto, não o teto.

## Regras
- Somente SELECT em views de dicionário. Nenhum DDL/DML; nenhuma recompilação automática.
- Compatibilidade 19c: `CONNECT BY NOCYCLE` padrão, sem sintaxe 21c+.
