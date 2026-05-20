-- ============================================================
-- INVERSIÓN EVOLUTIVA — Migración 007
-- Constraint: un agente solo puede tener 1 posición abierta
-- ============================================================
-- El INSERT ... WHERE NOT EXISTS en investor_agent.py no es
-- completamente atómico bajo concurrencia READ COMMITTED.
-- Este índice parcial único garantiza a nivel de BD que dos
-- workers simultáneos no puedan crear 2 posiciones abiertas
-- para el mismo agente.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_open_position_per_agent
    ON operaciones(agente_id)
    WHERE estado = 'abierta' AND accion IN ('BUY', 'SELL');
