"""
run_pipeline.py — Orchestrateur principal.
Lance dans l'ordre : Square → Drive → Calcul TVA → Dashboard
"""

import subprocess
import sys
import os
from datetime import datetime

def run(script, description):
    print(f"\n{'='*50}")
    print(f"▶ {description}")
    print(f"{'='*50}")
    result = subprocess.run(
        [sys.executable, f"scripts/{script}"],
        capture_output=False
    )
    if result.returncode != 0:
        print(f"❌ ERREUR dans {script} (code {result.returncode})")
        sys.exit(result.returncode)
    print(f"✅ {description} — OK")

def main():
    start = datetime.now()
    print(f"\n🚀 NKD TECH Dashboard Pipeline — {start.strftime('%Y-%m-%d %H:%M:%S')}")

    run("fetch_square.py", "1/3 — Récupération transactions Square")
    run("fetch_drive_invoices.py", "2/3 — Extraction factures Google Drive")
    run("compute_tva.py", "3/3 — Calcul TVA sur marge & analytics")

    elapsed = (datetime.now() - start).seconds
    print(f"\n🎉 Pipeline terminé en {elapsed}s — Dashboard prêt !")

if __name__ == "__main__":
    main()
