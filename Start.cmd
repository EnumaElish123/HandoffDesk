@echo off
setlocal
call "%~dp0handoffdesk.cmd" %* desktop
if errorlevel 1 pause
