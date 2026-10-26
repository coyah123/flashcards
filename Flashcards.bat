@echo off
rem Double-click launcher for Windows. Works from any folder and through a shortcut
rem (right-click > Send to > Desktop). If you COPY this file elsewhere instead,
rem set FLASHCARDS_HOME to the flashcards folder.
setlocal
set "APP_DIR=%~dp0"
if not exist "%APP_DIR%flashcards.py" set "APP_DIR=%FLASHCARDS_HOME%\"
if not exist "%APP_DIR%flashcards.py" (
    echo Can't find flashcards.py. Set FLASHCARDS_HOME to the flashcards folder.
    pause
    exit /b 1
)

rem pythonw = no console window. Fall back to the py launcher, then python.
where pythonw >nul 2>&1 && (start "" /D "%APP_DIR%" pythonw "%APP_DIR%flashcards.py" & exit /b 0)
where pyw >nul 2>&1 && (start "" /D "%APP_DIR%" pyw -3 "%APP_DIR%flashcards.py" & exit /b 0)
where python >nul 2>&1 && (start "" /D "%APP_DIR%" python "%APP_DIR%flashcards.py" & exit /b 0)
echo Python 3 not found. Install it from https://www.python.org/downloads/
pause
exit /b 1
