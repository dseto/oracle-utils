---
name: plsql-test
description: Gera testes utPLSQL v3 para package/procedure/function PL/SQL — lê a spec via all_source ou arquivo, identifica subprogramas públicos e produz package ut_<nome> com casos de caminho feliz, NULLs, limites e exceções. Usar quando o usuário pedir "gerar testes", "criar testes unitários", "testar esse package" ou mencionar utPLSQL.
---

# /plsql-test — geração de testes utPLSQL v3

## Entrada
- Alvo: package (preferencial), procedure ou function standalone.
- Fonte da spec: arquivo local fornecido, ou banco via MCP (conexão `dev`), só SELECT:
```sql
SELECT line, text
  FROM all_source
 WHERE owner = UPPER(:owner)
   AND name  = UPPER(:name)
   AND type IN ('PACKAGE', 'PROCEDURE', 'FUNCTION')
 ORDER BY line
```
Complementar assinaturas com `all_arguments` (nome, tipo, IN/OUT, posição, defaults) se a spec for ambígua.

Baseline **Oracle 19c** — o código de teste gerado não pode usar sintaxe 21c+.

## Fluxo

### 1. Verificar se utPLSQL está instalado
```sql
SELECT COUNT(*) AS instalado
  FROM all_objects
 WHERE owner = 'UT3'
   AND object_name = 'UT'
   AND object_type = 'PACKAGE'
```
Fallback (sem privilégio sobre UT3): `SELECT username FROM all_users WHERE username IN ('UT3', 'UT')`.
- **Instalado**: seguir normal.
- **Ausente**: gerar os testes mesmo assim e avisar ao final:
  > utPLSQL v3 não detectado nesta conexão. Instalação: baixar release em https://github.com/utPLSQL/utPLSQL/releases e rodar `install_headless.sql` como usuário privilegiado (cria schema UT3). Docs: https://www.utplsql.org/utPLSQL/latest/

### 2. Mapear a API pública
Da spec: listar procedures/functions públicas com parâmetros, tipos, defaults, e exceções declaradas/documentadas. Overloads viram testes separados (sufixo no nome do teste). Subprogramas só do body (privados) não são testados diretamente — testar via API pública.

### 3. Desenhar os casos
Por subprograma público, no mínimo:
- **Caminho feliz**: entrada típica → resultado esperado.
- **NULLs**: cada parâmetro obrigatório com NULL — comportamento esperado (exceção? default? propaga NULL?).
- **Limites**: string no tamanho máximo, zero, negativo, lista vazia, data limite — conforme os tipos.
- **Exceções esperadas**: `--%throws(-20001)` ou `--%throws(no_data_found)` para cada erro documentado/deduzido.
Se o comportamento esperado não for dedutível da spec, gerar o teste com `ut.fail('TODO: definir resultado esperado')` e comentário explicando a dúvida.

### 4. Gerar o package de teste
Convenções obrigatórias:
- Nome: `ut_<package_alvo>` (spec + body no mesmo script).
- `--%suite(<descrição>)` e `--%suitepath(<schema ou domínio>)` na spec.
- **Um `--%test` por comportamento** — nome do teste descreve o comportamento, não o subprograma (`calcula_juros_retorna_zero_para_principal_nulo`).
- `--%beforeall` para setup caro compartilhado; `--%beforeeach` para dados de teste por caso — **sem COMMIT**: o utPLSQL faz rollback automático (`ut.run` com savepoint); não usar autonomous transaction no setup.
- Asserts com `ut.expect(actual).to_equal(expected)` e variantes (`to_be_null`, `to_be_true`, `to_have_count`, `to_equal` com cursor para comparar conjuntos).
- Dependências não triviais (chamada a outro package, fila, `UTL_HTTP`, sysdate-sensível): isolar em variável/setup com comentário `-- TODO mock:` explicando o que precisa ser simulado e a estratégia sugerida (subtipo de teste, tabela stub, injeção por parâmetro).

### 5. Saída
Script `.sql` completo e autocontido, nesta ordem:
```
-- 1. (comentário) status do utPLSQL na conexão + como instalar se ausente
-- 2. CREATE OR REPLACE PACKAGE ut_<alvo>  (spec com anotações)
-- 3. CREATE OR REPLACE PACKAGE BODY ut_<alvo>
-- 4. Como executar:
--    EXEC ut.run('ut_<alvo>');
--    ou: EXEC ut.run('ut_<alvo>', ut_documentation_reporter());
```
Entregar o script ao usuário (arquivo ou bloco na conversa). Listar em 1 linha por teste o que cada um cobre.

## Regras
- **Nunca executar CREATE/compilação no banco sem confirmação explícita do usuário na conversa.** O padrão é entregar o script; o usuário executa.
- Leituras no banco: só SELECT em `all_source`, `all_arguments`, `all_objects`, `all_users`.
- Não inventar comportamento: dúvida vira `ut.fail('TODO...')` + pergunta ao usuário, não assert chutado.
- Package alvo wrapped: gerar testes só a partir da spec (assinaturas) e avisar que os casos de exceção são incompletos.
