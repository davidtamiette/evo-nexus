#!/usr/bin/env bash
# onstart.sh — ComfyUI + Cloudflare Tunnel + Watchdog
# Executado automaticamente pelo vast.ai ao iniciar a instância.
# Requer no ambiente: CLOUDFLARE_TUNNEL_TOKEN, COMFY_WORKSPACE
# Variáveis com default seguro:
set -euo pipefail

LOG="/workspace/logs/onstart.log"
mkdir -p /workspace/logs

exec > >(tee -a "$LOG") 2>&1

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === onstart.sh iniciando ==="

# ── 1. Garantir workspace montado ────────────────────────────────────────────
WORKSPACE="${COMFY_WORKSPACE:-/workspace}"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Workspace: $WORKSPACE"
df -h "$WORKSPACE" || { echo "ERRO: $WORKSPACE não montado"; exit 1; }

# ── 2. Instalar dependências de sistema ───────────────────────────────────────
apt-get update -qq
apt-get install -y -qq wget curl git libglib2.0-0 libsm6 libxrender1 libxext6 ffmpeg

# ── 3. Instalar/atualizar ComfyUI ─────────────────────────────────────────────
COMFY_DIR="$WORKSPACE/ComfyUI"
if [ ! -d "$COMFY_DIR/.git" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Clonando ComfyUI..."
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR"
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ComfyUI já existe — pulando clone"
fi

# ── 4. Instalar dependências Python do ComfyUI ────────────────────────────────
cd "$COMFY_DIR"
if ! python3 -c "import torch" 2>/dev/null; then
    pip install --quiet torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
fi
pip install --quiet -r requirements.txt

# ── 5. Criar diretórios de modelos ────────────────────────────────────────────
mkdir -p \
    "$WORKSPACE/models/diffusion_models" \
    "$WORKSPACE/models/text_encoders" \
    "$WORKSPACE/models/vae" \
    "$WORKSPACE/models/upscale_models" \
    "$WORKSPACE/models/controlnet" \
    "$WORKSPACE/models/loras" \
    "$WORKSPACE/outputs" \
    "$WORKSPACE/workflows"

# Configurar ComfyUI pra usar o workspace como base de modelos
cat > "$COMFY_DIR/extra_model_paths.yaml" << YAML
comfyui:
    base_path: $WORKSPACE/
    checkpoints: models/diffusion_models/
    text_encoders: models/text_encoders/
    vae: models/vae/
    upscale_models: models/upscale_models/
    controlnet: models/controlnet/
    loras: models/loras/
YAML

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Diretórios de modelos criados"

# ── 6. Instalar custom nodes (só se não existirem) ───────────────────────────
CUSTOM_NODES_DIR="$COMFY_DIR/custom_nodes"
mkdir -p "$CUSTOM_NODES_DIR"

install_node() {
    local repo="$1"
    local name=$(basename "$repo" .git)
    if [ ! -d "$CUSTOM_NODES_DIR/$name" ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Instalando custom node: $name"
        git clone --depth 1 "$repo" "$CUSTOM_NODES_DIR/$name"
        if [ -f "$CUSTOM_NODES_DIR/$name/requirements.txt" ]; then
            pip install --quiet -r "$CUSTOM_NODES_DIR/$name/requirements.txt" || true
        fi
    else
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Custom node já existe: $name"
    fi
}

install_node "https://github.com/ltdrdata/ComfyUI-Manager.git"
install_node "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"
install_node "https://github.com/crystian/ComfyUI-Crystools.git"

# ── 7. Iniciar ComfyUI em modo API ────────────────────────────────────────────
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_LOG="$WORKSPACE/logs/comfyui.log"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Iniciando ComfyUI na porta $COMFY_PORT..."
nohup python3 "$COMFY_DIR/main.py" \
    --listen 0.0.0.0 \
    --port "$COMFY_PORT" \
    --output-directory "$WORKSPACE/outputs" \
    --disable-auto-launch \
    --preview-method auto \
    > "$COMFY_LOG" 2>&1 &

COMFY_PID=$!
echo "$COMFY_PID" > /workspace/logs/comfyui.pid
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ComfyUI PID: $COMFY_PID"

# Aguardar ComfyUI responder (máx 120s)
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Aguardando ComfyUI ficar pronto..."
for i in $(seq 1 24); do
    if curl -sf "http://localhost:$COMFY_PORT/system_stats" > /dev/null 2>&1; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ComfyUI pronto após ${i}x5s"
        break
    fi
    sleep 5
    if [ $i -eq 24 ]; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERRO: ComfyUI não respondeu em 120s"
        tail -50 "$COMFY_LOG"
        exit 1
    fi
done

# ── 8. Instalar e iniciar Cloudflare Tunnel ───────────────────────────────────
if [ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] AVISO: CLOUDFLARE_TUNNEL_TOKEN não definido — tunnel não iniciado"
else
    # Instalar cloudflared se não existir
    if ! command -v cloudflared &>/dev/null; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Instalando cloudflared..."
        wget -qO /usr/local/bin/cloudflared \
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        chmod +x /usr/local/bin/cloudflared
    fi

    TUNNEL_LOG="$WORKSPACE/logs/cloudflared.log"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Iniciando Cloudflare Tunnel..."
    nohup cloudflared tunnel --no-autoupdate run \
        --token "$CLOUDFLARE_TUNNEL_TOKEN" \
        > "$TUNNEL_LOG" 2>&1 &

    TUNNEL_PID=$!
    echo "$TUNNEL_PID" > /workspace/logs/cloudflared.pid
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Cloudflare Tunnel PID: $TUNNEL_PID"

    # Aguardar tunnel conectar (máx 60s)
    for i in $(seq 1 12); do
        if grep -q "Registered tunnel connection" "$TUNNEL_LOG" 2>/dev/null; then
            echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Tunnel conectado após ${i}x5s"
            break
        fi
        sleep 5
        if [ $i -eq 12 ]; then
            echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] AVISO: Tunnel não confirmou conexão em 60s — verificar log"
            tail -20 "$TUNNEL_LOG"
        fi
    done
fi

# ── 9. Iniciar watchdog ───────────────────────────────────────────────────────
WATCHDOG_SCRIPT="$WORKSPACE/watchdog.py"
if [ -f "$WATCHDOG_SCRIPT" ]; then
    WATCHDOG_LOG="$WORKSPACE/logs/watchdog.log"
    IDLE_MINUTES="${IDLE_SHUTDOWN_MINUTES:-15}"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Iniciando watchdog (idle=${IDLE_MINUTES}min)..."
    nohup python3 "$WATCHDOG_SCRIPT" \
        --idle-minutes "$IDLE_MINUTES" \
        --comfy-port "$COMFY_PORT" \
        > "$WATCHDOG_LOG" 2>&1 &
    WATCHDOG_PID=$!
    echo "$WATCHDOG_PID" > /workspace/logs/watchdog.pid
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Watchdog PID: $WATCHDOG_PID"
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] AVISO: watchdog.py não encontrado em $WATCHDOG_SCRIPT"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Copie scripts/vastai/watchdog.py para o volume antes de usar"
fi

# ── 10. Gravar boot timestamp ─────────────────────────────────────────────────
BOOT_INFO="$WORKSPACE/logs/boot_info.json"
python3 -c "
import json, datetime, os, subprocess
info = {
    'boot_at': datetime.datetime.utcnow().isoformat() + 'Z',
    'hostname': os.uname().nodename,
    'comfy_pid': $COMFY_PID,
    'comfy_port': int(os.environ.get('COMFY_PORT', '8188')),
    'workspace': '$WORKSPACE',
}
try:
    r = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'], capture_output=True, text=True)
    info['gpu'] = r.stdout.strip()
except Exception:
    info['gpu'] = 'unknown'
with open('$BOOT_INFO', 'w') as f:
    json.dump(info, f, indent=2)
print('boot_info gravado:', json.dumps(info))
"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === onstart.sh concluído ==="
