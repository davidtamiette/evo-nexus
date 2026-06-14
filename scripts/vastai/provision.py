#!/usr/bin/env python3
"""
provision.py — Cria template vast.ai e instância de teste para ComfyUI.

Uso:
  python3 scripts/vastai/provision.py --action create-template
  python3 scripts/vastai/provision.py --action create-instance [--offer-id ID]
  python3 scripts/vastai/provision.py --action list-offers
  python3 scripts/vastai/provision.py --action destroy-instance --instance-id ID
  python3 scripts/vastai/provision.py --action status --instance-id ID

Escreve em .env:
  VAST_AI_TEMPLATE_ID      — hash_id do template criado
  VAST_AI_INSTANCE_ID      — ID da instância de teste ativa
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path("/Users/david/evonexus/.env")
ONSTART_SCRIPT = Path("/Users/david/evonexus/scripts/vastai/onstart.sh")

# Imagem base: PyTorch 2.4 + CUDA 12.4 (compatível com Flux.2)
IMAGE = "pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime"

# Porta ComfyUI exposta
COMFY_PORT = 8188


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def update_env_var(key: str, value: str) -> None:
    """Atualiza ou adiciona uma variável no .env sem sobrescrever o resto."""
    lines = ENV_FILE.read_text().splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    print(f"  .env: {key}={value}")


def vast_request(
    method: str,
    path: str,
    api_key: str,
    payload: dict | None = None,
) -> dict:
    url = f"https://console.vast.ai/api/v0{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read(2000).decode(errors="replace")
        raise RuntimeError(f"vast.ai {method} {path} → HTTP {e.code}: {body}") from e


# ── Actions ────────────────────────────────────────────────────────────────────

def action_list_offers(api_key: str, min_vram_gb: int = 24) -> list[dict]:
    """Lista ofertas on-demand com ≥ min_vram_gb VRAM, ordenadas por preço."""
    q = {
        "rentable": {"eq": True},
        "rented": {"eq": False},
        "gpu_ram": {"gte": min_vram_gb * 1024},
        "cuda_max_good": {"gte": 12.0},
        "type": "on-demand",
        "num_gpus": {"eq": 1},
        "disk_space": {"gte": 50},
    }
    url_path = f"/bundles/?q={urllib.parse.quote(json.dumps(q))}"
    data = vast_request("GET", url_path, api_key)
    offers = data.get("offers", [])
    offers.sort(key=lambda o: o.get("dph_total", 999))
    return offers


def action_create_template(api_key: str) -> str:
    """
    Cria template vast.ai com imagem base ComfyUI.
    Retorna o hash_id do template criado.

    Nota: env em templates é string no formato Docker flag ("-e VAR=val -p 8000:8000"),
    diferente de create-instance que aceita dict.
    """
    onstart = ONSTART_SCRIPT.read_text()

    # Env como string Docker flag — formato obrigatório para templates
    env_str = (
        f"-e COMFY_PORT={COMFY_PORT} "
        f"-e COMFY_WORKSPACE=/workspace "
        f"-e IDLE_SHUTDOWN_MINUTES=15 "
        f"-p {COMFY_PORT}:{COMFY_PORT}"
    )

    payload = {
        "name": "cognitiva-comfyui-flux2",
        "desc": "ComfyUI + Flux.2 fp8 para geração de imagens sob demanda (Cognitiva AI)",
        "image": IMAGE,
        "onstart": onstart,
        "runtype": "ssh",
        "ssh_direct": True,
        "use_ssh": True,
        "jup_direct": False,
        "use_jupyter_lab": False,
        "env": env_str,
        "recommended_disk_space": 250,
        "docker_login_repo": "",
        "docker_login_user": "",
        "docker_login_pass": "",
        "private": True,
        "extra_filters": {"cuda_max_good": {"gte": 12.0}},
    }

    print("Criando template vast.ai...")
    print(f"  image: {IMAGE}")
    print(f"  env: {env_str}")
    print(f"  onstart: {len(onstart)} chars")

    result = vast_request("POST", "/template/", api_key, payload)
    print(f"  Resposta: {json.dumps(result, indent=2)}")

    template_hash = (
        result.get("template_hash_id")
        or result.get("hash_id")
        or result.get("id")
    )
    if not template_hash:
        raise RuntimeError(f"template hash_id não encontrado na resposta: {result}")

    update_env_var("VAST_AI_TEMPLATE_ID", str(template_hash))
    print(f"\nTemplate criado: {template_hash}")
    return str(template_hash)


def action_create_instance(api_key: str, offer_id: int | None, template_hash: str | None) -> int:
    """
    Cria instância de teste. Se offer_id não for fornecido, escolhe o mais barato disponível.
    Retorna instance_id.
    """
    onstart = ONSTART_SCRIPT.read_text()

    if offer_id is None:
        print("Buscando melhor oferta disponível (RTX 4090 ou superior)...")
        offers = action_list_offers(api_key, min_vram_gb=24)
        if not offers:
            raise RuntimeError("Nenhuma oferta disponível com ≥24GB VRAM")

        # Preferência: RTX 5090 (Cognitiva padrão pra Flux.2 2K), depois fallback 4090/A40
        preferred_order = ["RTX 5090", "RTX 4090", "A40", "RTX A6000"]
        chosen = None
        for pref in preferred_order:
            for o in offers:
                if pref in o.get("gpu_name", "") and o.get("dph_total", 999) < 1.0:
                    chosen = o
                    break
            if chosen:
                break
        if not chosen:
            chosen = offers[0]

        offer_id = chosen["id"]
        print(f"  Oferta escolhida: id={offer_id} gpu={chosen['gpu_name']} dph=${chosen['dph_total']:.3f}/hr")

    # Injetar o token do Cloudflare Tunnel na instância via env
    # (não fica no template — o template é compartilhável)
    env_dict: dict = {
        f"-p {COMFY_PORT}:{COMFY_PORT}": "1",
        "COMFY_PORT": str(COMFY_PORT),
        "COMFY_WORKSPACE": "/workspace",
        "IDLE_SHUTDOWN_MINUTES": "15",
    }
    cf_token = load_env().get("CLOUDFLARE_TUNNEL_TOKEN", "")
    if cf_token:
        env_dict["CLOUDFLARE_TUNNEL_TOKEN"] = cf_token

    # Injetar credenciais Evolution API para o watchdog notificar via WhatsApp
    evo_env = load_env()
    if evo_env.get("EVOLUTION_API_URL"):
        env_dict["EVOLUTION_API_URL"] = evo_env["EVOLUTION_API_URL"]
    if evo_env.get("EVOLUTION_API_KEY"):
        env_dict["EVOLUTION_API_KEY"] = evo_env["EVOLUTION_API_KEY"]

    payload: dict = {
        "image": IMAGE,
        "disk": 250,
        "onstart": onstart,
        "runtype": "ssh",
        "env": env_dict,
        "label": "cognitiva-comfyui-flux2",
    }
    if template_hash:
        payload["template_hash_id"] = template_hash

    print(f"\nCriando instância no offer {offer_id}...")
    result = vast_request("PUT", f"/asks/{offer_id}/", api_key, payload)
    print(f"  Resposta: {json.dumps(result, indent=2)}")

    if not result.get("success", False) and "new_contract" not in result:
        raise RuntimeError(f"Falha ao criar instância: {result}")

    instance_id = result.get("new_contract")
    if not instance_id:
        raise RuntimeError(f"instance_id não encontrado na resposta: {result}")

    update_env_var("VAST_AI_INSTANCE_ID", str(instance_id))
    print(f"\nInstância criada: {instance_id}")
    return int(instance_id)


def action_poll_status(api_key: str, instance_id: int, max_wait: int = 300) -> dict:
    """Poll até a instância ficar running ou max_wait segundos."""
    print(f"Aguardando instância {instance_id} ficar running (max {max_wait}s)...")
    start = time.monotonic()
    while True:
        data = vast_request("GET", "/instances/", api_key)
        instances = data.get("instances", [])
        inst = next((i for i in instances if i.get("id") == instance_id), None)
        if inst is None:
            raise RuntimeError(f"Instância {instance_id} não encontrada")

        status = inst.get("actual_status", "unknown")
        elapsed = time.monotonic() - start
        print(f"  [{elapsed:.0f}s] status={status} ssh={inst.get('ssh_host', '')}:{inst.get('ssh_port', '')}")

        if status == "running":
            print(f"\nInstância rodando em {elapsed:.0f}s")
            return inst

        if status in ("error", "failed", "cancelled"):
            raise RuntimeError(f"Instância entrou em estado {status}: {inst}")

        if elapsed > max_wait:
            raise TimeoutError(f"Timeout {max_wait}s — status ainda: {status}")

        time.sleep(15)


def action_destroy_instance(api_key: str, instance_id: int) -> None:
    """Destrói instância (DELETE)."""
    print(f"Destruindo instância {instance_id}...")
    result = vast_request("DELETE", f"/instances/{instance_id}/", api_key)
    print(f"  Resposta: {result}")
    # Limpar .env
    update_env_var("VAST_AI_INSTANCE_ID", "")
    print("  VAST_AI_INSTANCE_ID removido do .env")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Vast.ai provisioning para ComfyUI")
    parser.add_argument("--action", required=True,
                        choices=["create-template", "create-instance", "list-offers",
                                 "destroy-instance", "status"],
                        help="Ação a executar")
    parser.add_argument("--offer-id", type=int, help="ID de oferta específica (opcional)")
    parser.add_argument("--instance-id", type=int, help="ID de instância (para destroy/status)")
    args = parser.parse_args()

    env = load_env()
    api_key = env.get("VAST_AI_API_KEY", "")
    if not api_key:
        print("ERRO: VAST_AI_API_KEY não encontrado em .env", file=sys.stderr)
        sys.exit(1)

    template_hash = env.get("VAST_AI_TEMPLATE_ID", "")

    if args.action == "list-offers":
        offers = action_list_offers(api_key)
        print(f"\n{'ID':>10}  {'GPU':30}  {'$/hr':>6}  {'VRAM':>8}  {'CUDA':>6}")
        print("-" * 70)
        for o in offers[:20]:
            print(
                f"{o['id']:>10}  {o.get('gpu_name','?'):30}  "
                f"${o.get('dph_total',0):.3f}  "
                f"{o.get('gpu_ram',0)/1024:.0f}GB  "
                f"{o.get('cuda_max_good','?')}"
            )

    elif args.action == "create-template":
        action_create_template(api_key)

    elif args.action == "create-instance":
        instance_id = action_create_instance(api_key, args.offer_id, template_hash or None)
        # Poll até ficar running
        inst = action_poll_status(api_key, instance_id)
        print(f"\nInstância pronta:")
        print(f"  SSH: ssh root@{inst.get('ssh_host')} -p {inst.get('ssh_port')}")
        print(f"  GPU: {inst.get('gpu_name')}")
        print(f"  $/hr: {inst.get('dph_total', 0):.3f}")

    elif args.action == "destroy-instance":
        instance_id = args.instance_id or int(env.get("VAST_AI_INSTANCE_ID", "0") or "0")
        if not instance_id:
            print("ERRO: --instance-id obrigatório (ou VAST_AI_INSTANCE_ID no .env)", file=sys.stderr)
            sys.exit(1)
        action_destroy_instance(api_key, instance_id)

    elif args.action == "status":
        instance_id = args.instance_id or int(env.get("VAST_AI_INSTANCE_ID", "0") or "0")
        if not instance_id:
            print("ERRO: --instance-id obrigatório (ou VAST_AI_INSTANCE_ID no .env)", file=sys.stderr)
            sys.exit(1)
        data = vast_request("GET", "/instances/", api_key)
        instances = data.get("instances", [])
        inst = next((i for i in instances if i.get("id") == instance_id), None)
        if inst is None:
            print(f"Instância {instance_id} não encontrada")
        else:
            print(json.dumps({
                "id": inst.get("id"),
                "status": inst.get("actual_status"),
                "gpu": inst.get("gpu_name"),
                "ssh_host": inst.get("ssh_host"),
                "ssh_port": inst.get("ssh_port"),
                "dph_total": inst.get("dph_total"),
                "start_date": inst.get("start_date"),
            }, indent=2))


if __name__ == "__main__":
    main()
