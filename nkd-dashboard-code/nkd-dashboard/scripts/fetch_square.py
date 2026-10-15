"""
fetch_square.py — Récupère toutes les transactions Square (NKD TECH MGP)
et les sauvegarde dans data/transactions.json
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta

SQUARE_TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]
BASE_URL = "https://connect.squareup.com/v2"
HEADERS = {
    "Authorization": f"Bearer {SQUARE_TOKEN}",
    "Content-Type": "application/json",
    "Square-Version": "2024-01-17"
}

TARGET_LOCATION = "LRJ3WYA1SQ9TS"  # NKD TECH Mon Grand Plaisir


def get_payments(days_back=None):
    """Récupère les paiements. Si days_back=None, récupère tout."""
    payments = []
    cursor = None

    params = {
        "location_id": TARGET_LOCATION,
        "limit": 100,
        "sort_order": "DESC"
    }

    if days_back:
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        params["begin_time"] = since.strftime("%Y-%m-%dT00:00:00Z")

    while True:
        if cursor:
            params["cursor"] = cursor

        r = requests.get(f"{BASE_URL}/payments", headers=HEADERS, params=params)
        r.raise_for_status()
        data = r.json()

        batch = data.get("payments", [])
        payments.extend(batch)
        print(f"  Récupéré {len(payments)} paiements...")

        cursor = data.get("cursor")
        if not cursor:
            break

    return payments


def get_catalog_items():
    """Récupère le catalogue Square avec les coûts d'achat si disponibles."""
    items = {}
    cursor = None

    while True:
        params = {"types": "ITEM_VARIATION", "limit": 100}
        if cursor:
            params["cursor"] = cursor

        r = requests.get(f"{BASE_URL}/catalog/list", headers=HEADERS, params=params)
        r.raise_for_status()
        data = r.json()

        for obj in data.get("objects", []):
            variation_data = obj.get("item_variation_data", {})
            item_id = obj.get("id", "")
            name = variation_data.get("name", "")
            # Coût d'achat si saisi dans Square (en centimes)
            cost = variation_data.get("item_variation_vendor_infos", [{}])
            cost_money = variation_data.get("cost_money", {})
            items[item_id] = {
                "name": name,
                "cost_cents": cost_money.get("amount", 0)
            }

        cursor = data.get("cursor")
        if not cursor:
            break

    return items


def normalize_payment(p, catalog_items):
    """Normalise un paiement en structure standard."""
    amount_cents = p.get("amount_money", {}).get("amount", 0)

    # Récupérer les articles vendus pour calculer le coût d'achat
    line_items = p.get("line_items", [])
    total_cost_cents = 0
    items_detail = []

    for item in line_items:
        variation_id = item.get("catalog_object_id", "")
        qty = float(item.get("quantity", 1))
        catalog = catalog_items.get(variation_id, {})
        item_cost = catalog.get("cost_cents", 0) * qty
        total_cost_cents += item_cost
        items_detail.append({
            "name": item.get("name", catalog.get("name", "Article")),
            "qty": qty,
            "unit_price_cents": item.get("base_price_money", {}).get("amount", 0),
            "unit_cost_cents": catalog.get("cost_cents", 0)
        })

    return {
        "id": p.get("id"),
        "date": p.get("created_at", "")[:10],
        "datetime": p.get("created_at", ""),
        "amount_cents": amount_cents,
        "amount_eur": round(amount_cents / 100, 2),
        "status": p.get("status"),
        "source_type": p.get("source_type"),
        "location_id": p.get("location_id"),
        "cost_cents": total_cost_cents,
        "cost_eur": round(total_cost_cents / 100, 2),
        "margin_cents": amount_cents - total_cost_cents,
        "margin_eur": round((amount_cents - total_cost_cents) / 100, 2),
        # TVA sur marge = (PV - PA) × 20/120
        "tva_marge_eur": round((amount_cents - total_cost_cents) / 100 * 20 / 120, 4),
        "items": items_detail
    }


def main():
    print("🔄 Récupération des paiements Square...")
    payments_raw = get_payments()
    print(f"✅ {len(payments_raw)} paiements récupérés")

    print("📦 Récupération du catalogue...")
    catalog = get_catalog_items()
    print(f"✅ {len(catalog)} articles catalogue")

    print("🔄 Normalisation des données...")
    completed = [
        normalize_payment(p, catalog)
        for p in payments_raw
        if p.get("status") == "COMPLETED"
    ]
    print(f"✅ {len(completed)} paiements COMPLETED")

    # Sauvegarde
    os.makedirs("data", exist_ok=True)
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_transactions": len(completed),
        "payments": completed
    }

    with open("data/transactions.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"💾 Sauvegardé dans data/transactions.json")


if __name__ == "__main__":
    main()
