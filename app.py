"""
app.py — Serveur web Flask pour le dashboard NKD TECH.
Sert le dashboard HTML et expose /api/analytics pour les données live.
"""

import json
import os
from datetime import datetime
from flask import Flask, jsonify, send_from_directory, render_template_string

app = Flask(__name__, static_folder="static")

def load_analytics():
    """Charge les données analytics depuis le fichier JSON."""
    try:
        with open("data/analytics.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "Données non disponibles — pipeline non encore exécuté"}
    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def index():
    """Sert le dashboard principal."""
    return send_from_directory("templates", "dashboard.html")


@app.route("/api/analytics")
def api_analytics():
    """Endpoint JSON pour les données analytics."""
    data = load_analytics()
    return jsonify(data)


@app.route("/api/health")
def health():
    """Health check pour Render."""
    data = load_analytics()
    last_updated = data.get("last_updated", "jamais")
    return jsonify({
        "status": "ok",
        "last_updated": last_updated,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route("/api/refresh", methods=["POST"])
def refresh():
    """Déclenche manuellement le pipeline (optionnel, pour usage interne)."""
    import subprocess, sys
    result = subprocess.Popen(
        [sys.executable, "scripts/run_pipeline.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    return jsonify({"status": "pipeline_started", "pid": result.pid})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
