@echo off
cd /d "%~dp0"
set /p OUTPUT_SOURCE=Pasta output/ de outra maquina para copiar (Enter para comecar vazio):
if "%OUTPUT_SOURCE%"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -OutputSourcePath "%OUTPUT_SOURCE%"
)
pause
