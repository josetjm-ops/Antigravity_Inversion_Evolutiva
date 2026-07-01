# Contexto — Análisis de mejoras al motor evolutivo

## Alcance
Analizar (SIN implementar ningún cambio en el código de la aplicación) tres mejoras candidatas al motor evolutivo de Inversión Evolutiva, orientadas a que los agentes cierren operaciones en positivo con mayor consistencia. El resultado final debe ser un archivo PLAN_DE_MEJORA.md con los cambios especificados por fases. NO se debe modificar ningún archivo de la aplicación (agents/, evolution/, cron/, db/migrations/, scripts/) durante este proceso. Solo lectura, investigación, prototipos aislados de prueba (si es necesario, fuera del repo) y redacción del plan.

## Mejora candidata 1 — Instrumentación de decaimiento OOS→producción
Hoy nadie compara sistemáticamente lo que el backtest walk-forward prometió (fitness OOS al nacer un agente, calculado en el torneo de evolution_engine.py) contra lo que el agente realmente rindió al alcanzar MIN_SAMPLE_TRADES en producción. Investigar y especificar en el plan: migración de BD necesaria (columna fitness_oos_prometido en agentes), query o vista de comparación prometido vs real, esfuerzo y riesgo estimado.

## Mejora candidata 2 — Validación estadística real en el torneo
El umbral actual para desplegar un hijo del torneo es fitness OOS > 0 con solo n_trades OOS >= 5, muestra insuficiente para distinguir edge real de azar. Investigar y especificar: diseño de bootstrap sobre los trades OOS (resample con reemplazo, ~1000 iteraciones) con criterio de intervalo de confianza (ej. 80%), integración en la cascada existente (torneo → Hall of Fame → slot vacante) sin romperla, y simulación con datos históricos ya disponibles (modo lectura, sin escribir en BD) de qué tan distinto habría sido aplicar este criterio a ciclos pasados si es reconstruible desde logs_juez.

## Mejora candidata 3 — Walk-forward multi-fold
El backtester actual usa un solo split fijo (BACKTEST_TRAIN_DAYS=40 / BACKTEST_VALIDATE_DAYS=20), sensible al régimen de mercado de esas 3 semanas específicas. Investigar y especificar: diseño de 3 folds deslizantes (30d train / 10d validate, avanzando 10 días entre folds) con purge gap de 1 día, agregación de fitness entre folds (promedio penalizado por varianza), costo computacional estimado y si cabe dentro del timeout de 12 min de judge_daily.yml.

## Requisitos del análisis
- Todo el trabajo de exploración/backtesting en modo solo-lectura o contra la sandbox Neon aislada (tests/conftest.py) -- nunca contra Supabase de producción, y nunca modificando código fuente.
- Usar como referencia el baseline ya documentado: Calmar Ratio 1.508, win-rate 47.7% sobre 1mo.
- Si alguna mejora candidata resulta no viable o de bajo impacto, documentarlo igual en el plan con la razón -- no descartarla en silencio.

## Criterio de cumplimiento
El objetivo se considera cumplido únicamente cuando existe PLAN_DE_MEJORA.md en la raíz del proyecto, organizado en fases numeradas (Fase 1, Fase 2, Fase 3...) por dependencia y prioridad, y ningún otro archivo de la aplicación fue modificado.
