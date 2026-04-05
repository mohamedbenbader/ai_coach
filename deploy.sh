#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Mise à jour Coach Bot (à exécuter en root après un git push)
# ─────────────────────────────────────────────────────────────────
set -e

APP_DIR="/opt/coach-bot"
APP_USER="coachbot"

echo "🔄 Mise à jour Coach Bot..."

git -C "$APP_DIR" pull
chown -R $APP_USER:$APP_USER "$APP_DIR"

sudo -u $APP_USER "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

systemctl restart coach-bot

echo "✅ Mise à jour terminée !"
systemctl status coach-bot --no-pager -l
