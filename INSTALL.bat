@echo off
py -m pip install -r requirements.txt
if errorlevel 1 python -m pip install -r requirements.txt
pause
