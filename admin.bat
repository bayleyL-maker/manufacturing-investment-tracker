@echo off
REM Launches the admin GUI without opening a console window.
REM Double-click this file to start the admin.
cd /d "%~dp0"
start "" pythonw scripts\admin.py
