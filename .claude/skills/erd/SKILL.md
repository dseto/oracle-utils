---
name: erd
description: Gera diagrama ER (mermaid erDiagram) de um schema Oracle 19c ou de uma lista de tabelas — entidades com colunas/tipos, PK/UK marcadas e relacionamentos com cardinalidade derivada das FKs. Usar quando o usuário pedir "diagrama ER", "ERD", "modelo de dados", "desenhar as tabelas", "como as tabelas se relacionam", ou quiser visualizar a estrutura de um schema/módulo.
---

# /erd — diagrama ER de schema ou tabelas

## Entrada
- `owner` (schema) — obrigatório.
- Lista de tabelas (opcional). Sem lista = todas as tabelas do owner.

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). Se MCP indisponível, pedir ao usuário a saída das queries de `sql/viz/` e montar o diagrama offline.

## Fluxo

### 1. Coletar metadados (somente SELECT)
- [erd_tables.sql](../../../sql/viz/erd_tables.sql) — colunas, tipos, nullable (binds `:owner`, `:table_list` opcional).
- [erd_pk_uk.sql](../../../sql/viz/erd_pk_uk.sql) — constraints P/U com colunas em ordem.
- [erd_fks.sql](../../../sql/viz/erd_fks.sql) — FKs com tabela pai resolvida, colunas dos dois lados e nullable da coluna FK.
- Queries usam `DBA_`; se ORA-00942, trocar por `ALL_` e avisar que a visão pode estar limitada aos objetos acessíveis.

### 2. Dimensionar o diagrama
- Contar tabelas retornadas. **Limite: ~20 tabelas por diagrama.**
- Se mais de ~20: agrupar por prefixo/módulo (ex.: `FIN_%`, `EST_%`) e gerar um diagrama por grupo, mais um diagrama de visão geral só com as tabelas "hub" (mais referenciadas). Confirmar o agrupamento com o usuário se os prefixos não forem óbvios.
- Tabelas sem FK de/para o conjunto: listar à parte como "isoladas" (não poluir o diagrama).

### 3. Montar o erDiagram
Regras de montagem:
- **Entidade** = tabela; atributos com tipo resumido aceito pelo mermaid (sem parênteses/vírgulas): `VARCHAR2(50)` → `varchar2_50`, `NUMBER(10,2)` → `number_10_2`, `NUMBER` → `number`, `DATE` → `date`, `TIMESTAMP(6)` → `timestamp`. Marcar `PK`, `UK`, `FK` após o tipo.
- Em tabelas largas (>15 colunas), mostrar só PK, FKs e colunas de negócio relevantes; anotar "+N colunas" no texto.
- **Relacionamento** = uma linha por FK, no sentido pai → filho, rótulo = nome da FK ou verbo curto:
  - FK NOT NULL: `PAI ||--o{ FILHO : "fk_nome"` (todo filho tem pai).
  - FK nullable (`child_col_nullable = 'Y'` em qualquer coluna da FK): `PAI |o--o{ FILHO : "fk_nome"` (relacionamento opcional).
  - FK cujas colunas são também PK/UK do filho (1:1): `PAI ||--|| FILHO` (ou `|o--||` se nullable).
- Auto-relacionamento (FK para a própria tabela): representar normalmente com rótulo tipo "pai de".

### 4. Saída
- Bloco ```mermaid inline no chat com o(s) diagrama(s).
- Resumo textual: nº de tabelas, nº de FKs, tabelas isoladas, FKs desabilitadas (se houver).
- Para schemas grandes (múltiplos diagramas) ou se o usuário quiser compartilhar: oferecer Artifact HTML — artifacts renderizam mermaid nativamente via `<pre class="mermaid">`, um bloco por diagrama, com títulos por módulo. Single-file, sem libs externas.

## Regras
- Somente SELECT em views de dicionário. Nenhum DDL/DML.
- Diagrama reflete constraints declaradas: FKs não declaradas no banco (garantidas só pela aplicação) não aparecem — avisar quando houver colunas `*_ID` sem FK correspondente.
- Compatibilidade 19c: as queries não usam sintaxe 21c+.
