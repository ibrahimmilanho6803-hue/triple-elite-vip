@echo off
start "Dashboard" cmd /k "cd C:\Users\HP\Desktop\Triple_Elite_VIP && python dashboard.py"
start "Paiement" cmd /k "cd C:\Users\HP\Desktop\Triple_Elite_VIP && python paiement.py"
echo ============================
echo Serveurs demarres !
echo Dashboard : http://localhost:5000
echo Paiement  : http://localhost:5001
echo ============================
pause