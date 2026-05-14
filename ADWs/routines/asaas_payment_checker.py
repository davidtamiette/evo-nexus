#!/usr/bin/env python3
"""ADW: Asaas Payment Checker — vencimentos hoje + inadimplência D+2/D+5 (roda às 08:00, systematic, no AI)"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "dashboard", "backend"))

from ADWs.runner import run_script, banner, summary

# Carrega .env
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


def main():
    banner("💳  Asaas Payment Checker", "Vencimentos hoje • Inadimplência D+2/D+5 • Alerta dono")
    from asaas_payment_checker import tick_daily

    def _run():
        result = tick_daily()
        if result.get("status") == "skip":
            return {"ok": True, "summary": f"skip — {result.get('reason', '')}"}
        if result.get("status") == "error":
            return {"ok": False, "summary": f"error — {result.get('reason', 'unknown')}"}

        parts = []
        upcoming = result.get("upcoming", {})
        if upcoming.get("new", 0):
            parts.append(f"aviso antecipado={upcoming['new']}")
        due = result.get("due_today", {})
        if due.get("new", 0):
            parts.append(f"vencendo hoje={due['new']}")
        overdue = result.get("overdue", {})
        if overdue.get("sent"):
            parts.append(f"atraso notif={len(overdue['sent'])}")
        admin = result.get("admin_alerts", [])
        if admin:
            parts.append(f"alertas dono={len(admin)}")

        return {"ok": True, "summary": " | ".join(parts) or "work — nenhum evento novo"}

    results = [run_script(_run, log_name="asaas-daily", timeout=120)]
    summary(results, "Asaas Payment Checker")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelado.")
