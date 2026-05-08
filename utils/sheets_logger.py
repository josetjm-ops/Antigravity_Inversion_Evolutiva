"""
Logger a Google Sheets para operaciones y genealogía de agentes.

Credenciales (prioridad):
  1. GOOGLE_CREDENTIALS_JSON contiene el JSON completo de la service account
     (GitHub Actions secret — string multilínea o una sola línea).
  2. GOOGLE_CREDENTIALS_JSON es una ruta a un archivo .json en disco
     (desarrollo local).

Variables de entorno requeridas:
  GOOGLE_SHEET_ID           — ID del spreadsheet (extraer del URL)
  GOOGLE_CREDENTIALS_JSON   — JSON de service account O ruta al archivo
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Headers de cada pestaña ────────────────────────────────────────────────────

_HEADERS_OPS = [
    "ID", "Agente ID", "Generación", "Timestamp Entrada (UTC)", "Acción",
    "Precio Entrada", "SL", "TP", "Pips SL", "R:R",
    "Capital Usado ($)", "Estado", "Timestamp Salida (UTC)", "Precio Salida",
    "P&G ($)", "P&G %",
    "Confianza Final", "Confianza Técnica", "Confianza Macro",
    "RSI", "FVG Activo", "FVG Dirección", "FVG Pips",
    "OB Activo", "OB Dirección", "Range Spike",
    "Razonamiento LLM",
]

_HEADERS_AGENTS = [
    "ID", "Generación", "Tipo Origen", "Fecha Nacimiento",
    "Padre 1", "Padre 2",
    "Estado", "Fecha Eliminación", "Razón Eliminación",
    "ROI Total (%)", "Fitness (Calmar)", "Win Rate (%)",
    "Ops Total", "Capital Inicial ($)", "Capital Final ($)",
    "FVG min pips", "OB impulso pips", "R:R target",
    "Cuarentena (min)", "peso_fvg", "peso_ob",
]

# ── Helpers de columnas ────────────────────────────────────────────────────────

def _col_letter(n: int) -> str:
    """Convierte índice 1-based a letra de columna de Sheets: 1→A, 27→AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# Índices 1-based de columnas en "Operaciones"
_COL_OPS = {h: i + 1 for i, h in enumerate(_HEADERS_OPS)}
# Índices 1-based de columnas en "Agentes"
_COL_AGT = {h: i + 1 for i, h in enumerate(_HEADERS_AGENTS)}

# Letras de columnas clave para las fórmulas P&G
_L_ACCION     = _col_letter(_COL_OPS["Acción"])           # E
_L_P_ENT      = _col_letter(_COL_OPS["Precio Entrada"])   # F
_L_CAPITAL    = _col_letter(_COL_OPS["Capital Usado ($)"]) # K
_L_ESTADO     = _col_letter(_COL_OPS["Estado"])           # L
_L_P_SAL      = _col_letter(_COL_OPS["Precio Salida"])    # N
_L_PNL        = _col_letter(_COL_OPS["P&G ($)"])          # O


def _pnl_formula(n: int) -> str:
    """Fórmula P&G ($) para la fila `n` usando GOOGLEFINANCE cuando está abierta."""
    return (
        f'=IF({_L_ESTADO}{n}="cancelada",0,'
        f'IF({_L_ESTADO}{n}="abierta",'
        f'IF({_L_ACCION}{n}="BUY",(GOOGLEFINANCE("CURRENCY:EURUSD")-{_L_P_ENT}{n})/{_L_P_ENT}{n}*{_L_CAPITAL}{n},'
        f'({_L_P_ENT}{n}-GOOGLEFINANCE("CURRENCY:EURUSD"))/{_L_P_ENT}{n}*{_L_CAPITAL}{n}),'
        f'IF({_L_ACCION}{n}="BUY",({_L_P_SAL}{n}-{_L_P_ENT}{n})/{_L_P_ENT}{n}*{_L_CAPITAL}{n},'
        f'({_L_P_ENT}{n}-{_L_P_SAL}{n})/{_L_P_ENT}{n}*{_L_CAPITAL}{n})))'
    )


