#!/bin/bash
# EvoNexus — macOS startup script
# Inicia Dashboard (Flask + terminal-server) e Scheduler
# Chamado pelo LaunchAgent em ~/Library/LaunchAgents/com.cognitiva.evonexus.plist

SCRIPT_DIR="/Users/david/evonexus"
LOG_DIR="$SCRIPT_DIR/logs"

# PATH completo para macOS (Homebrew Apple Silicon + Intel + local)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"

mkdir -p "$LOG_DIR"
cd "$SCRIPT_DIR" || exit 1

# Carrega variáveis de ambiente — ignora linhas com aspas multi-linha que quebram o bash source
if [ -f .env ]; then
  set -a
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=[^"'"'"']*$' .env 2>/dev/null) 2>/dev/null || true
  set +a
fi

# Detecta Python: .venv > uv > python3
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
  PYTHON="$(command -v uv) run python"
else
  PYTHON="python3"
fi

# Aguarda o macOS terminar o login antes de subir os serviços
sleep 8

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando EvoNexus..."

# Para serviços existentes para evitar conflito de porta
pkill -f 'terminal-server/bin/server.js' 2>/dev/null || true
DASHBOARD_PORT="${EVONEXUS_PORT:-8080}"
pids=$(lsof -ti "tcp:$DASHBOARD_PORT" 2>/dev/null || true)
[ -n "$pids" ] && kill $pids 2>/dev/null || true
pkill -f "$SCRIPT_DIR/dashboard/backend/app.py" 2>/dev/null || true
pkill -f "$SCRIPT_DIR/scheduler.py" 2>/dev/null || true
sleep 1

# Inicia terminal-server
nohup node "$SCRIPT_DIR/dashboard/terminal-server/bin/server.js" \
  > "$LOG_DIR/terminal-server.log" 2>&1 &
echo $! > /tmp/evonexus-terminal-server.pid
echo "[$(date '+%Y-%m-%d %H:%M:%S')] terminal-server iniciado (PID $!)"

# Inicia scheduler
nohup $PYTHON "$SCRIPT_DIR/scheduler.py" \
  > "$LOG_DIR/scheduler.log" 2>&1 &
echo $! > /tmp/evonexus-scheduler.pid
echo "[$(date '+%Y-%m-%d %H:%M:%S')] scheduler iniciado (PID $!)"

# Inicia Flask dashboard
nohup $PYTHON "$SCRIPT_DIR/dashboard/backend/app.py" \
  > "$LOG_DIR/dashboard.log" 2>&1 &
echo $! > /tmp/evonexus-dashboard.pid
echo "[$(date '+%Y-%m-%d %H:%M:%S')] dashboard iniciado (PID $!) — http://localhost:$DASHBOARD_PORT"

# Aguarda o dashboard estar pronto e abre o browser automaticamente
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Aguardando dashboard ficar pronto..."
for i in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$DASHBOARD_PORT" 2>/dev/null | grep -q "200"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Dashboard pronto — abrindo browser"
    open "http://localhost:$DASHBOARD_PORT"
    break
  fi
  sleep 1
done
