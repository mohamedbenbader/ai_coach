#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate

echo "🌐 Démarrage du serveur web sur http://127.0.0.1:5001"
python3 web.py
