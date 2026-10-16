@echo off
rem PromptForge — сборка PromptForge.exe (PyInstaller, onedir)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" -m pip install pyinstaller || goto :error
".venv\Scripts\pyinstaller.exe" --noconfirm --windowed --name PromptForge --collect-submodules app main.py || goto :error
xcopy /E /I /Y config dist\PromptForge\config >nul
xcopy /E /I /Y data dist\PromptForge\data >nul
echo Готово: dist\PromptForge\PromptForge.exe
exit /b 0
:error
echo Ошибка сборки.
pause
