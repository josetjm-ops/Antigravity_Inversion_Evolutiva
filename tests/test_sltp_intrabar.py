"""
Tests del verificador intra-vela de SL/TP (Sesión 12 — fix sesgo snapshot).

Cubre:
  1. Unitarios de check_sl_tp_intrabar (8 casos: BUY/SELL × {solo TP, solo SL,
     ambos, ninguno}).
  2. Integración del loop _verify_position_intrabar con velas sintéticas:
     - Caso #2803: SELL cuyo TP fue tocado intra-vela y debió cerrar en TP
       (no en SL trailing).
     - Trailing intra-vela progresivo: SL se aprieta vela a vela.
     - Fallback: sin velas OHLC, cae al check snapshot legacy.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.simulated_broker import check_sl_tp_intrabar


# ─────────────────────────────────────────────────────────────────────────────
# 1. Unitarios de check_sl_tp_intrabar
# ─────────────────────────────────────────────────────────────────────────────

def _candle(open_, high, low, close):
    return {"open": open_, "high": high, "low": low, "close": close,
            "timestamp": datetime(2026, 5, 27, tzinfo=timezone.utc)}


class TestCheckSlTpIntrabar:
    """8 casos canónicos: BUY/SELL × {solo TP, solo SL, ambos, ninguno}."""

    # ── BUY (entry < TP, SL < entry) ──────────────────────────────────────────
    def test_buy_only_tp(self):
        # TP=1.0900, SL=1.0800; vela toca solo TP
        assert check_sl_tp_intrabar(
            "BUY", 1.0800, 1.0900, _candle(1.0850, 1.0910, 1.0845, 1.0905)
        ) == "HIT_TP"

    def test_buy_only_sl(self):
        assert check_sl_tp_intrabar(
            "BUY", 1.0800, 1.0900, _candle(1.0850, 1.0860, 1.0795, 1.0810)
        ) == "HIT_SL"

    def test_buy_both_hit_sl_wins(self):
        # Vela amplia que toca SL y TP en el mismo minuto → SL gana (peor caso)
        assert check_sl_tp_intrabar(
            "BUY", 1.0800, 1.0900, _candle(1.0850, 1.0910, 1.0790, 1.0860)
        ) == "HIT_SL"

    def test_buy_neither(self):
        assert check_sl_tp_intrabar(
            "BUY", 1.0800, 1.0900, _candle(1.0850, 1.0870, 1.0830, 1.0855)
        ) == "OPEN"

    # ── SELL (entry > TP, SL > entry) ─────────────────────────────────────────
    def test_sell_only_tp(self):
        # SELL @ 1.16496, SL=1.16585 (encima), TP=1.16263 (debajo)
        assert check_sl_tp_intrabar(
            "SELL", 1.16585, 1.16263, _candle(1.16496, 1.16500, 1.16250, 1.16300)
        ) == "HIT_TP"

    def test_sell_only_sl(self):
        assert check_sl_tp_intrabar(
            "SELL", 1.16585, 1.16263, _candle(1.16496, 1.16600, 1.16400, 1.16550)
        ) == "HIT_SL"

    def test_sell_both_hit_sl_wins(self):
        assert check_sl_tp_intrabar(
            "SELL", 1.16585, 1.16263, _candle(1.16496, 1.16600, 1.16250, 1.16450)
        ) == "HIT_SL"

    def test_sell_neither(self):
        assert check_sl_tp_intrabar(
            "SELL", 1.16585, 1.16263, _candle(1.16496, 1.16550, 1.16400, 1.16470)
        ) == "OPEN"

    def test_invalid_action_returns_open(self):
        assert check_sl_tp_intrabar(
            "HOLD", 1.0800, 1.0900, _candle(1.0850, 1.0910, 1.0790, 1.0860)
        ) == "OPEN"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Integración: _verify_position_intrabar
# ─────────────────────────────────────────────────────────────────────────────

def _make_op_sell_2803_like():
    """Reproduce la operación #2803: SELL @ 1.16496 con TP=1.16263."""
    return {
        "id": 2803,
        "agente_id": "2026-05-19_10",
        "accion": "SELL",
        "timestamp_entrada": datetime(2026, 5, 27, 7, 46, tzinfo=timezone.utc),
        "timestamp_ultima_verificacion": datetime(2026, 5, 27, 7, 46, tzinfo=timezone.utc),
        "precio_entrada": 1.16496,
        "capital_usado": 161.7714,
        # SL inicial por encima del entry (típico ATR para SELL)
        "stop_loss": 1.16585,
        "take_profit": 1.16263,
        "precio_extremo_favorable": 1.16496,
        "trailing_activation_pips": 15.0,
        "trailing_distance_pips": 10.0,
    }


