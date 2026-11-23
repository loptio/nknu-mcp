@echo off
rem Nightly ingest of the crawler's new exports into the library (2026-11-16; spec
rem docs/superpowers/specs/2026-11-16-nightly-ingest-design.md). The crawler's nightly_update.cmd calls this file when
rem its own step has ended: call C:\nknu-transfer\build\nightly_ingest.cmd
rem It runs the compose service "ingest" and, when the library was swapped (the service leaves
rem C:\nknu-transfer\out\ingest-swapped.flag), restarts Datasette, which holds the replaced file open. Only that one
rem service is restarted: restarting the whole inspect compose would start a 17-minute page sweep.
rem What happened is in /work/research/pipeline_2026-11-02/ingest-status.json; this file only records start and end.
rem ASCII only: cmd reads this file in the console's code page. The redirection stands before each echo: a digit
rem right before ">>" (exit code 0) would be read as a handle number and the digit lost.
setlocal
set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh"
set "TASKLOG=C:\nknu-transfer\build\nightly-ingest-task.log"
set "FLAG=C:\nknu-transfer\out\ingest-swapped.flag"
if exist "%FLAG%" del "%FLAG%"
>> "%TASKLOG%" echo %date% %time% start
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm ingest >> "%TASKLOG%" 2>&1
set "RC=%errorlevel%"
if exist "%FLAG%" (
  docker compose -f C:\nknu-transfer\inspect\compose.yaml restart datasette >> "%TASKLOG%" 2>&1
  del "%FLAG%"
  >> "%TASKLOG%" echo %date% %time% datasette restarted
)
>> "%TASKLOG%" echo %date% %time% end, exit code %RC%
exit /b %RC%
