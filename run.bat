@echo off
chcp 65001 >nul
setlocal EnableExtensions
rem PromptForge - запуск приложения
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run

echo [PromptForge] Виртуальное окружение не найдено, создаю...
set "PYEXE="
set "PYARGS="

rem 1) Лаунчер py -3
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=py"
    set "PYARGS=-3"
    goto :found
)

rem 2) python из PATH (заглушка Microsoft Store здесь не пройдёт проверку)
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYEXE=python"
    goto :found
)

rem 3) Типовые пути установки
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%LOCALAPPDATA%\Python\pythoncore-3*" "%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" (
        "%%~D\python.exe" --version >nul 2>&1
        if not errorlevel 1 set "PYEXE=%%~D\python.exe"
    )
)
if defined PYEXE goto :found

echo.
echo [PromptForge] Python 3 не найден.
echo Установите Python 3.10 или новее: https://www.python.org/downloads/
echo При установке отметьте галочку "Add python.exe to PATH", затем запустите run.bat снова.
echo.
pause
exit /b 1

:found
echo [PromptForge] Найден Python: %PYEXE% %PYARGS%
"%PYEXE%" %PYARGS% -m venv .venv || goto :error
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error

:run
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :error
exit /b 0

:error
echo [PromptForge] Ошибка запуска. См. logs\promptforge.log
pause
exit /b 1