"""
fetch_drive_invoices.py — Extrait les prix d'achat depuis les PDFs de factures
fournisseurs stockés sur Google Drive et construit un catalogue de coûts.
"""

import os
import io
import json
import re
from datetime import datetime, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import pdfplumber

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google-credentials.json")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")  # ID du dossier factures


def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def list_pdf_files(service, folder_id):
    """Liste tous les PDFs dans le dossier Drive."""
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=200
    ).execute()
    return results.get("files", [])


def download_pdf(service, file_id):
    """Télécharge un PDF depuis Drive en mémoire."""
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer


def extract_invoice_data(pdf_buffer, filename):
    """
    Extrait les données de prix d'un PDF de facture fournisseur.
    Gère les formats les plus courants (Lamine Barbès, autres fournisseurs tech).
    """
    items = []

    try:
        with pdfplumber.open(pdf_buffer) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"

                # Essayer aussi d'extraire les tableaux
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if row and len(row) >= 3:
                            items.extend(parse_table_row(row, filename))

            # Si pas de tableau, parser le texte brut
            if not items:
                items = parse_text_invoice(full_text, filename)

    except Exception as e:
        print(f"  ⚠️ Erreur extraction {filename}: {e}")

    return items


def parse_table_row(row, filename):
    """Parse une ligne de tableau de facture."""
    items = []
    try:
        # Chercher une ligne avec description + quantité + prix
        row_clean = [str(c).strip() if c else "" for c in row]
        row_text = " | ".join(row_clean)

        # Pattern prix : nombre avec virgule ou point, optionnellement €
        price_pattern = r'(\d+[.,]\d{2})\s*€?'
        prices = re.findall(price_pattern, row_text)

        if prices and len(row_clean) >= 2:
            description = row_clean[0] if row_clean[0] else row_clean[1]
            if len(description) > 3 and not description.replace(" ", "").isdigit():
                # Prendre le premier prix comme prix unitaire
                unit_price = float(prices[0].replace(",", "."))
                qty = 1

                # Chercher une quantité
                qty_match = re.search(r'\b(\d+)\s*(?:pcs?|unités?|x)\b', row_text, re.IGNORECASE)
                if qty_match:
                    qty = int(qty_match.group(1))

                if unit_price > 0:
                    items.append({
                        "description": description[:100],
                        "qty": qty,
                        "unit_price_ht": unit_price,
                        "source_file": filename
                    })
    except Exception:
        pass
    return items


def parse_text_invoice(text, filename):
    """Parse un PDF texte libre (non tabulaire)."""
    items = []
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if len(line) < 5:
            continue

        # Chercher lignes avec : description + prix
        # Patterns courants : "Ecran iPhone 14 Pro - 45,00€" ou "2x Batterie Samsung S22 90.00"
        match = re.search(
            r'(\d*[xX×]?\s*)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9\s\-\/]{5,60}?)\s+(\d+[.,]\d{2})\s*€?',
            line
        )
        if match:
            qty_str = (match.group(1) or "1").strip().lower().replace("x", "").replace("×", "").strip()
            try:
                qty = int(qty_str) if qty_str else 1
            except ValueError:
                qty = 1

            description = match.group(2).strip()
            price = float(match.group(3).replace(",", "."))

            # Filtrer les faux positifs (totaux, TVA, etc.)
            skip_keywords = ["total", "tva", "sous-total", "remise", "livraison", "frais", "date", "facture", "n°"]
            if any(kw in description.lower() for kw in skip_keywords):
                continue

            if price > 0 and price < 5000:  # Prix raisonnable pour une pièce
                items.append({
                    "description": description[:100],
                    "qty": qty,
                    "unit_price_ht": price,
                    "source_file": filename
                })

    return items


def normalize_description(desc):
    """Normalise la description pour le matching avec Square."""
    desc = desc.lower().strip()
    # Supprimer caractères spéciaux
    desc = re.sub(r'[^\w\s]', ' ', desc)
    desc = re.sub(r'\s+', ' ', desc)
    return desc


def main():
    print("🔄 Connexion à Google Drive...")
    service = get_drive_service()

    folder_id = DRIVE_FOLDER_ID
    if not folder_id:
        print("⚠️ DRIVE_FOLDER_ID non configuré — skipping extraction Drive")
        # Créer un fichier vide pour ne pas bloquer le pipeline
        os.makedirs("data", exist_ok=True)
        with open("data/purchase_costs.json", "w") as f:
            json.dump({"last_updated": datetime.now(timezone.utc).isoformat(), "items": []}, f)
        return

    print(f"📁 Scan du dossier Drive {folder_id}...")
    pdf_files = list_pdf_files(service, folder_id)
    print(f"✅ {len(pdf_files)} PDFs trouvés")

    all_items = []
    for pdf_file in pdf_files:
        print(f"  📄 Traitement : {pdf_file['name']}")
        try:
            buffer = download_pdf(service, pdf_file["id"])
            items = extract_invoice_data(buffer, pdf_file["name"])
            all_items.extend(items)
            print(f"     → {len(items)} articles extraits")
        except Exception as e:
            print(f"     ⚠️ Erreur : {e}")

    print(f"\n✅ Total : {len(all_items)} lignes d'achat extraites")

    # Dédoublonner et consolider par description normalisée
    consolidated = {}
    for item in all_items:
        key = normalize_description(item["description"])
        if key not in consolidated:
            consolidated[key] = {
                "description_original": item["description"],
                "description_normalized": key,
                "prices": [],
                "source_files": []
            }
        consolidated[key]["prices"].append(item["unit_price_ht"])
        if item["source_file"] not in consolidated[key]["source_files"]:
            consolidated[key]["source_files"].append(item["source_file"])

    # Calculer le prix moyen d'achat par article
    final_items = []
    for key, data in consolidated.items():
        avg_price = sum(data["prices"]) / len(data["prices"])
        final_items.append({
            "description_normalized": key,
            "description": data["description_original"],
            "avg_purchase_price_ht": round(avg_price, 2),
            "price_count": len(data["prices"]),
            "sources": data["source_files"]
        })

    # Sauvegarder
    os.makedirs("data", exist_ok=True)
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_pdf_processed": len(pdf_files),
        "total_items": len(final_items),
        "items": final_items
    }

    with open("data/purchase_costs.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"💾 Sauvegardé dans data/purchase_costs.json")


if __name__ == "__main__":
    main()
