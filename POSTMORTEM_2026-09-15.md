# Post-mortem — INVERSIÓN EVOLUTIVA (2026-06 → 2026-09)

**Veredicto: el enfoque no funciona. La causa no son las estrategias — es que la
función de fitness nunca midió lo que el sistema hace en producción.**

---

## El resultado, sin adornos

| | |
|---|---|
| Ventana de validación | 2026-08-13 → 2026-09-10 |
| Muestra | 322 operaciones = **166 decisiones independientes** |
| Expectancy | **−0,171 R por decisión** |
| Probabilidad de que sea positiva | **1,0 %** |
| Pool | $14.435 → $13.742 (**−4,8 %**) |
| Criterios de continuidad cumplidos | **1 de 4** |

Sobre la vida del proyecto: **1.041 operaciones, −$7,55** con el capital
original; y **−8,4 %** desde la capitalización a $15.000.

---

## Lo que se intentó, en orden

Tres perfiles validados con holdout de 6 meses y gate bootstrap. **Tres
fracasos de transferencia:**

| Intento | Prometido (holdout) | Real (producción) |
|---|---|---|
| Julio | +0,50 a +0,71 R | −0,108 R |
| Agosto (backtester "honesto") | +0,60 R | −1,14 R / **0 operaciones** |

El campeón `2026-08-13_02` prometió +0,6275 R y **no operó ni una sola vez en
un mes**. Su hermano operó 5 veces y dio −1,140 R.

---

## Las cuatro divergencias backtest ↔ producción

Se encontraron cuatro. Se cerraron tres. Cada corrección destapó la siguiente.

