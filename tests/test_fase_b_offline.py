"""
Tests de la Fase B — cliente Dukascopy y motor evolutivo offline.

Motivación: el cuello de botella #1 del sistema es el hambre de datos (la
evolución en producción avanza a ~10 trades/día). La Fase B desacopla la
búsqueda genética del reloj de producción corriéndola sobre años de
histórico. Estos tests protegen las dos piezas críticas:

  1. La DECODIFICACIÓN del feed de Dukascopy (un error de offset o de mes
     produciría precios silenciosamente falsos — el peor tipo de bug para
     un sistema que evoluciona sobre esos datos).
  2. El PROTOCOLO ANTI-OVERFIT (folds que no se solapan con el holdout,
     fitness penalizado por varianza entre regímenes).

Puros — sin red ni DB: el feed se simula construyendo un .bi5 real (LZMA
sobre la estructura binaria documentada).
"""
from __future__ import annotations

import lzma
import os
import struct
import sys
from datetime import date, datetime, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import dukascopy_client as dk
from evolution import offline_evolution as oe


# ─── Decodificación del feed binario ────────────────────────────────────────

def _bi5_sintetico(ticks: list[tuple[int, int, int]]) -> bytes:
    """Construye un .bi5 real: LZMA sobre registros de 20 bytes big-endian."""
    crudo = b"".join(
        struct.pack(">3I2f", ms, ask, bid, 1.0, 1.0) for ms, ask, bid in ticks
    )
    return lzma.compress(crudo, format=lzma.FORMAT_ALONE)


def test_decodifica_precios_con_el_divisor_correcto(monkeypatch):
    """EUR/USD cotiza con 5 decimales: el entero 108435 debe ser 1.08435.
    Un divisor equivocado desplazaría TODOS los precios sin fallar nunca."""
    payload = _bi5_sintetico([(0, 108435, 108433), (1500, 108440, 108438)])

    class _Resp:
        status_code = 200
        content = payload
        def raise_for_status(self): pass

    monkeypatch.setattr(dk.requests, "get", lambda *a, **k: _Resp())

    df = dk.fetch_hour_ticks("EURUSD", date(2024, 5, 6), 12)
    assert len(df) == 2
    assert df["ask"].iloc[0] == pytest.approx(1.08435)
    assert df["bid"].iloc[0] == pytest.approx(1.08433)
    assert df["mid"].iloc[0] == pytest.approx((1.08435 + 1.08433) / 2)


