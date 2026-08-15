-- Execucoes de jobs do Scheduler que nao terminaram com sucesso na janela.
-- Binds: :hours (janela em horas; ex.: 24)
-- Fallback: se DBA_SCHEDULER_JOB_RUN_DETAILS der ORA-00942,
--   trocar por ALL_SCHEDULER_JOB_RUN_DETAILS.
SELECT log_date,
       owner,
       job_name,
       status,
       error#,
       actual_start_date,
       run_duration,
       SUBSTR(additional_info, 1, 200) additional_info
FROM   dba_scheduler_job_run_details
WHERE  status <> 'SUCCEEDED'
AND    log_date > SYSTIMESTAMP - NUMTODSINTERVAL(:hours, 'HOUR')
ORDER BY log_date DESC;