| # | Divergencia | Efecto medido | Estado |
|---|---|---|---|
| 1 | El backtest no cerraba por fin de día | −0,069 R | Corregida (PR #37) |
| 2 | El backtest no modelaba el trailing stop | −0,189 R | Corregida (PR #39) |
| 3 | Cadencia: backtest cada 1 h, producción cada 15 min | −0,160 R | Medida, no aplicada |
| 4 | **El backtest ignora el agente macro** | **60 % del peso de decisión** | **No cerrable** |

Las tres primeras suman ~0,41 R. **El hueco real es de ~0,62 R.**

### Por qué la cuarta no se puede cerrar

El backtester declara en su propio docstring: *"Sin macro: solo señales
técnicas"*. Pero en producción `peso_tecnico_vs_macro` promedia **0,40** — el
**60 % de la decisión en vivo la aporta un agente que el backtest nunca vio**.

Meterlo al backtest exigiría histórico de noticias y sentimiento alineado a dos
años de velas de 15 minutos. Esa fuente no existe en el proyecto y ya estaba
marcada como bloqueante desde julio.

La consecuencia es estructural: **la evolución lleva toda la vida del proyecto
optimizando genes técnicos contra un fitness que desconoce la mayor parte de lo
que decide en vivo.** El gen `umbral_confianza_minima`, que la evolución ajusta
con precisión decimal, opera sobre distribuciones de confianza completamente
distintas a cada lado.

---

## El hallazgo que cierra la discusión

Sobre las 322 operaciones reales de la ventana, partidas por la mediana de
confianza técnica:

```
confianza técnica ALTA (>0,59):  n=161   −0,356 R
confianza técnica BAJA (≤0,59):  n=161   +0,215 R
                                 brecha   0,571 R — al revés
```

Robusto al partir por especie (reversión: 0,730 R de brecha), por dirección
(BUY 0,738 R, SELL 0,460 R) y no explicado por el tamaño de posición.

**En producción, cuanto más confiado está el motor técnico, peor le va.**

> **Advertencia metodológica honesta:** esta muestra está filtrada por el propio
> umbral de confianza que se estudia, así que hay sesgo de selección. Es
> indicio fuerte, no prueba causal limpia. Pero apunta en la misma dirección que
> todo lo demás: la señal que el sistema optimiza no predice el resultado real.

---

## Hipótesis que se descartaron por medición

Vale documentarlas para que nadie las vuelva a perseguir:

- **Cierre por fin de día** — real pero pequeño (−0,069 R). No explicaba nada.
- **Cadencia de evaluación** — real y consistente (−0,16 R en ambos campeones),
  pero el backtest con la cadencia correcta sigue diciendo **+0,45 R** mientras
  producción entregó −0,17 R.
- **Fuente de datos (Yahoo vs Dukascopy)** — difieren 3,27 pips de media, pero
  es un **offset casi constante** (desviación 0,70) y no un desfase temporal
  (probados corrimientos de ±45 min: la alineación en 0 es la mejor). Un offset
  constante no altera indicadores diferenciales como RSI o MACD. **Descartada.**

---

## El bug que costó 21 días

Del 26-ago al 15-sep el Agente Juez murió cada noche:

```python
cap_inicial = 10.0                                    # hardcodeado
(1064.38 − 10) / 10 × 100 = 10.543,82
ranking_historico.roi_diario es NUMERIC(8,4) → máx 9.999,9999
```

Al capitalizar a $15.000 el 10-ago, cada agente pasó de ~$10 a ~$1.000. El
umbral exacto de muerte era **cualquier agente por encima de ~$1.010**.

**La lección no es el cálculo: es que un paso de AUDITORÍA podía abortar el
ciclo evolutivo completo.** Corregido en PR #57 con el capital real más un
recorte defensivo.

**Segunda lección, operativa:** el sistema avisó correctamente durante 21 días
generando 56 issues de GitHub. **Nadie las vio.** Una alerta que no llega a
donde la persona mira no es una alerta.

---

## Qué sí quedó construido y es reutilizable

- `data/dukascopy_client.py` — feed de ticks desde 2003, sin API key, con caché
  Parquet reanudable.
- `evolution/backtester.py` — walk-forward con cierre EOD, trailing, salidas
  parciales y break-even modelados, gate bootstrap y validación multi-fold.
- `scripts/evaluar_criterio_corte.py` — evaluador que mide **decisiones
  independientes** en vez de operaciones, corrigiendo la correlación entre
  agentes (factor 1,9× medido).
- La disciplina de holdout intocable + bootstrap, que funcionó: **detectó
  correctamente que no había edge suficiente en tendencia y ruptura.**

---

## Lo que yo haría distinto

1. **Validar la transferencia antes de construir sobre ella.** El primer
   experimento debió ser: desplegar un genoma cualquiera y comprobar que
   backtest y producción dan lo mismo. Se construyeron meses de maquinaria
   evolutiva sobre una correspondencia que nunca se verificó.

2. **Nunca declarar "ahora sí está honesto".** Lo dije dos veces —tras el cierre
   EOD y tras el trailing— y me equivoqué las dos. Cada corrección destapaba la
   siguiente divergencia.

3. **Un paso de auditoría no puede abortar el proceso principal.**

4. **No intervenir con muestras de dos días.** El 12 de agosto cambié la
   configuración dos veces reaccionando al P&L diario, y las dos veces me
   equivocé. Las correcciones que sirvieron salieron de medir 6 meses de
   holdout, no de mirar el resultado de ayer.

---

## Recomendación

**Cerrar.** El requisito científico de un sistema evolutivo es que la función de
fitness correlacione con el desempeño real. Tras tres meses, no lo hace, y el
hueco principal no es cerrable con los datos disponibles.

Seguir iterando significaría buscar una quinta divergencia sin garantía de que
sea la última, sobre un sistema que ya falsificó su mecanismo central tres
veces.

**Lo que NO debe borrarse aún:** el cliente Dukascopy y el backtester son piezas
sólidas y reutilizables para cualquier proyecto futuro de trading cuantitativo.

---

## Cierre operativo (2026-09-15)

Decisión aprobada: **apagar y archivar, no eliminar.**

1. Workflows (Monitor, Juez, Health Check, Backfill) desactivados tras el
   cierre EOD de la noche del 15-sep, sin posiciones abiertas.
2. Tareas programadas de revisión eliminadas.
3. Repositorio archivado en GitHub (solo lectura, reversible).
4. Respaldo completo de la base de producción en
   `C:\JOSE TOMAS JARAMILLO\Respaldo_Inversion_Evolutiva_2026-09-15`
   (CSV por tabla + esquema + migraciones).

**Pendiente manual:** pausar en cron-job.org los jobs que disparan los
workflows ("GH Action - Trade Monitor", "Judge Daily", "Health Check",
"Backfill Weekly").

**Para reactivar:** desarchivar el repo en Settings → General, reactivar los
workflows con `gh workflow enable <id>` y reanudar los jobs de cron-job.org.
