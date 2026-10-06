@echo off
rem Génère les combinés en ligne de commande (voir main.py --help).
cd /d "%~dp0"
python main.py %*
pause
