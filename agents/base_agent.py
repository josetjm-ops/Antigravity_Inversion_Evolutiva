"""
Clase base para todos los sub-agentes del sistema.
Encapsula el cliente DeepSeek y el patrón de razonamiento táctico.
DeepSeek es compatible con el SDK de OpenAI (mismo protocolo, distinto endpoint).
"""

import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)

_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── LLM fuera del camino de ejecución (Fase 3, rediseño 2026-07-02) ──────────
# Antes SubAgentTechnical/SubAgentRisk podían invocar al LLM en producción
# (banda de confianza ambigua / toda decisión BUY-SELL), pero el backtester
# SIEMPRE corre con reason() stubeado a HOLD determinista (ver
# evolution/backtester.py:_walk_forward_trades). Esa divergencia significa
# que el fitness OOS mide una política distinta a la desplegada, y además el
# LLM es no-determinista, no-backtesteable y con costo/latencia recurrente.
# Con este flag en False (default), SubAgentTechnical/SubAgentRisk usan
# SIEMPRE su heurística determinista — igual que el backtester. El Agente
# Juez (razonamiento narrativo post-hoc, no decide trades) NO usa este flag:
# sigue llamando a DeepSeek siempre, vía BaseAgent.reason() directamente.
LLM_EXECUTION_ENABLED = os.getenv("LLM_EXECUTION_ENABLED", "false").lower() == "true"


class BaseAgent:
    """
    Clase base con acceso al LLM DeepSeek.
    Cada sub-agente hereda y define su propio system_prompt y lógica de análisis.
    """

    role: str = "base"
    system_prompt: str = "Eres un asistente de análisis financiero."

    def __init__(self, agent_id: str, params: dict):
        self.agent_id = agent_id
        self.params = params

    def reason(self, user_message: str, context: dict | None = None) -> str:
        """
        Llama a DeepSeek con el system_prompt del sub-agente y el mensaje del usuario.
        Devuelve el texto de razonamiento del modelo.
        """
        messages = [{"role": "system", "content": self.system_prompt}]
        if context:
            messages.append({
                "role": "user",
                "content": f"Contexto adicional:\n{context}\n\n{user_message}",
            })
        else:
            messages.append({"role": "user", "content": user_message})

        response = _client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=512,
            timeout=30.0,
        )
        return response.choices[0].message.content.strip()

    def analyze(self) -> dict[str, Any]:
        raise NotImplementedError("Cada sub-agente debe implementar analyze()")
