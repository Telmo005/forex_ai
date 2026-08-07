@echo off
cd /d "%~dp0"
set /p DASHBOARD_PASSWORD=Password do dashboard (Enter para correr sem password):
streamlit run dashboard.py
