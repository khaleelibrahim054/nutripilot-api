#!/usr/bin/env bash
# Provision an Ubuntu EC2 instance and deploy the NutriPilot (Careem) FastAPI app.
#
# Installs: build tools, nginx, uv (+ Python 3.11), certbot (optional), PostgreSQL client.
# Database: external (AWS RDS PostgreSQL); no database server is installed locally.
# Sets up:  app user, code checkout, .env, migrations, systemd service,
#           nginx reverse proxy, UFW firewall, optional HTTPS.
#
# Usage (as a sudo-capable user, e.g. ubuntu):
#   DATABASE_URL='postgresql+asyncpg://user:pass@mydb.xxxx.rds.amazonaws.com:5432/careem?ssl=require' \
#   GITHUB_TOKEN=ghp_xxx \
#   DOMAIN=api.example.com  EMAIL=you@example.com \
#   bash setup-ec2.sh
#
# Env vars:
#   DATABASE_URL  RDS connection string. Required on the first run (it is written to .env);
#                 ignored afterwards, edit .env instead. Must use the postgresql+asyncpg:// scheme.
#   REPO_URL      git URL to clone (default: https://github.com/khaleelibrahim054/nutripilot-api.git)
#   GIT_USER      GitHub username used for cloning (default: khaleelibrahim054)
#   GITHUB_TOKEN  GitHub personal access token; required if the repo is private.
#                 Used only for the clone/fetch commands and never written to disk.
#   BRANCH        branch to deploy (default: main)
#   DOMAIN        server_name for nginx; enables HTTPS if EMAIL is also set (default: _ = any host)
#   EMAIL         Let's Encrypt registration email
#   APP_USER      system user running the app (default: nutripilot)
#   APP_DIR       install directory (default: /opt/nutripilot)
#   APP_PORT      local uvicorn port (default: 8000)
#   WORKERS       uvicorn workers (default: number of CPUs, max 4)
#
# Re-running is safe: it updates the code, dependencies, migrations and config,
# and never overwrites an existing .env.

set -Eeuo pipefail
trap 'echo "ERROR: failed at line $LINENO" >&2' ERR

DATABASE_URL="${DATABASE_URL:-}"
REPO_URL="${REPO_URL:-https://github.com/khaleelibrahim054/nutripilot-api.git}"
GIT_USER="${GIT_USER:-khaleelibrahim054}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"
BRANCH="${BRANCH:-main}"
DOMAIN="${DOMAIN:-_}"
EMAIL="${EMAIL:-}"
APP_USER="${APP_USER:-nutripilot}"
APP_DIR="${APP_DIR:-/opt/nutripilot}"
APP_PORT="${APP_PORT:-8000}"
SERVICE="nutripilot"
CPUS="$(nproc)"
WORKERS="${WORKERS:-$(( CPUS > 4 ? 4 : CPUS ))}"
ENV_FILE="$APP_DIR/.env"

log() { printf '\n==> %s\n' "$*"; }

