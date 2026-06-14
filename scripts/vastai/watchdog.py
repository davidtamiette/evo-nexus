#!/usr/bin/env python3
"""
watchdog.py — ComfyUI idle shutdown watchdog
Monitora /queue do ComfyUI. Após IDLE_MINUTES sem requisição,
envia notificação WhatsApp (NOTIFICACAO-COGNITIVA) e executa shutdown.

Uso: python3 watchdog.py [--idle-minutes 15] [--comfy-port 8188]

Env vars usadas (lidas de /workspace/.env se existir):
  EVOLUTION_API_URL    — URL base da Evolution API (ex: https://api.evonexus.cognitiva.ai)
  EVOLUTION_API_KEY    — Bearer token Evolution
  WATCHDOG_PHONE       — Número destino (default: 5531982302185)
  WATCHDOG_INSTANCE    — Instância Evolution (default: NOTIFICACAO-COGNITIVA)
"""

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

WORKSPACE = Path(os.environ.get("COMFY_WORKSPACE", "/workspace"))
ENV_FILE = WORKSPACE / ".env"
SHUTDOWN_LOG = WORKSPACE / "logs" / "last_idle_shutdown.json"


def load_env_file(path: Path) -> dict[str, str]:
    """Carrega variáveis de um arquivo .env simples (sem expandir $VAR)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def query_comfy_queue(port: int) -> dict | None:
    """
    GET http://localhost:{port}/queue
    Retorna o JSON ou None em caso de erro.
    """
    url = f"http://localhost:{port}/queue"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning("Erro ao consultar /queue: %s", e)
        return None


def estimate_session_cost(boot_file: Path) -> str:
    """Calcula custo estimado da sessão com base no tempo desde o boot."""
    # Estimativa conservadora: $0.47/hr (RTX 5090 interruptible)
    COST_PER_HOUR = 0.47
    try:
        data = json.loads(boot_file.read_text())
        boot_at = datetime.datetime.fromisoformat(data["boot_at"].replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        hours = (now - boot_at).total_seconds() / 3600
        cost = hours * COST_PER_HOUR
        return f"${cost:.2f} (~{hours:.1f}h @ ~${COST_PER_HOUR}/hr)"
    except Exception:
        return "desconhecido"


def send_whatsapp_notification(
    evo_url: str,
    evo_key: str,
    phone: str,
    instance: str,
    message: str,
) -> bool:
    """Envia mensagem WhatsApp via Evolution API."""
    url = f"{evo_url.rstrip('/')}/message/sendText/{instance}"
    payload = json.dumps({
        "number": phone,
        "text": message,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "apikey": evo_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            log.info("WhatsApp enviado: %s", resp.get("key", {}).get("id", "ok"))
            return True
    except urllib.error.HTTPError as e:
        body = e.read(500).decode(errors="replace")
        log.error("Erro ao enviar WhatsApp HTTP %s: %s", e.code, body)
        return False
    except Exception as e:
        log.error("Erro ao enviar WhatsApp: %s", e)
        return False


def run_shutdown(dry_run: bool = False) -> None:
    """Executa shutdown -h now (ou apenas loga em dry_run)."""
    if dry_run:
        log.info("[DRY RUN] Executaria: shutdown -h now")
        return
    log.info("Executando: shutdown -h now")
    try:
        subprocess.run(["shutdown", "-h", "now"], check=True)
    except Exception as e:
        log.error("Erro no shutdown: %s — tentando halt", e)
        subprocess.run(["halt"], check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="ComfyUI idle watchdog")
    parser.add_argument("--idle-minutes", type=int, default=15,
                        help="Minutos de idle antes do shutdown (default: 15)")
    parser.add_argument("--comfy-port", type=int, default=8188,
                        help="Porta do ComfyUI (default: 8188)")
    parser.add_argument("--poll-seconds", type=int, default=60,
                        help="Intervalo de poll em segundos (default: 60)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Loga shutdown sem executar")
    args = parser.parse_args()

    # Carregar .env do workspace (tokens Evolution)
    env_vars = load_env_file(ENV_FILE)
    # Variáveis de ambiente do processo têm prioridade
    evo_url = os.environ.get("EVOLUTION_API_URL") or env_vars.get("EVOLUTION_API_URL", "")
    evo_key = os.environ.get("EVOLUTION_API_KEY") or env_vars.get("EVOLUTION_API_KEY", "")
    phone = os.environ.get("WATCHDOG_PHONE") or env_vars.get("WATCHDOG_PHONE", "5531982302185")
    instance = os.environ.get("WATCHDOG_INSTANCE") or env_vars.get("WATCHDOG_INSTANCE", "NOTIFICACAO-COGNITIVA")

    idle_threshold = args.idle_minutes * 60  # em segundos
    log.info(
        "Watchdog iniciado | idle=%dmin | poll=%ds | port=%d | dry_run=%s",
        args.idle_minutes, args.poll_seconds, args.comfy_port, args.dry_run,
    )

    last_activity_at = time.monotonic()
    boot_info_file = WORKSPACE / "logs" / "boot_info.json"

    while True:
        time.sleep(args.poll_seconds)

        queue = query_comfy_queue(args.comfy_port)
        if queue is None:
            # ComfyUI não respondeu — pode estar carregando ainda, não contar como idle
            log.warning("ComfyUI não respondeu — resetting idle timer")
            last_activity_at = time.monotonic()
            continue

        running = queue.get("queue_running", [])
        pending = queue.get("queue_pending", [])
        is_idle = len(running) == 0 and len(pending) == 0

        if not is_idle:
            last_activity_at = time.monotonic()
            log.info("Queue ativa: %d running, %d pending — idle timer resetado", len(running), len(pending))
            continue

        idle_seconds = time.monotonic() - last_activity_at
        idle_minutes = idle_seconds / 60
        log.info("Idle há %.1fmin / %dmin threshold", idle_minutes, args.idle_minutes)

        if idle_seconds < idle_threshold:
            continue

        # ─── Idle threshold atingido ───────────────────────────────────────────
        log.info("IDLE THRESHOLD ATINGIDO — iniciando sequência de shutdown")

        cost_estimate = estimate_session_cost(boot_info_file)
        message = (
            f"*vast.ai desligando por idle*\n\n"
            f"ComfyUI ficou {args.idle_minutes}min sem requisições.\n"
            f"Custo estimado da sessão: {cost_estimate}\n\n"
            f"Instância sendo desligada agora. Para usar novamente, inicie via `/gerar`."
        )

        # Gravar log de shutdown antes de notificar (evidência para Oath)
        SHUTDOWN_LOG.parent.mkdir(parents=True, exist_ok=True)
        shutdown_record = {
            "shutdown_at": datetime.datetime.utcnow().isoformat() + "Z",
            "idle_minutes": args.idle_minutes,
            "idle_seconds_actual": round(idle_seconds),
            "cost_estimate": cost_estimate,
            "dry_run": args.dry_run,
        }
        SHUTDOWN_LOG.write_text(json.dumps(shutdown_record, indent=2))
        log.info("shutdown log gravado em %s", SHUTDOWN_LOG)

        # Notificação WhatsApp
        if evo_url and evo_key:
            log.info("Enviando notificação WhatsApp → %s via %s", phone, instance)
            sent = send_whatsapp_notification(evo_url, evo_key, phone, instance, message)
            if sent:
                log.info("Notificação enviada com sucesso")
                # Aguardar 10s para a mensagem ser entregue antes do shutdown
                time.sleep(10)
            else:
                log.warning("Falha na notificação WhatsApp — prosseguindo com shutdown mesmo assim")
        else:
            log.warning(
                "EVOLUTION_API_URL ou EVOLUTION_API_KEY não configurados — shutdown sem notificação"
            )

        run_shutdown(dry_run=args.dry_run)

        if args.dry_run:
            # Em dry_run, resetar timer pra continuar monitorando
            log.info("[DRY RUN] Resetando idle timer para continuar teste")
            last_activity_at = time.monotonic()


if __name__ == "__main__":
    main()
