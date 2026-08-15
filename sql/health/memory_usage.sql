-- Visao geral de memoria: componentes da SGA e principais metricas de PGA.
-- Todos os valores filtrados estao em bytes na origem; saida em MB.
-- Binds: nenhum.
SELECT 'SGA' area,
       name,
       ROUND(value / 1024 / 1024) mb
FROM   v$sga
UNION ALL
SELECT 'PGA',
       name,
       ROUND(value / 1024 / 1024)
FROM   v$pgastat
WHERE  name IN ('aggregate PGA target parameter',
                'aggregate PGA auto target',
                'total PGA allocated',
                'total PGA inuse',
                'maximum PGA allocated')
ORDER BY 1, 2;

-- Percentual de acerto do PGA (quanto menor, mais spill para temp).
SELECT name,
       value pct
FROM   v$pgastat
WHERE  name = 'cache hit percentage';
