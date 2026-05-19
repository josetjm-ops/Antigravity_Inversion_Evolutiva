@echo off
REM ============================================================
REM  DESPLIEGUE A PRODUCCION - Sesion 7 nocturna
REM  Limpieza OANDA + atr_period retroactivo + pips_sl + nuevos horarios
REM
REM  Solo haz DOBLE CLIC sobre este archivo.
REM  No necesitas escribir nada.
REM
REM  Importante: las migraciones SQL (005, 006) deben aplicarse a Neon
REM  ANTES o DESPUES de este push, usando APLICAR_MIGRACIONES.bat.
REM ============================================================
setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

echo.
echo ============================================================
echo  DESPLIEGUE A PRODUCCION - Antigravity Inversion Evolutiva
echo  (Sesion 7 nocturna: horarios + cleanup OANDA + atr_period + pips_sl)
echo ============================================================
echo.

REM -- Verificar que git esta instalado --------------------------
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git no esta instalado o no esta en el PATH.
    echo Descarga e instala Git desde https://git-scm.com/download/win
    echo.
    pause
    exit /b 1
)

REM -- Verificar que estamos en un repo git ---------------------
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Esta carpeta no es un repositorio git.
    echo.
    pause
    exit /b 1
)

REM -- Validar sintaxis Python de los archivos modificados ------
echo [PASO 1/5] Validando sintaxis Python...
where python >nul 2>&1
if errorlevel 1 (
    echo   [AVISO] Python no esta en el PATH. Salto la validacion de sintaxis.
) else (
    python -m py_compile cron\trade_monitor.py
    if errorlevel 1 (
        echo   [ERROR] Sintaxis invalida en cron\trade_monitor.py
        pause
        exit /b 1
    )
    python -m py_compile cron\judge_scheduler.py
    if errorlevel 1 (
        echo   [ERROR] Sintaxis invalida en cron\judge_scheduler.py
        pause
        exit /b 1
    )
    python -m py_compile agents\investor_agent.py
    if errorlevel 1 (
        echo   [ERROR] Sintaxis invalida en agents\investor_agent.py
        pause
        exit /b 1
    )
    python -m py_compile db\apply_migrations.py
    if errorlevel 1 (
        echo   [ERROR] Sintaxis invalida en db\apply_migrations.py
        pause
        exit /b 1
    )
    python -m py_compile dashboard\app.py
    if errorlevel 1 (
        echo   [ERROR] Sintaxis invalida en dashboard\app.py
        pause
        exit /b 1
    )
    echo   [OK] Archivos Python compilan sin errores.
)

REM -- Mostrar la rama actual -----------------------------------
echo.
echo [PASO 2/5] Verificando rama git...
for /f "tokens=*" %%i in ('git branch --show-current') do set "RAMA=%%i"
echo   Rama actual: %RAMA%

REM -- Mostrar los archivos modificados -------------------------
echo.
echo [PASO 3/5] Archivos modificados/nuevos:
git status --short

REM -- Stage de los archivos especificos del despliegue ---------
echo.
echo [PASO 4/5] Agregando archivos al commit...

REM Codigo Python modificado
git add cron\trade_monitor.py cron\judge_scheduler.py agents\investor_agent.py dashboard\app.py db\apply_migrations.py

REM Workflows GitHub Actions
git add .github\workflows\trade_monitor.yml .github\workflows\judge_daily.yml .github\workflows\trading_cycle.yml

REM Configuracion y scripts
git add .env.example DESPLEGAR_A_PRODUCCION.bat APLICAR_MIGRACIONES.bat

REM Migraciones SQL (nuevas + actualizadas)
git add db\migrations\001_initial_schema.sql db\migrations\002_oanda_integration.sql db\migrations\003_smc_schema.sql db\migrations\005_cleanup_oanda_columns.sql db\migrations\006_atr_period_backfill.sql

REM Documentacion canonica
git add Inversion_Evolutiva.md

if errorlevel 1 (
    echo   [ERROR] git add fallo.
    pause
    exit /b 1
)
echo   [OK] Archivos staged.

REM -- Verificar si hay algo que commitear ----------------------
git diff --cached --quiet
if not errorlevel 1 (
    echo.
    echo [AVISO] No hay cambios staged. Quizas ya commiteaste antes.
    echo Intentando push de todos modos por si hay commits locales sin pushear...
    goto :PUSH
)

