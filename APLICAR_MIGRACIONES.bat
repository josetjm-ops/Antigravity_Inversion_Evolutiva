@echo off
REM ============================================================
REM  APLICAR MIGRACIONES A NEON - Sesion 7 nocturna
REM
REM  Ejecuta:
REM    005_cleanup_oanda_columns.sql   (limpia columnas OANDA huerfanas)
REM    006_atr_period_backfill.sql     (agrega atr_period a agentes existentes)
REM
REM  Solo haz DOBLE CLIC sobre este archivo.
REM  Ambas migraciones son IDEMPOTENTES: si ya estan aplicadas, no rompen nada.
REM ============================================================
setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

echo.
echo ============================================================
echo  APLICAR MIGRACIONES - Antigravity Inversion Evolutiva
echo ============================================================
echo.

REM -- Verificar que Python esta instalado -----------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python no esta instalado o no esta en el PATH.
    echo Descarga e instala Python 3.11 desde https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM -- Verificar que el .env existe -----------------------------
if not exist ".env" (
    echo [ERROR] No se encuentra el archivo .env con DATABASE_URL.
    echo Crea un .env basado en .env.example y configura DATABASE_URL.
    echo.
    pause
    exit /b 1
)

REM -- Verificar dependencias minimas ---------------------------
echo [PASO 1/3] Verificando dependencias Python...
python -c "import psycopg2, sqlalchemy, dotenv" >nul 2>&1
if errorlevel 1 (
    echo   [AVISO] Faltan dependencias. Instalando requirements.txt...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo   [ERROR] No se pudieron instalar las dependencias.
        pause
        exit /b 1
    )
)
echo   [OK] Dependencias presentes.

REM -- Health check + aplicar migraciones -----------------------
echo.
echo [PASO 2/3] Aplicando migraciones 005 y 006 a Neon...
python -m db.apply_migrations --only 005,006
if errorlevel 1 (
    echo.
    echo   [ERROR] Una o mas migraciones fallaron. Revisa los mensajes arriba.
    pause
    exit /b 1
)

echo.
echo [PASO 3/3] Migraciones aplicadas correctamente.
echo.
echo ============================================================
echo  MIGRACIONES COMPLETADAS
echo ============================================================
echo.
echo Que sigue:
echo   1. Haz commit y push de los cambios con DESPLEGAR_A_PRODUCCION.bat
echo   2. Verifica el primer ciclo del Juez a las 11:00 pm Bogota.
echo.
pause
endlocal
