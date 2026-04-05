#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Setup initial Coach Bot sur Ubuntu 22.04 (Hetzner)
# À exécuter en root une seule fois : bash setup.sh
# ─────────────────────────────────────────────────────────────────
set -e

APP_DIR="/opt/coach-bot"
APP_USER="coachbot"
DOMAIN=""

echo "=============================="
echo "  Coach Bot — Setup Hetzner"
echo "=============================="

# ── 1. Domaine (optionnel) ─────────────────────────────────────
read -rp "Ton domaine (laisse vide pour utiliser l'IP) : " DOMAIN

# ── 2. Clé Anthropic ──────────────────────────────────────────
read -rp "ANTHROPIC_API_KEY : " ANTHROPIC_KEY

# ── 3. Mise à jour système ─────────────────────────────────────
echo "[1/8] Mise à jour du système..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 4. Dépendances ────────────────────────────────────────────
echo "[2/8] Installation des paquets..."
apt-get install -y -qq python3 python3-pip python3-venv \
    postgresql postgresql-contrib \
    nginx certbot python3-certbot-nginx \
    git curl ufw

# ── 5. PostgreSQL ─────────────────────────────────────────────
echo "[3/8] Configuration PostgreSQL..."
DB_PASS=$(openssl rand -base64 24)
sudo -u postgres psql -c "CREATE USER coachbot WITH PASSWORD '${DB_PASS}';" 2>/dev/null || true
sudo -u postgres psql -c "CREATE DATABASE coachbot OWNER coachbot;" 2>/dev/null || true
DATABASE_URL="postgresql://coachbot:${DB_PASS}@localhost/coachbot"

# ── 6. Utilisateur système + app ──────────────────────────────
echo "[4/8] Création de l'utilisateur système..."
id -u $APP_USER &>/dev/null || useradd -m -s /bin/bash $APP_USER

echo "[5/8] Déploiement de l'application..."
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull
else
    git clone https://github.com/mohamedbenbader/ai_coach.git "$APP_DIR"
fi
chown -R $APP_USER:$APP_USER "$APP_DIR"

# Venv + dépendances
sudo -u $APP_USER python3 -m venv "$APP_DIR/venv"
sudo -u $APP_USER "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u $APP_USER "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

# Fichier .env
SECRET_KEY=$(openssl rand -base64 32)
cat > "$APP_DIR/.env" <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}
DATABASE_URL=${DATABASE_URL}
SECRET_KEY=${SECRET_KEY}
WEB_PORT=8000
EOF
chown $APP_USER:$APP_USER "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

# ── 7. Systemd ────────────────────────────────────────────────
echo "[6/8] Configuration du service systemd..."
cat > /etc/systemd/system/coach-bot.service <<EOF
[Unit]
Description=Coach Bot
After=network.target postgresql.service

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/venv/bin/gunicorn web:app --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable coach-bot
systemctl start coach-bot

# ── 8. Nginx ──────────────────────────────────────────────────
echo "[7/8] Configuration Nginx..."
if [ -n "$DOMAIN" ]; then
    SERVER_NAME="$DOMAIN"
else
    SERVER_NAME="_"
fi

cat > /etc/nginx/sites-available/coach-bot <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME};

    client_max_body_size 5M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/coach-bot /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# ── 9. HTTPS (si domaine fourni) ──────────────────────────────
if [ -n "$DOMAIN" ]; then
    echo "[8/8] Certificat HTTPS Let's Encrypt..."
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}"
else
    echo "[8/8] Pas de domaine — HTTPS ignoré (accès via IP uniquement)"
fi

# ── Firewall ──────────────────────────────────────────────────
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo ""
echo "✅ Installation terminée !"
if [ -n "$DOMAIN" ]; then
    echo "   App accessible sur : https://${DOMAIN}"
else
    IP=$(curl -s ifconfig.me)
    echo "   App accessible sur : http://${IP}"
fi