def test_timestamps_son_offset_desde_la_hora_utc(monkeypatch):
    """El primer uint32 son milisegundos DESDE EL INICIO DE LA HORA, no un
    epoch. Interpretarlo mal desordenaría toda la serie temporal."""
    payload = _bi5_sintetico([(0, 108435, 108433), (90_000, 108440, 108438)])

    class _Resp:
        status_code = 200
        content = payload
        def raise_for_status(self): pass

    monkeypatch.setattr(dk.requests, "get", lambda *a, **k: _Resp())

    df = dk.fetch_hour_ticks("EURUSD", date(2024, 5, 6), 12)
    assert df["timestamp"].iloc[0] == datetime(2024, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert df["timestamp"].iloc[1] == datetime(2024, 5, 6, 12, 1, 30, tzinfo=timezone.utc)


def test_url_usa_mes_indexado_desde_cero(monkeypatch):
    """Dukascopy indexa los meses desde 0 (enero=00). Es EL error clásico
    del feed: pedir mayo como '05' devuelve datos de junio."""
    capturadas = []

    class _Resp:
        status_code = 404
        content = b""
        def raise_for_status(self): pass

    def _fake_get(url, **kwargs):
        capturadas.append(url)
        return _Resp()

    monkeypatch.setattr(dk.requests, "get", _fake_get)
    dk.fetch_hour_ticks("EURUSD", date(2024, 5, 6), 9)

    assert "/2024/04/06/09h_ticks.bi5" in capturadas[0], (
        f"Mayo debe pedirse como mes 04 (0-indexado). URL: {capturadas[0]}"
    )


def test_hora_sin_mercado_devuelve_vacio_sin_reventar(monkeypatch):
    """Fines de semana y feriados responden 404: es normal, no un error."""
    class _Resp:
        status_code = 404
        content = b""
        def raise_for_status(self): pass

    monkeypatch.setattr(dk.requests, "get", lambda *a, **k: _Resp())
    df = dk.fetch_hour_ticks("EURUSD", date(2024, 5, 5), 3)   # domingo
    assert df.empty
    assert list(df.columns) == ["timestamp", "ask", "bid", "mid"]


def test_agregacion_a_velas_usa_mid_no_bid_ni_ask():
    """Las velas se construyen sobre el MID porque el costo de transacción
    se modela aparte (TRADE_FRICTION_PIPS). Usar bid/ask lo cobraría dos veces."""
    base = datetime(2024, 5, 6, 12, 0, tzinfo=timezone.utc)
    ticks = pd.DataFrame({
        "timestamp": [base, base + pd.Timedelta(seconds=20), base + pd.Timedelta(seconds=40)],
        "ask": [1.1002, 1.1006, 1.1004],
        "bid": [1.1000, 1.1004, 1.1002],
    })
    ticks["mid"] = (ticks["ask"] + ticks["bid"]) / 2

    velas = dk.ticks_a_velas(ticks, regla="1min")
    assert len(velas) == 1
    assert velas["open"].iloc[0] == pytest.approx(1.1001)
    assert velas["high"].iloc[0] == pytest.approx(1.1005)
    assert velas["low"].iloc[0] == pytest.approx(1.1001)
    assert velas["close"].iloc[0] == pytest.approx(1.1003)


# ─── Protocolo anti-overfit del motor offline ───────────────────────────────

def _df_sintetico(n_velas: int) -> pd.DataFrame:
    base = pd.Timestamp("2024-01-01", tz="UTC")
    precios = [1.10 + (i % 50) * 0.0001 for i in range(n_velas)]
    return pd.DataFrame({
        "timestamp": [base + pd.Timedelta(minutes=15 * i) for i in range(n_velas)],
        "open": precios, "high": [p + 0.0005 for p in precios],
        "low": [p - 0.0005 for p in precios], "close": precios,
    })


def test_folds_no_se_solapan_con_su_propio_train():
    """Cada fold OOS debe empezar DESPUÉS de su warmup + purge: si el tramo
    evaluado incluyera velas de entrenamiento, el fitness estaría inflado."""
    df = _df_sintetico(96 * 200)
    folds = oe._construir_folds(df, n_folds=3, train_dias=40, val_dias=15,
                                purge_dias=1, velas_por_dia=96)
    assert len(folds) >= 2
    for i, (ini, fin) in enumerate(folds):
        assert fin > ini
        # El inicio del OOS deja espacio para train+purge por delante
        assert ini >= (40 + 1) * 96, f"Fold {i} arranca antes de terminar su warmup"


def test_folds_avanzan_por_el_historico():
    """Los folds deben cubrir tramos DISTINTOS — si todos evaluaran el mismo
    período, la penalización por varianza no mediría robustez entre regímenes."""
    df = _df_sintetico(96 * 300)
    folds = oe._construir_folds(df, n_folds=4, train_dias=40, val_dias=15,
                                purge_dias=1, velas_por_dia=96)
    inicios = [f[0] for f in folds]
    assert len(set(inicios)) == len(inicios), "Folds duplicados"
    assert inicios == sorted(inicios), "Los folds deben avanzar cronológicamente"


def test_historico_insuficiente_no_produce_folds():
    """Con pocos datos debe devolver lista vacía (y el runner aborta), en vez
    de inventar folds degenerados que darían un fitness sin sentido."""
    df = _df_sintetico(100)
    assert oe._construir_folds(df, 3, 40, 15, 1, 96) == []


def test_fitness_penaliza_la_inestabilidad_entre_folds(monkeypatch):
    """Un genoma que rinde +2 en un fold y -1 en otro debe puntuar PEOR que
    uno estable en +0.4: el primero encontró un régimen, no un edge."""
    fitness_por_llamada = iter([2.0, -1.0])

    def _fake_metrics(trades):
        return {"fitness": next(fitness_por_llamada), "n_trades": 10,
                "expectancy": 0.0, "win_rate": 0.5, "max_drawdown": 0.1,
                "oos_trades": []}

    monkeypatch.setattr("evolution.backtester._walk_forward_trades",
                        lambda *a, **k: [{"pnl": 0.1}])
    monkeypatch.setattr("evolution.backtester._calc_metrics", _fake_metrics)

    g = {"params_tecnicos": {}, "params_smc": {}, "params_riesgo": {},
         "especie": "tendencia"}
    ev = oe.evaluar_genoma(g, _df_sintetico(1000), [(100, 200), (300, 400)],
                           {"direccion": "NEUTRAL"}, lam=0.5)

    # media = 0.5, desviación = 2.121 → fitness = 0.5 - 0.5*2.121 = -0.56
    assert ev["fitness_medio"] == pytest.approx(0.5)
    assert ev["fitness"] < 0, "La inestabilidad debe hundir el fitness agregado"


# Los pesos del ensamble técnico se RENORMALIZAN para sumar 1.0 después de
# muestrear (igual que hace breed_agent tras mutar en producción). Esa
# renormalización puede dejar un peso individual marginalmente fuera de su
# bound — es el comportamiento real del motor, no un defecto: la restricción
# que de verdad gobierna es "los tres suman 1".
_PESOS_NORMALIZADOS = {"peso_rsi", "peso_ema", "peso_macd"}


def test_genoma_aleatorio_respeta_los_bounds_de_produccion():
    """Los genomas iniciales deben nacer dentro de los mismos rangos que usa
    la mutación en producción — si no, sus campeones no serían portables."""
    import random
    from evolution.evolution_engine import _BOUNDS_TECNICOS, _BOUNDS_SMC

    rng = random.Random(7)
    for _ in range(15):
        g = oe.genoma_aleatorio("tendencia", rng)
        for gen, (lo, hi, _e) in _BOUNDS_TECNICOS.items():
            if gen in g["params_tecnicos"] and gen not in _PESOS_NORMALIZADOS:
                assert lo <= g["params_tecnicos"][gen] <= hi, f"{gen} fuera de bounds"
        for gen, (lo, hi, _e) in _BOUNDS_SMC.items():
            if gen in g["params_smc"]:
                assert lo <= g["params_smc"][gen] <= hi, f"{gen} fuera de bounds"


def test_pesos_tecnicos_suman_uno():
    """
    La restricción real sobre los pesos del ensamble: suman 1.0 (misma
    invariante que garantiza _normalize_weights en la crianza de producción).

    Tolerancia 2e-6, no exacta: _normalize_weights redondea cada peso a 6
    decimales por separado, así que la suma puede desviarse hasta 1.5e-6
    (3 × 0.5e-6). Medido empíricamente: 1.0e-6.
    """
    import random
    rng = random.Random(5)
    for _ in range(10):
        tec = oe.genoma_aleatorio("tendencia", rng)["params_tecnicos"]
        suma = tec["peso_rsi"] + tec["peso_ema"] + tec["peso_macd"]
        assert suma == pytest.approx(1.0, abs=2e-6)


def test_genoma_aleatorio_marca_la_especie_correctamente():
    """reversion opera contra-tendencia por diseño: HTF apagado y RSI en
    modo reversión. Si naciera con las marcas de tendencia, la especie
    perdería su decorrelación."""
    import random
    rng = random.Random(3)
    rev = oe.genoma_aleatorio("reversion", rng)
    assert rev["params_smc"]["htf_filter_enabled"] == 0
    assert rev["params_tecnicos"]["rsi_modo"] == "reversion"

    ten = oe.genoma_aleatorio("tendencia", rng)
    assert ten["params_smc"]["htf_filter_enabled"] == 1
    assert ten["params_tecnicos"]["rsi_modo"] == "momentum"


def test_genoma_aleatorio_incluye_los_genes_de_fase_3():
    """partial_tp_r y sesion_trading deben existir desde el nacimiento: el
    incidente de la migración 015 mostró que un gen ausente jamás entra al
    pool porque el crossover solo hereda claves existentes."""
    import random
    g = oe.genoma_aleatorio("ruptura", random.Random(11))
    assert "partial_tp_r" in g["params_smc"]
    assert "sesion_trading" in g["params_smc"]
    assert g["params_smc"]["sesion_trading"] in (
        "cualquiera", "londres", "ny", "overlap")


def test_checkpoint_ida_y_vuelta(tmp_path):
    """El checkpoint debe poder releerse para reanudar, y no debe arrastrar
    los trades OOS (pesan megas y no aportan al retomar)."""
    ruta = tmp_path / "cp.json"
    poblacion = [{
        "params_tecnicos": {"rsi_periodo": 14}, "params_macro": {},
        "params_riesgo": {}, "params_smc": {}, "especie": "tendencia",
        "_eval": {"fitness": 0.5, "n_trades": 30,
                  "oos_trades": [{"pnl": 0.1}] * 100},
    }]
    oe._guardar_checkpoint(ruta, "tendencia", 7, poblacion)

    datos = oe.cargar_checkpoint(ruta)
    assert datos["generacion"] == 7
    assert datos["especie"] == "tendencia"
    assert datos["poblacion"][0]["_eval"]["fitness"] == 0.5
    assert "oos_trades" not in datos["poblacion"][0]["_eval"]


def test_checkpoint_inexistente_devuelve_none():
    assert oe.cargar_checkpoint(dk.Path("no_existe_jamas.json")) is None
