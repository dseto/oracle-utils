---
name: alert-triage
description: Triagem do alert log Oracle 19c — agrupa erros ORA- por código, frequência e janela temporal, correlaciona rajadas com causa comum e encadeia /ora-error para diagnóstico individual. Aceita arquivo/texto colado do alert log ou consulta V$DIAG_ALERT_EXT. Usar quando o usuário colar trechos de alert log, pedir "analisar o alert log", "o que aconteceu no banco ontem/hoje", ou "triagem de erros da instância".
---

# /alert-triage — triagem do alert log

## Entrada
Um destes:
- **Arquivo local** (`alert_<sid>.log` ou trecho `.log`/`.txt`) — modo offline.
- **Texto colado** pelo usuário — modo offline.
- **Nada** → modo online: consultar `V$DIAG_ALERT_EXT` via SQLcl MCP com [alert_log.sql](../../../sql/health/alert_log.sql) (bind `:hours`, padrão 24). Requer privilégio de dicionário; se ORA-00942/sem acesso, pedir o arquivo ao usuário.

Perguntar a janela de interesse se não informada (padrão: últimas 24h).

## Fluxo

### 1. Coletar e normalizar
- Modo online: rodar os dois blocos de [alert_log.sql](../../../sql/health/alert_log.sql) (resumo por código + detalhe cronológico).
- Modo offline: extrair do texto todas as linhas com `ORA-\d+` e o timestamp da entrada correspondente (linhas de timestamp precedem o bloco de mensagem no alert log).

### 2. Agrupar e classificar
Para cada código ORA- distinto:
- Frequência total, primeira e última ocorrência, distribuição temporal (uniforme vs rajada).
- Severidade: instância (ORA-00600/07445/00494, ORA-01578 corrupção) > espaço (ORA-01652/01653/01654/30036/00257/19809) > aplicação (ORA-00001, ORA-01555, ORA-00060, ORA-04068) > ruído conhecido.

### 3. Correlacionar
- **Rajada do mesmo código em minutos** = quase sempre uma única causa raiz (ex.: ORA-01652 em rajada = uma query/lote estourando temp — não são N problemas).
- **Códigos encadeados no tempo**: ex.: ORA-00257/ORA-19809 seguido de sessões travadas = FRA cheia; ORA-04031 seguido de ORA-00600 = pressão de shared pool.
- ORA-00600/07445: extrair os argumentos entre colchetes (primeiro argumento identifica o bug) e recomendar abertura de SR/busca no MOS — não especular causa interna.

### 4. Diagnóstico individual
Para os 1–3 códigos mais relevantes, encadear com `/ora-error` (causas prováveis + queries de verificação). Se a causa apontar para espaço/FRA, encadear `/ora-health` (área de tablespaces/FRA).

## Saída (relatório)
```
# Triagem alert log — janela <inicio> a <fim>

| ORA- | Ocorrências | Primeira | Última | Padrão | Severidade |
|------|-------------|----------|--------|--------|------------|
| ORA-01652 | 47 | 03:12 | 03:19 | rajada | espaço |

## Correlações
- <ex.: as 47 ocorrências de ORA-01652 em 7 min = mesma causa (job X às 03:00)>

## Prioridades
1. <código> — <causa provável> — <próxima ação / query de verificação>

## Não investigado / ruído
<códigos irrelevantes e por quê>
```

## Regras
- Somente SELECT (modo online). Nenhuma ação corretiva executada — adição de datafile, purge de FRA, etc. viram scripts entregues para revisão.
- Não colar o alert log inteiro de volta no relatório — apenas as linhas-evidência.
- Timestamps do alert log estão no fuso do servidor; anotar isso ao comparar com horários relatados pelo usuário.