def _pnl_pct_formula(n: int) -> str:
    """Fórmula P&G % para la fila `n`."""
    return f'=IF({_L_CAPITAL}{n}=0,0,{_L_PNL}{n}/{_L_CAPITAL}{n}*100)'


# ── Singleton ──────────────────────────────────────────────────────────────────

class SheetsLogger:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_client()
        return cls._instance

    # ── Inicialización ─────────────────────────────────────────────────────

    def _init_client(self) -> None:
        self.client      = None
        self.ws_ops      = None
        self.ws_agents   = None
        self.spreadsheet = None

        self.sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
        if not self.sheet_id:
            log.warning("[SheetsLogger] GOOGLE_SHEET_ID no configurado. Sheets deshabilitado.")
            return

        creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
        if not creds_env:
            log.warning("[SheetsLogger] GOOGLE_CREDENTIALS_JSON no configurado. Sheets deshabilitado.")
            return

        try:
            # Prioridad 1: contenido JSON directo (GitHub Actions secret)
            info  = json.loads(creds_env)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        except (json.JSONDecodeError, ValueError):
            # Prioridad 2: ruta a archivo JSON (desarrollo local)
            if not os.path.exists(creds_env):
                log.warning(
                    "[SheetsLogger] Credenciales no encontradas en '%s'. Sheets deshabilitado.",
                    creds_env,
                )
                return
            creds = Credentials.from_service_account_file(creds_env, scopes=SCOPES)

        try:
            self.client      = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(self.sheet_id)
            self._ensure_worksheets()
            log.info("[SheetsLogger] Conectado a Google Sheets correctamente.")
        except Exception as exc:
            log.error("[SheetsLogger] Error inicializando cliente Sheets: %s", exc)
            self.client = None

    # ── Gestión de pestañas ────────────────────────────────────────────────

    def _ensure_worksheets(self) -> None:
        self.ws_ops    = self._get_or_create_sheet("Operaciones", _HEADERS_OPS)
        self.ws_agents = self._get_or_create_sheet("Agentes",     _HEADERS_AGENTS)

    def _get_or_create_sheet(self, title: str, headers: list[str]):
        try:
            ws = self.spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(
                title=title, rows=2000, cols=len(headers)
            )
            ws.update("A1", [headers], value_input_option="USER_ENTERED")
            log.info("[SheetsLogger] Pestaña '%s' creada con headers.", title)
            return ws

        # Validar headers — si no coinciden, reescribir fila 1
        try:
            existing = ws.row_values(1)
        except Exception:
            existing = []
        if existing != headers:
            end_col = _col_letter(len(headers))
            ws.update(f"A1:{end_col}1", [headers], value_input_option="USER_ENTERED")
            log.info("[SheetsLogger] Headers de '%s' actualizados.", title)
        return ws

    # ── Operaciones ────────────────────────────────────────────────────────

    def log_operation(
        self,
        op_id: int,
        decision: dict,
        precio_entrada: float,
        timestamp_entrada: datetime,
        capital_usado: float,
        pips_sl: float | None = None,
    ) -> None:
        """Registra una nueva operación (BUY/SELL/HOLD) en la pestaña Operaciones."""
        if not self.client or self.ws_ops is None:
            return
        try:
            indicadores: dict = {}
            senal_tec = decision.get("senal_tecnico")
            if isinstance(senal_tec, dict):
                indicadores = senal_tec.get("indicadores", {})

            accion    = decision.get("accion_final", "HOLD")
            estado    = "cancelada" if accion == "HOLD" else "abierta"
            sl        = decision.get("stop_loss")  or ""
            tp        = decision.get("take_profit") or ""

            # Calcular Pips SL si no viene explícito
            if pips_sl is None and sl and precio_entrada:
                try:
                    pips_sl = round(abs(float(precio_entrada) - float(sl)) * 10000, 1)
                except Exception:
                    pips_sl = ""

            # R:R calculado
            rr = ""
            if sl and tp and precio_entrada:
                try:
                    sl_dist = abs(float(precio_entrada) - float(sl))
                    tp_dist = abs(float(tp) - float(precio_entrada))
                    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else ""
                except Exception:
                    pass

            ts_str = (
                timestamp_entrada.isoformat()
                if hasattr(timestamp_entrada, "isoformat")
                else str(timestamp_entrada)
            )

            # Número de fila que ocupará esta nueva fila
            n = len(self.ws_ops.col_values(1)) + 1  # col A incluye header

            row = [
                op_id,
                decision.get("agente_id", ""),
                "",                                 # Generación (backfill lo llena)
                ts_str,
                accion,
                precio_entrada,
                sl,
                tp,
                pips_sl if pips_sl != "" else "",
                rr,
                capital_usado,
                estado,
                "",                                 # Timestamp Salida (vacío al abrir)
                "",                                 # Precio Salida (vacío al abrir)
                _pnl_formula(n),
                _pnl_pct_formula(n),
                decision.get("confianza_final", ""),
                decision.get("confianza_tecnica", ""),
                decision.get("confianza_macro", ""),
                _safe_float(indicadores.get("rsi")),
                str(indicadores.get("fvg_activo", False)),
                indicadores.get("fvg_direccion", "NONE"),
                _safe_float(indicadores.get("fvg_pips")),
                str(indicadores.get("ob_activo", False)),
                indicadores.get("ob_direccion", "NONE"),
                str(indicadores.get("range_spike", False)),
                decision.get("razonamiento", ""),
            ]

            self.ws_ops.append_row(row, value_input_option="USER_ENTERED")
            log.info("[SheetsLogger] Operación %s registrada en Sheets.", op_id)
        except Exception as exc:
            log.error("[SheetsLogger] Error registrando operación %s: %s", op_id, exc)

    def update_operation(
        self,
        op_id: int,
        precio_salida: float,
        pnl: float,
        timestamp_salida: datetime | None = None,
    ) -> None:
        """Cierra una operación: actualiza Estado, Timestamp Salida y Precio Salida.
        La fórmula P&G (col O) se recalcula automáticamente al cambiar Estado a 'cerrada'."""
        if not self.client or self.ws_ops is None:
            return
        try:
            cell = self.ws_ops.find(str(op_id), in_column=_COL_OPS["ID"])
            if not cell:
                log.warning("[SheetsLogger] Operación %s no encontrada en Sheets.", op_id)
                return
            ts_str = (
                timestamp_salida.isoformat()
                if (timestamp_salida and hasattr(timestamp_salida, "isoformat"))
                else ""
            )
            # Actualiza columnas L (Estado) → M (Timestamp Salida) → N (Precio Salida)
            l_col = _col_letter(_COL_OPS["Estado"])
            n_col = _col_letter(_COL_OPS["Precio Salida"])
            self.ws_ops.update(
                f"{l_col}{cell.row}:{n_col}{cell.row}",
                [["cerrada", ts_str, precio_salida]],
                value_input_option="USER_ENTERED",
            )
            log.info("[SheetsLogger] Operación %s cerrada en Sheets.", op_id)
        except gspread.exceptions.CellNotFound:
            log.warning("[SheetsLogger] Operación %s no encontrada en Sheets.", op_id)
        except Exception as exc:
            log.error("[SheetsLogger] Error cerrando operación %s: %s", op_id, exc)

    # ── Agentes ────────────────────────────────────────────────────────────

    def log_agent(self, agent_data: dict) -> None:
        """Registra un nuevo agente en la pestaña Agentes (árbol genealógico)."""
        if not self.client or self.ws_agents is None:
            return
        try:
            smc  = agent_data.get("params_smc") or {}
            ops  = int(agent_data.get("operaciones_total", 0) or 0)
            won  = int(agent_data.get("operaciones_ganadoras", 0) or 0)
            wr   = round(won / ops * 100, 2) if ops > 0 else 0.0

            fecha_nac = agent_data.get("fecha_nacimiento")
            fecha_str = (
                fecha_nac.isoformat()
                if hasattr(fecha_nac, "isoformat")
                else str(fecha_nac or "")
            )
            tipo = (
                "Génesis"
                if not agent_data.get("padre_1_id")
                else f"Mutante Gen-{agent_data.get('generacion', '?')}"
            )

            row = [
                agent_data.get("id", ""),
                agent_data.get("generacion", ""),
                tipo,
                fecha_str,
                agent_data.get("padre_1_id") or "",
                agent_data.get("padre_2_id") or "",
                agent_data.get("estado", "activo"),
                str(agent_data.get("fecha_eliminacion") or ""),
                agent_data.get("razon_eliminacion") or "",
                _safe_float(agent_data.get("roi_total"), decimals=4),
                _safe_float(agent_data.get("fitness_score"), decimals=6),
                wr,
                ops,
                _safe_float(agent_data.get("capital_inicial", 10.0), decimals=4),
                _safe_float(agent_data.get("capital_actual", 10.0), decimals=4),
                smc.get("fvg_min_pips", ""),
                smc.get("ob_impulse_pips", ""),
                smc.get("risk_reward_target", ""),
                smc.get("macro_quarantine_minutes", ""),
                smc.get("peso_fvg", ""),
                smc.get("peso_ob", ""),
            ]
            self.ws_agents.append_row(row, value_input_option="USER_ENTERED")
            log.info("[SheetsLogger] Agente %s registrado en Sheets.", agent_data.get("id"))
        except Exception as exc:
            log.error("[SheetsLogger] Error registrando agente %s: %s", agent_data.get("id"), exc)

    def update_agent_status(
        self,
        agent_id: str,
        status: str,
        roi: float | None = None,
        ops: int | None = None,
        ops_ganadoras: int | None = None,
        fitness: float | None = None,
        fecha_eliminacion: str | None = None,
        razon_eliminacion: str | None = None,
        capital_final: float | None = None,
    ) -> None:
        """Actualiza Estado, ROI, Fitness y datos de eliminación de un agente."""
        if not self.client or self.ws_agents is None:
            return
        try:
            cell = self.ws_agents.find(str(agent_id), in_column=_COL_AGT["ID"])
            if not cell:
                log.warning("[SheetsLogger] Agente %s no encontrado en Sheets.", agent_id)
                return
            r = cell.row

            def _upd(header: str, value):
                if value is not None:
                    return {"range": f"{_col_letter(_COL_AGT[header])}{r}", "values": [[value]]}
                return None

            updates = list(filter(None, [
                _upd("Estado",            status),
                _upd("Fecha Eliminación", fecha_eliminacion or ""),
                _upd("Razón Eliminación", razon_eliminacion or ""),
                _upd("ROI Total (%)",     round(float(roi), 4) if roi is not None else None),
                _upd("Fitness (Calmar)",  round(float(fitness), 6) if fitness is not None else None),
                _upd("Ops Total",         ops),
                _upd("Capital Final ($)", round(float(capital_final), 4) if capital_final is not None else None),
            ]))
            if ops is not None and ops_ganadoras is not None and ops > 0:
                wr_upd = _upd("Win Rate (%)", round(ops_ganadoras / ops * 100, 2))
                if wr_upd:
                    updates.append(wr_upd)

            if updates:
                self.ws_agents.batch_update(updates)
            log.info("[SheetsLogger] Agente %s actualizado en Sheets → %s.", agent_id, status)
        except gspread.exceptions.CellNotFound:
            log.warning("[SheetsLogger] Agente %s no encontrado en Sheets.", agent_id)
        except Exception as exc:
            log.error("[SheetsLogger] Error actualizando agente %s: %s", agent_id, exc)


# ── Helper interno ─────────────────────────────────────────────────────────────

def _safe_float(value, decimals: int = 4):
    """Convierte a float redondeado o devuelve '' si el valor es None/falsy."""
    if value is None:
        return ""
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return ""
