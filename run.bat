@echo off
rem PromptForge — запуск приложения
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [PromptForge] Виртуальное окружение не найдено, создаю...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :error
exit /b 0

:error
echo [PromptForge] Ошибка запуска. См. logs\promptforge.log
pause