def _candles_sell_hits_tp():
    """Serie de velas 1m: precio baja progresivamente y toca el TP 1.16263."""
    base = datetime(2026, 5, 27, 8, 0, tzinfo=timezone.utc)
    return [
        {"timestamp": base + timedelta(minutes=1),  "open": 1.16490, "high": 1.16495, "low": 1.16470, "close": 1.16475},
        {"timestamp": base + timedelta(minutes=2),  "open": 1.16475, "high": 1.16480, "low": 1.16440, "close": 1.16450},
        {"timestamp": base + timedelta(minutes=3),  "open": 1.16450, "high": 1.16455, "low": 1.16400, "close": 1.16410},
        # Esta vela toca el TP (low 1.16260 <= TP 1.16263)
        {"timestamp": base + timedelta(minutes=4),  "open": 1.16410, "high": 1.16415, "low": 1.16260, "close": 1.16300},
        {"timestamp": base + timedelta(minutes=5),  "open": 1.16300, "high": 1.16350, "low": 1.16290, "close": 1.16330},
    ]


def _candles_sell_no_hit_progressive_drop():
    """Velas que bajan sin tocar TP — solo activan trailing y aprietan SL."""
    base = datetime(2026, 5, 27, 8, 0, tzinfo=timezone.utc)
    return [
        # Profit >15 pips → activa trailing. low=1.16330 (16.6 pips profit) → SL trailing = 1.16430
        {"timestamp": base + timedelta(minutes=1), "open": 1.16480, "high": 1.16490, "low": 1.16330, "close": 1.16360},
        # Nuevo mínimo 1.16290 (20.6 pips) → SL trailing = 1.16390
        {"timestamp": base + timedelta(minutes=2), "open": 1.16360, "high": 1.16380, "low": 1.16290, "close": 1.16310},
        # Rebote que toca el SL trailing 1.16390 (high 1.16400 >= 1.16390)
        {"timestamp": base + timedelta(minutes=3), "open": 1.16310, "high": 1.16400, "low": 1.16300, "close": 1.16395},
    ]


