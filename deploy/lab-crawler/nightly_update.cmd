@echo off
rem Nightly periodic update of the crawler's archive (2026-11-16, after the first real night).
rem Windows Task Scheduler runs this one file; the schedule's time waits for the owner.
rem Everything the night needs is pinned in C:\nknu-crawl\compose.yaml (service "nightly"); nothing is typed by hand.
rem The night's own log is /crawl/archive/update.log; this file only records that the task started and how it ended
rem (C:\nknu-crawl\nightly-task.log), so a night Docker could not start still leaves a line.
rem ASCII only: cmd reads this file in the console's code page.
setlocal
set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh"
set "TASKLOG=C:\nknu-crawl\nightly-task.log"
echo %date% %time% start>> "%TASKLOG%"
docker compose -f C:\nknu-crawl\compose.yaml run --rm nightly >> "%TASKLOG%" 2>&1
set "RC=%errorlevel%"
rem the redirection goes first: "%RC%>>" with a one-digit code would read as a handle number (0>>) and drop the digit
>> "%TASKLOG%" echo %date% %time% end, exit code %RC%
rem 2026-11-17: then the content session's nightly ingest (agreed 11-16; added after its library swap 11-17 00:49).
rem It reads current.tsv and seen.tsv itself, so it runs whatever the crawler's step ended with; its own exit code
rem is logged on its own line and the task still ends with the crawler's code.
echo %date% %time% ingest start>> "%TASKLOG%"
call C:\nknu-transfer\build\nightly_ingest.cmd >> "%TASKLOG%" 2>&1
set "IRC=%errorlevel%"
>> "%TASKLOG%" echo %date% %time% ingest end, exit code %IRC%
rem 2026-11-23: then the night's coverage by site is written for the panels (reports/coverage.jsonl). It only
rem reads the ledger, runs after the ingest, and does not change the task's exit code.
echo %date% %time% coverage start>> "%TASKLOG%"
docker compose -f C:\nknu-crawl\compose.yaml run --rm coverage --quiet >> "%TASKLOG%" 2>&1
set "CRC=%errorlevel%"
>> "%TASKLOG%" echo %date% %time% coverage end, exit code %CRC%
rem 2026-11-23: last, the raw crawl files (E:\nknu\warc) are copied to the backup disk (D:\nknu-backup\crawler-warc):
rem files the backup lacks, each read back and checked by SHA-256; nothing is deleted there and the source is mounted
rem read-only. It runs after the ingest so that nothing it does can hold the ingest up, and it does not change the
rem task's exit code. Its result is /crawl/archive/reports/warc-backup.json (and warc-backup.log beside it).
echo %date% %time% warc backup start>> "%TASKLOG%"
docker compose -f C:\nknu-crawl\compose.yaml run --rm warc-backup --quiet >> "%TASKLOG%" 2>&1
set "BRC=%errorlevel%"
>> "%TASKLOG%" echo %date% %time% warc backup end, exit code %BRC%
exit /b %RC%
