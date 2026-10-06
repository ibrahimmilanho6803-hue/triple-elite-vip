@echo off
rem Lance le site client et le site de paiement en local (variables : fichier .env, voir .env.example).
cd /d "%~dp0"
start "Dashboard" cmd /k python dashboard.py
start "Paiement" cmd /k python paiement.py
echo ============================
echo Serveurs demarres !
echo Site client : http://localhost:5000
echo Paiement    : http://localhost:5001
echo ============================
pause
