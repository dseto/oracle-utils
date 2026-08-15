---
name: plsql-review
description: Faz review de código PL/SQL (arquivo local, texto colado ou objeto no banco via all_source) com checklist de segurança, robustez, performance e manutenibilidade — cada finding com severidade e correção sugerida. Usar quando o usuário pedir "review", "revisar", "auditar" ou "analisar" um package/procedure/function/trigger PL/SQL, ou perguntar se um código PL/SQL está seguro/correto/performático.
---

# /plsql-review — review de código PL/SQL

## Entrada
Uma das três fontes:
- **Arquivo local**: path de `.sql`/`.pks`/`.pkb`/`.prc` — ler direto.
- **Texto colado**: código na conversa.
- **Objeto no banco**: owner + nome — buscar via MCP (conexão `dev`), só SELECT:
```sql
SELECT type, line, text
  FROM all_source
 WHERE owner = UPPER(:owner)
   AND name  = UPPER(:name)
 ORDER BY DECODE(type, 'PACKAGE', 1, 'PACKAGE BODY', 2, 3), line
```
Se `all_source` retornar vazio: objeto não existe, sem privilégio, ou é wrapped — informar e parar.

Baseline **Oracle 19c**: nenhuma correção sugerida pode usar sintaxe/feature 21c+ (SQL macros, JS MLE, iterators de FOR LOOP 21c).

## Fluxo

### 1. Obter o código
Resolver a fonte acima. Guardar mapeamento linha → conteúdo (o número de linha do finding vem de `all_source.line` ou da linha do arquivo).

### 2. Rodar o checklist por categoria
Percorrer TODAS as categorias abaixo. Cada finding recebe severidade (CRÍTICO / ALTO / MÉDIO / BAIXO) e correção concreta.

**a) SEGURANÇA**
| Verificar | Severidade típica | Correção |
|---|---|---|
| Concatenação de entrada em `EXECUTE IMMEDIATE`/`DBMS_SQL` sem bind nem `DBMS_ASSERT` | CRÍTICO | bind variables; identificadores via `DBMS_ASSERT.SIMPLE_SQL_NAME`/`ENQUOTE_NAME` |
| `AUTHID DEFINER` (default) onde `AUTHID CURRENT_USER` bastaria | ALTO | avaliar invoker rights; documentar se definer é intencional |
| Grants excessivos referenciados/exigidos pelo código (`ANY`, `DBA`) | ALTO | privilégio mínimo; grants por objeto |
| Senha/credencial hardcoded em string | CRÍTICO | externalizar (wallet, tabela segura); nunca em código |

**b) ROBUSTEZ**
| Verificar | Severidade típica | Correção |
|---|---|---|
| `WHEN OTHERS` sem `RAISE`/log (engole erro) | CRÍTICO | logar + `RAISE;` ou remover o handler |
| Handler sem contexto do erro original | ALTO | `DBMS_UTILITY.FORMAT_ERROR_BACKTRACE` + `FORMAT_ERROR_STACK` no log |
| `COMMIT` dentro de loop | ALTO | commit único ao fim; quem controla transação é o chamador |
| `PRAGMA AUTONOMOUS_TRANSACTION` fora de log/auditoria | ALTO | restringir a logging; nunca para "resolver" mutating table ou driblar transação |

**c) PERFORMANCE**
| Verificar | Severidade típica | Correção |
|---|---|---|
| Cursor row-by-row onde cabe conjunto | ALTO | `BULK COLLECT` com `LIMIT` (500–1000) + `FORALL` |
| SELECT dentro de loop (N+1) | ALTO | mover para JOIN na query principal ou carga prévia em collection |
| Função aplicada a coluna indexada no WHERE | MÉDIO | reescrever predicado sobre a coluna crua; último caso, índice function-based |
| Lookup estável e frequente sem `RESULT_CACHE` | MÉDIO | `FUNCTION ... RESULT_CACHE` (avaliar volatilidade dos dados) |
| Parâmetro OUT/IN OUT grande (collection, LOB, record) sem `NOCOPY` | BAIXO | adicionar `NOCOPY` (documentar semântica em caso de exceção) |
| GTT usada onde collection em memória basta (ou o inverso: collection gigante) | MÉDIO | volume pequeno → collection; volume grande/precisa de SQL → GTT |

**d) MANUTENIBILIDADE**
| Verificar | Severidade típica | Correção |
|---|---|---|
| Spec expõe mais que a API pública (helpers na spec) | MÉDIO | mover privados para o body; spec enxuta |
| Variáveis globais de package mutáveis | MÉDIO | encapsular em getters/setters ou passar por parâmetro; atenção a estado entre chamadas |
| Magic numbers/strings | BAIXO | constantes nomeadas na spec ou body |
| Código morto (procedure nunca chamada, branch impossível) | BAIXO | remover; se dúvida, checar dependências em `all_dependencies` |
| Tipos hardcoded onde cabe `%TYPE`/`%ROWTYPE` | MÉDIO | `tabela.coluna%TYPE` / `tabela%ROWTYPE` |

### 3. Saída
Resumo no topo, depois uma linha por finding, ordenado por severidade:
```
## Review: <arquivo ou OWNER.OBJETO>
Total: N findings — X CRÍTICO, Y ALTO, Z MÉDIO, W BAIXO

pkg_vendas.pkb:142 [CRÍTICO] EXECUTE IMMEDIATE concatena p_tabela sem DBMS_ASSERT → usar DBMS_ASSERT.SIMPLE_SQL_NAME(p_tabela)
pkg_vendas.pkb:87  [ALTO] WHEN OTHERS THEN NULL engole erro → logar backtrace e RAISE
...
```
Fechar com no máximo 3 recomendações priorizadas (o que corrigir primeiro e por quê).

## Regras
- Leitura apenas: `all_source` e views de apoio (`all_dependencies`, `all_procedures`) via SELECT. Nunca compilar, criar ou alterar objeto.
- Correções são entregues como sugestão/trecho de código para o usuário aplicar — nunca aplicadas no banco.
- Sem finding inventado: se uma categoria não tem problema, dizer "sem findings" nela.
- Código wrapped ou parcial: revisar o que é visível e declarar a limitação.
