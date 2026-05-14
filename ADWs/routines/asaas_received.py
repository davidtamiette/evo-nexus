#!/usr/bin/env python3
"""ADW: Asaas Received — confirmação de pagamentos recebidos a cada 5 minutos (systematic, no AI)"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "dashboard", "backend"))

from ADWs.runner import run_script, banner, summary

_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()


def main():
    banner("💸  Asaas Received", "Confirmação de pagamentos recebidos")
    from asaas_payment_checker import tick_received

    def _run():
        result = tick_received()
        if result.get("status") == "skip":
            return {"ok": True, "summary": f"skip — {result.get('reason', '')}"}
        sent = result.get("sent", [])
        return {"ok": True, "summary": f"recebidos={result.get('new', 0)} | enviados={len(sent)}"}

    results = [run_script(_run, log_name="asaas-received", timeout=60)]
    summary(results, "Asaas Received")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelado.")