REM -- Commit ---------------------------------------------------
echo.
echo Creando commit...
git commit ^
    -m "feat(sesion7-nocturna): horarios reasignados + cleanup OANDA + atr_period retroactivo + pips_sl" ^
    -m "HORARIOS:" ^
    -m "- Ventana de apertura: 1:30 am - 11:00 pm Bogota (06:30 - 04:00 UTC siguiente dia)." ^
    -m "- Monitor SL/TP: cada 15 min de 1:30 am a 10:30 pm Bogota (ultimo ciclo)." ^
    -m "- Cierre forzoso EOD: 10:45 pm Bogota (03:45 UTC)." ^
    -m "- Ciclo evolutivo del Juez: 11:00 pm Bogota (04:00 UTC siguiente dia)." ^
    -m "- trade_monitor.yml: 4 entradas de cron coordinadas para cubrir el rango con cruce de medianoche UTC." ^
    -m "- judge_daily.yml: cron a 03:45 UTC + sleep 900 para que el Juez arranque exactamente a las 04:00 UTC." ^
    -m "- Nuevas env vars TRADING_START_TIME_UTC y TRADING_CUTOFF_TIME_UTC (formato HH:MM)." ^
    -m "- JUDGE_RUN_TIME default cambiado de 17:00 a 23:00." ^
    -m "- _within_trading_hours() maneja ventanas que cruzan la medianoche UTC." ^
    -m "BD:" ^
    -m "- 005_cleanup_oanda_columns.sql: DROP COLUMN IF EXISTS de columnas OANDA huerfanas." ^
    -m "- 002_oanda_integration.sql convertida en no-op deprecada." ^
    -m "- 006_atr_period_backfill.sql: aplica atr_period=14 a agentes existentes (incluida Gen 1)." ^
    -m "- 003_smc_schema.sql: default JSONB incluye atr_period:14 para nuevas instancias." ^
    -m "- investor_agent._persist_operation ahora puebla pips_sl en INSERT para BUY/SELL." ^
    -m "DOC:" ^
    -m "- Inversion_Evolutiva.md actualizado: secciones 2,3,7,8,11,14,15,16 + changelog." ^
    -m "- Tabla agentes/operaciones documenta fecha_eliminacion, razon_eliminacion, pips_sl, pnl_porcentaje, sl_dinamico, precio_extremo_favorable, timestamps." ^
    -m "- dashboard/app.py (pestana Instrucciones) refleja los nuevos horarios." ^
    -m "EJECUCION:" ^
    -m "- APLICAR_MIGRACIONES.bat aplica 005 y 006 a Neon de forma idempotente."
if errorlevel 1 (
    echo   [ERROR] git commit fallo. Revisa los mensajes arriba.
    pause
    exit /b 1
)
echo   [OK] Commit creado.

:PUSH
REM -- Push -----------------------------------------------------
echo.
echo [PASO 5/5] Subiendo a GitHub (rama %RAMA%)...
git push origin %RAMA%
if errorlevel 1 (
    echo.
    echo   [ERROR] git push fallo. Posibles causas:
    echo     - No tienes permisos para pushear a esta rama.
    echo     - Necesitas autenticarte en GitHub.
    echo     - La rama remota tiene commits que no tienes localmente (haz git pull primero).
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  DESPLIEGUE COMPLETADO EXITOSAMENTE
echo ============================================================
echo.
echo Que sigue:
echo   1. Ejecuta APLICAR_MIGRACIONES.bat para aplicar 005 y 006 a Neon
echo      (idempotente: se puede correr varias veces sin problema).
echo   2. Verifica los nuevos workflows en GitHub - Actions:
echo        - "Monitor de Trades" debe disparar desde las 1:30 am Bogota.
echo        - "Agente Juez" debe disparar a las 10:45 pm Bogota (cierre EOD)
echo          y completar el ciclo evolutivo a las 11:00 pm Bogota.
echo   3. (Opcional) "Run workflow" manual del Agente Juez con dry_run=true
echo      para validar la conectividad antes del primer ciclo en vivo.
echo.
echo Puedes cerrar esta ventana.
echo.
pause
endlocal
