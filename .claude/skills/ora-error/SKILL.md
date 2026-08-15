---
name: ora-error
description: Diagnostica erros ORA-XXXXX do Oracle 19c com causas prováveis ordenadas e queries de verificação executáveis. Usar quando o usuário colar um erro ORA-, perguntar "por que deu esse erro", ou pedir triagem de falha de aplicação/job envolvendo Oracle.
---

# /ora-error — triagem de erros ORA-

## Entrada
- Código do erro (ORA-XXXXX) — obrigatório.
- Contexto: statement que falhou, horário, ambiente (dev/hml), frequência (sempre vs intermitente).
Se faltar contexto crítico ao diagnóstico, perguntar antes de especular.

## Fluxo

### 1. Classificar o erro
Identificar categoria: privilégio/objeto, espaço, concorrência/lock, consistência (undo), PL/SQL runtime, conexão/rede, corrupção, limite de recurso.

### 2. Causas prováveis ORDENADAS
Listar da mais provável para a menos, cada uma com **query de verificação** executável via MCP (só SELECT). Não listar mais de 4 causas.

### 3. Verificar
Rodar as queries de verificação (com permissão implícita — são SELECT) e reduzir às causas confirmadas.

### 4. Saída
```
## ORA-XXXXX: <mensagem oficial>
**Causa confirmada** (ou "mais provável, não confirmável sem X"): ...
**Evidência**: <resultado das queries>
**Correção**: <ação concreta; se envolver DDL/DML/parâmetro, entregar script sem executar>
**Prevenção**: <1-2 linhas>
```

## Playbooks dos erros mais comuns

| Erro | Primeiras verificações |
|---|---|
| ORA-00942 tabela não existe | objeto existe? (`all_objects`), sinônimo? grant? case/aspas no nome? |
| ORA-01555 snapshot too old | undo_retention vs duração da query (`v$undostat` tuned_undoretention), LOBs com pctversion/retention baixo, fetch across commit |
| ORA-01652 temp | `v$tempseg_usage` por sessão, sort/hash gigante = plano ruim (encadear /sql-tune) |
| ORA-01653/01654 espaço em tablespace | `dba_free_space`, autoextend, maxsize |
| ORA-00060 deadlock | trace do deadlock no ADR (pedir ao usuário), grafo de recursos, ordem de update divergente entre transações |
| ORA-00001 unique violada | constraint (`all_constraints`/`all_cons_columns`), duplicata na carga vs corrida de concorrência |
| ORA-04031 shared pool | `v$sgastat`, hard parse excessivo (literais sem bind — `v$sql` force_matching) |
| ORA-01722 invalid number | conversão implícita — coluna VARCHAR2 comparada a número; dado sujo; NLS |
| ORA-06502 PL/SQL numeric/value | overflow de variável, VARCHAR2 pequeno, conversão |
| ORA-12154/12541 TNS | tnsnames/alias, listener — problema de client, não do banco |
| ORA-08177 serialization | ISOLATION SERIALIZABLE com contenção em bloco — rever necessidade do nível |
| ORA-01013/01017 | timeout/cancel pelo client vs credencial errada (conta lock? `dba_users.account_status`) |

Erro fora da tabela: derivar do conhecimento da versão 19c; se dúvida sobre comportamento específico de release, avisar e sugerir MOS (My Oracle Support).

## Regras
- Verificações são SELECT-only. Correções com efeito colateral = script entregue, nunca executado.
- Erro intermitente: perguntar por padrão temporal antes de concluir (job concorrente? backup? stats job 22h?).
