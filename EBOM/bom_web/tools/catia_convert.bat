@echo off
REM Double-click launcher for the CATIA convert agent.
REM All Korean text lives in catia_convert_menu.py -- cmd.exe mangles non-ASCII
REM batch files when the console codepage differs from the file encoding, so this
REM launcher stays pure ASCII on purpose. Do not add Korean text here.
cd /d "%~dp0"
python catia_convert_menu.py
pause
