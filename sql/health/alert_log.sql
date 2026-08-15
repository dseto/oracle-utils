-- Erros ORA- no alert log via V$DIAG_ALERT_EXT (ADR), janela de :hours horas.
-- Binds: :hours (janela em horas; ex.: 24)
-- Requer privilegio de dicionario (ex.: SELECT ANY DICTIONARY ou SYSDBA).
-- Se ORA-00942/sem acesso: pedir ao usuario o arquivo alert_<sid>.log (analise offline).
-- Resumo: frequencia por codigo ORA- com primeira e ultima ocorrencia.
SELECT REGEXP_SUBSTR(message_text, 'ORA-[0-9]+')     ora_code,
       COUNT(*)                                      occurrences,
       MIN(originating_timestamp)                    first_seen,
       MAX(originating_timestamp)                    last_seen
FROM   v$diag_alert_ext
WHERE  component_id = 'rdbms'
AND    originating_timestamp > SYSTIMESTAMP - NUMTODSINTERVAL(:hours, 'HOUR')
AND    message_text LIKE '%ORA-%'
GROUP BY REGEXP_SUBSTR(message_text, 'ORA-[0-9]+')
ORDER BY occurrences DESC;

-- Detalhe cronologico (limitado a 200 mensagens para nao estourar a saida).
SELECT originating_timestamp,
       SUBSTR(message_text, 1, 300) message_text
FROM   v$diag_alert_ext
WHERE  component_id = 'rdbms'
AND    originating_timestamp > SYSTIMESTAMP - NUMTODSINTERVAL(:hours, 'HOUR')
AND    message_text LIKE '%ORA-%'
ORDER BY originating_timestamp
FETCH FIRST 200 ROWS ONLY;