if [[ $EUID -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi
if ! command -v apt-get >/dev/null; then
  echo "This script supports Debian/Ubuntu only." >&2; exit 1
fi

# Fail fast, before installing anything, if the first run has no database URL.
if ! $SUDO test -f "$ENV_FILE"; then
  if [[ -z "$DATABASE_URL" ]]; then
    echo "DATABASE_URL is required on the first run, e.g.:" >&2
    echo "  DATABASE_URL='postgresql+asyncpg://user:pass@host.rds.amazonaws.com:5432/dbname?ssl=require' bash $0" >&2
    exit 1
  fi
  if [[ "$DATABASE_URL" != postgresql+asyncpg://* ]]; then
    echo "DATABASE_URL must start with postgresql+asyncpg:// (the app uses the asyncpg driver)." >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------- packages
log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -y
$SUDO apt-get install -y --no-install-recommends \
  ca-certificates curl git build-essential openssl ufw nginx postgresql-client

# ---------------------------------------------------------------- app user
log "Creating app user and directories"
if ! id "$APP_USER" >/dev/null 2>&1; then
  $SUDO useradd --system --create-home --home-dir "/home/$APP_USER" --shell /bin/bash "$APP_USER"
fi
$SUDO mkdir -p "$APP_DIR"
$SUDO chown "$APP_USER:$APP_USER" "$APP_DIR"

run_as_app() { $SUDO -u "$APP_USER" -H bash -lc "$*"; }

# Run git as the app user. With a token, authenticate through a one-off HTTP header passed in
# the environment (GIT_CONFIG_*), so the token never lands in .git/config, the URL or argv.
git_as_app() {
  if [[ -n "$GITHUB_TOKEN" ]]; then
    local auth
    auth="$(printf '%s:%s' "$GIT_USER" "$GITHUB_TOKEN" | base64 -w0)"
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="http.https://github.com/.extraheader" \
    GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${auth}" \
    GIT_TERMINAL_PROMPT=0 \
      $SUDO --preserve-env=GIT_CONFIG_COUNT,GIT_CONFIG_KEY_0,GIT_CONFIG_VALUE_0,GIT_TERMINAL_PROMPT \
      -u "$APP_USER" -H bash -c "$*"
  else
    GIT_TERMINAL_PROMPT=0 $SUDO --preserve-env=GIT_TERMINAL_PROMPT -u "$APP_USER" -H bash -c "$*"
  fi
}

# ---------------------------------------------------------------- uv + python
log "Installing uv"
if ! run_as_app 'test -x "$HOME/.local/bin/uv"'; then
  run_as_app 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
UV="/home/$APP_USER/.local/bin/uv"

# ---------------------------------------------------------------- code
log "Fetching code ($BRANCH)"
if [[ -d "$APP_DIR/.git" ]]; then
  run_as_app "cd '$APP_DIR' && git remote set-url origin '$REPO_URL'"
  git_as_app "cd '$APP_DIR' && git fetch --prune origin"
  run_as_app "cd '$APP_DIR' && git checkout '$BRANCH' && git reset --hard 'origin/$BRANCH'"
else
  # Clone into a temp dir, then move in, so a pre-created .env in APP_DIR does not block the clone.
  TMP_CLONE="$(mktemp -d)"
  $SUDO chown "$APP_USER:$APP_USER" "$TMP_CLONE"
  git_as_app "git clone --branch '$BRANCH' '$REPO_URL' '$TMP_CLONE/src'" || {
    echo "Clone failed. If the repo is private, pass GITHUB_TOKEN (a PAT with read access)." >&2
    exit 1
  }
  $SUDO cp -a "$TMP_CLONE/src/." "$APP_DIR/"
  $SUDO rm -rf "$TMP_CLONE"
  $SUDO chown -R "$APP_USER:$APP_USER" "$APP_DIR"
fi

# ---------------------------------------------------------------- .env
log "Writing .env"
if ! $SUDO test -f "$ENV_FILE"; then
  ENC_KEY="$(openssl rand -base64 32 | tr '+/' '-_')"   # valid Fernet key (32 url-safe base64 bytes)
  $SUDO tee "$ENV_FILE" >/dev/null <<EOF
APP_NAME="NutriPilot API"
ENVIRONMENT=production
LOG_LEVEL=INFO
DATABASE_URL=${DATABASE_URL}
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
CORS_ORIGINS=[]
AI_SERVICE_URL=http://localhost:8100
AI_SERVICE_TIMEOUT_SECONDS=10
AI_SERVICE_STREAM_IDLE_TIMEOUT_SECONDS=60
# Fill these in, then: sudo systemctl restart ${SERVICE}
CAREEM_CLIENT_ID=
CAREEM_CLIENT_SECRET=
CAREEM_REDIRECT_URI=nutripilot://auth/careem/callback
CAREEM_SCOPES=["openid"]
AUTH_ENCRYPTION_KEY=${ENC_KEY}
EOF
  NEW_ENV=1
else
  echo ".env already exists; leaving it unchanged."
  NEW_ENV=0
fi
$SUDO chown "$APP_USER:$APP_USER" "$ENV_FILE"
$SUDO chmod 600 "$ENV_FILE"

# ---------------------------------------------------------------- database connectivity
log "Checking connection to the database"
DB_LINE="$($SUDO grep -E '^DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')"
DB_HOSTPORT="$(sed -E 's#^[^:]+://[^@]*@([^/?]+).*#\1#' <<<"$DB_LINE")"
DB_HOST="${DB_HOSTPORT%%:*}"
DB_PORT="5432"; [[ "$DB_HOSTPORT" == *:* ]] && DB_PORT="${DB_HOSTPORT##*:}"
if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" -t 10 >/dev/null; then
  echo "Cannot reach $DB_HOST:$DB_PORT." >&2
  echo "Check the RDS security group allows inbound $DB_PORT from this instance's security group," >&2
  echo "and that the instance and RDS share a VPC (or RDS is publicly accessible)." >&2
  exit 1
fi
echo "Reached $DB_HOST:$DB_PORT"

# ---------------------------------------------------------------- deps + migrations
log "Installing Python dependencies"
run_as_app "cd '$APP_DIR' && '$UV' sync --frozen --no-dev"

log "Running database migrations"
run_as_app "cd '$APP_DIR' && '$UV' run --frozen --no-dev alembic upgrade head"

# ---------------------------------------------------------------- systemd
log "Installing systemd service"
$SUDO tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<EOF
[Unit]
Description=NutriPilot API (FastAPI / uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${APP_PORT} --workers ${WORKERS} --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE"
$SUDO systemctl restart "$SERVICE"

# ---------------------------------------------------------------- nginx
log "Configuring nginx"
NGINX_CONF="/etc/nginx/sites-available/${SERVICE}"
# Skip rewriting when certbot has already added TLS directives to this file.
if ! $SUDO grep -q "managed by Certbot" "$NGINX_CONF" 2>/dev/null; then
  $SUDO tee "$NGINX_CONF" >/dev/null <<EOF
upstream ${SERVICE}_app {
    server 127.0.0.1:${APP_PORT};
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 10m;
    server_tokens off;

    location / {
        proxy_pass http://${SERVICE}_app;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Needed for SSE streaming responses.
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF
fi
$SUDO ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/${SERVICE}"
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO nginx -t
$SUDO systemctl enable nginx
$SUDO systemctl reload nginx || $SUDO systemctl restart nginx

# ---------------------------------------------------------------- HTTPS
if [[ "$DOMAIN" != "_" && -n "$EMAIL" ]]; then
  log "Enabling HTTPS with Let's Encrypt"
  $SUDO apt-get install -y --no-install-recommends certbot python3-certbot-nginx
  $SUDO certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email --redirect --non-interactive \
    || echo "WARNING: certbot failed. Check that DNS for $DOMAIN points at this server and port 80 is open."
fi

# ---------------------------------------------------------------- firewall
log "Configuring firewall (UFW)"
$SUDO ufw allow OpenSSH
$SUDO ufw allow 'Nginx Full'
$SUDO ufw --force enable

# ---------------------------------------------------------------- verify
log "Verifying"
sleep 3
$SUDO systemctl --no-pager --lines=0 status "$SERVICE" || true
for path in /api/v1/health /health; do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1${path}" -H "Host: ${DOMAIN}" || true)"
  echo "GET ${path} -> ${code}"
done

cat <<EOF

Done.
  Service logs : sudo journalctl -u ${SERVICE} -f
  Restart      : sudo systemctl restart ${SERVICE}
  Config       : ${ENV_FILE}
  nginx site   : ${NGINX_CONF}
EOF
if [[ "$NEW_ENV" == "1" ]]; then
  echo "
NEXT STEP: set CAREEM_CLIENT_ID and CAREEM_CLIENT_SECRET in ${ENV_FILE}, then restart the service.
Also open ports 22, 80 and 443 in the EC2 security group."
fi
