@echo off
rem Weekly knowledge-base proposal run (generates report only, never writes tables).
rem Used by Windows Task Scheduler; safe to run when the API server is not running.
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" -X utf8 "scripts\kb_weekly.py" >> "data\knowledge\weekly\_task.log" 2>&1
endlocal