class TestVerifyPositionIntrabar:
    """Integración del loop con _apply_trailing_stop y close_operation mockeados."""

    def _patch_close_and_capital(self):
        """Devuelve los context-managers necesarios para mockear cierre + capital."""
        # Mock InvestorAgent: capturamos los argumentos del close_operation
        agent_mock = MagicMock()
        agent_mock.close_operation.return_value = {
            "op_id": 2803, "pnl": 0.0, "pnl_pct": 0.0, "nuevo_capital": 100.0,
        }

        # Mock get_conn para devolver capital
        conn_mock = MagicMock()
        cursor_mock = MagicMock()
        cursor_mock.fetchone.return_value = {"capital_actual": 100.0}
        cursor_mock.execute = MagicMock()
        conn_mock.__enter__.return_value = conn_mock
        conn_mock.__exit__.return_value = False
        conn_mock.cursor.return_value = cursor_mock

        return agent_mock, conn_mock, cursor_mock

    def test_sell_tp_hit_intrabar_closes_at_tp_not_sl(self):
        """Caso #2803: TP=1.16263 fue tocado → debe cerrar en TP, no en SL trailing."""
        from cron import trade_monitor as tm

        op = _make_op_sell_2803_like()
        candles = _candles_sell_hits_tp()

        agent_mock, conn_mock, _ = self._patch_close_and_capital()

        with patch.object(tm, "_persist_trailing") as persist_mock, \
             patch("data.simulated_broker.get_intrabar_candles", return_value=candles), \
             patch("agents.investor_agent.InvestorAgent", return_value=agent_mock), \
             patch("db.connection.get_conn", return_value=conn_mock), \
             patch("db.connection.get_dict_cursor", return_value=conn_mock.cursor()):
            result = tm._verify_position_intrabar(op, fallback_price=1.16300)

        assert result["closed"] is True
        assert result["fallback"] is False
        # close_operation se llamó con precio_salida = TP exacto = 1.16263
        call_kwargs = agent_mock.close_operation.call_args.kwargs
        assert call_kwargs["precio_salida"] == pytest.approx(1.16263, abs=1e-6)
        # Y el timestamp_salida es el de la vela que tocó el TP (minute 4)
        assert call_kwargs["timestamp_salida"] == candles[3]["timestamp"]

    def test_sell_trailing_tightens_and_hits_sl(self):
        """Sin TP hit, el trailing aprieta el SL y eventualmente una vela lo toca."""
        from cron import trade_monitor as tm

        op = _make_op_sell_2803_like()
        candles = _candles_sell_no_hit_progressive_drop()

        agent_mock, conn_mock, _ = self._patch_close_and_capital()

        with patch.object(tm, "_persist_trailing") as persist_mock, \
             patch("data.simulated_broker.get_intrabar_candles", return_value=candles), \
             patch("agents.investor_agent.InvestorAgent", return_value=agent_mock), \
             patch("db.connection.get_conn", return_value=conn_mock), \
             patch("db.connection.get_dict_cursor", return_value=conn_mock.cursor()):
            result = tm._verify_position_intrabar(op, fallback_price=1.16400)

        assert result["closed"] is True
        # El SL trailing fue 1.16390 (extremo 1.16290 + 10 pips); vela 3 lo toca en high 1.16400
        call_kwargs = agent_mock.close_operation.call_args.kwargs
        assert call_kwargs["precio_salida"] == pytest.approx(1.16390, abs=1e-5)
        assert call_kwargs["timestamp_salida"] == candles[2]["timestamp"]

    def test_fallback_when_no_candles_uses_snapshot(self):
        """Sin velas OHLC, el verificador cae al check snapshot legacy."""
        from cron import trade_monitor as tm

        op = _make_op_sell_2803_like()
        agent_mock, conn_mock, _ = self._patch_close_and_capital()

        with patch.object(tm, "_persist_trailing"), \
             patch("data.simulated_broker.get_intrabar_candles", return_value=[]), \
             patch("agents.investor_agent.InvestorAgent", return_value=agent_mock), \
             patch("db.connection.get_conn", return_value=conn_mock), \
             patch("db.connection.get_dict_cursor", return_value=conn_mock.cursor()):
            # snapshot 1.16200 < TP 1.16263 → HIT_TP
            result = tm._verify_position_intrabar(op, fallback_price=1.16200)

        assert result["fallback"] is True
        assert result["closed"] is True
        call_kwargs = agent_mock.close_operation.call_args.kwargs
        assert call_kwargs["precio_salida"] == pytest.approx(1.16263, abs=1e-6)
        # En fallback no hay timestamp de vela → close_operation recibe None (usa now())
        assert call_kwargs["timestamp_salida"] is None

    def test_no_hit_advances_verification_cursor(self):
        """Si ninguna vela toca SL/TP, se persiste el avance del cursor."""
        from cron import trade_monitor as tm

        op = _make_op_sell_2803_like()
        # Velas suaves que no tocan ni SL ni TP ni activan trailing significativo
        base = datetime(2026, 5, 27, 8, 0, tzinfo=timezone.utc)
        candles = [
            {"timestamp": base + timedelta(minutes=1), "open": 1.16490, "high": 1.16500, "low": 1.16480, "close": 1.16495},
            {"timestamp": base + timedelta(minutes=2), "open": 1.16495, "high": 1.16510, "low": 1.16485, "close": 1.16500},
        ]
        agent_mock, conn_mock, _ = self._patch_close_and_capital()

        with patch.object(tm, "_persist_trailing") as persist_mock, \
             patch("data.simulated_broker.get_intrabar_candles", return_value=candles), \
             patch("agents.investor_agent.InvestorAgent", return_value=agent_mock), \
             patch("db.connection.get_conn", return_value=conn_mock), \
             patch("db.connection.get_dict_cursor", return_value=conn_mock.cursor()):
            result = tm._verify_position_intrabar(op, fallback_price=1.16500)

        assert result["closed"] is False
        assert result["candles_checked"] == 2
        # _persist_trailing fue llamado con since_ts = última vela procesada
        persist_mock.assert_called_once()
        kwargs = persist_mock.call_args.kwargs or {}
        # since_ts es el cuarto argumento posicional o kwarg
        args = persist_mock.call_args.args
        last_ts = kwargs.get("since_ts", args[3] if len(args) >= 4 else None)
        assert last_ts == candles[-1]["timestamp"]
        agent_mock.close_operation.assert_not_called()
