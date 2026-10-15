"""
fetch_square.py — Récupère toutes les transactions Square (NKD TECH MGP)
avec le détail des articles (API Orders) et les sauvegarde dans data/transactions.json
"""

import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone, timedelta

from classify import classify_item


def load_dotenv(path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")):
    """Charge le fichier .env local à la racine du dépôt (jamais envoyé sur GitHub) s'il existe."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

SQUARE_TOKEN = os.environ["SQUARE_ACCESS_TOKEN"]
BASE_URL = "https://connect.squareup.com/v2"
HEADERS = {
    "Authorization": f"Bearer {SQUARE_TOKEN}",
    "Content-Type": "application/json",
    "Square-Version": "2024-01-17"
}

TARGET_LOCATION = "LRJ3WYA1SQ9TS"  # NKD TECH Mon Grand Plaisir

# Session avec nouvelles tentatives : Square coupe parfois la connexion sur les longues récupérations
http = requests.Session()
http.mount("https://", HTTPAdapter(max_retries=Retry(
    total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET", "POST")
)))


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

        r = http.get(f"{BASE_URL}/payments", headers=HEADERS, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        batch = data.get("payments", [])
        payments.extend(batch)
        print(f"  Récupéré {len(payments)} paiements...")

        cursor = data.get("cursor")
        if not cursor:
            break

    return payments


def get_orders(order_ids):
    """Récupère les commandes (avec leurs articles) par lots de 100."""
    orders = {}
    ids = list(dict.fromkeys(i for i in order_ids if i))

    for start in range(0, len(ids), 100):
        r = http.post(
            f"{BASE_URL}/orders/batch-retrieve",
            headers=HEADERS,
            json={"location_id": TARGET_LOCATION, "order_ids": ids[start:start + 100]},
            timeout=60
        )
        if r.status_code in (401, 403):
            raise SystemExit(
                "❌ Square refuse l'accès aux commandes : la clé SQUARE_ACCESS_TOKEN "
                "n'a pas le droit ORDERS_READ (voir developer.squareup.com → votre application → OAuth / Permissions)."
            )
        r.raise_for_status()
        for o in r.json().get("orders", []):
            orders[o["id"]] = o
        print(f"  Récupéré {len(orders)} commandes...")

    return orders


def get_catalog():
    """
    Récupère le catalogue Square : pour chaque déclinaison (variation),
    le coût d'achat éventuel et le nom de la catégorie de l'article.
    """
    variations, items, categories = {}, {}, {}
    cursor = None

    while True:
        params = {"types": "ITEM,ITEM_VARIATION,CATEGORY", "limit": 100}
        if cursor:
            params["cursor"] = cursor

        r = http.get(f"{BASE_URL}/catalog/list", headers=HEADERS, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        for obj in data.get("objects", []):
            t = obj.get("type")
            if t == "CATEGORY":
                categories[obj["id"]] = obj.get("category_data", {}).get("name", "")
            elif t == "ITEM":
                item_data = obj.get("item_data", {})
                cat_id = item_data.get("category_id")
                if not cat_id and item_data.get("categories"):
                    cat_id = item_data["categories"][0].get("id")
                items[obj["id"]] = {"name": item_data.get("name", ""), "category_id": cat_id}
                # Les déclinaisons sont souvent incluses dans l'article
                for v in item_data.get("variations", []):
                    vd = v.get("item_variation_data", {})
                    variations[v["id"]] = {
                        "item_id": obj["id"],
                        "cost_cents": vd.get("cost_money", {}).get("amount", 0)
                    }
            elif t == "ITEM_VARIATION":
                vd = obj.get("item_variation_data", {})
                variations.setdefault(obj["id"], {
                    "item_id": vd.get("item_id"),
                    "cost_cents": vd.get("cost_money", {}).get("amount", 0)
                })

        cursor = data.get("cursor")
        if not cursor:
            break

    catalog = {}
    for vid, v in variations.items():
        item = items.get(v["item_id"], {})
        catalog[vid] = {
            "cost_cents": v["cost_cents"],
            "category": categories.get(item.get("category_id"), "")
        }
    return catalog


def order_items(order, catalog):
    """Liste des articles d'une commande, avec leur classement TVA."""
    result = []
    for li in order.get("line_items", []):
        variation_id = li.get("catalog_object_id", "")
        cat = catalog.get(variation_id, {})
        name = li.get("name") or ""
        if li.get("variation_name") and li["variation_name"].strip().lower() not in ("regular", "normal", "standard", "article de base", "prix normal"):
            name = f"{name} {li['variation_name']}".strip()
        qty = float(li.get("quantity", 1) or 1)
        result.append({
            "name": name,
            "category": cat.get("category", ""),
            "qty": qty,
            "unit_price_cents": li.get("base_price_money", {}).get("amount", 0),
            "total_cents": li.get("total_money", {}).get("amount", 0),
            "unit_cost_cents": cat.get("cost_cents", 0),
            "tva_class": classify_item(name, cat.get("category", ""))
        })
    return result


def normalize_payment(p, orders, catalog):
    """
    Normalise un paiement. Le montant du paiement est réparti entre les classes TVA
    au prorata du montant de chaque article de la commande : une vente n'est donc
    jamais comptée deux fois, même si la commande est payée en plusieurs fois.
    """
    amount_cents = p.get("amount_money", {}).get("amount", 0)
    order = orders.get(p.get("order_id"), {})
    items = order_items(order, catalog)

    split = {"occasion": 0, "neuf": 0, "accessoire_reparation": 0, "libre": 0}
    order_total = sum(max(i["total_cents"], 0) for i in items)
    if order_total > 0:
        allocated = 0
        positive = [i for i in items if i["total_cents"] > 0]
        for idx, i in enumerate(positive):
            if idx == len(positive) - 1:
                share = amount_cents - allocated  # le reste, pour tomber juste au centime
            else:
                share = round(amount_cents * i["total_cents"] / order_total)
            split[i["tva_class"]] += share
            allocated += share
    else:
        split["libre"] = amount_cents

    total_cost_cents = sum(i["unit_cost_cents"] * i["qty"] for i in items)

    return {
        "id": p.get("id"),
        "order_id": p.get("order_id"),
        "date": p.get("created_at", "")[:10],
        "datetime": p.get("created_at", ""),
        "amount_cents": amount_cents,
        "amount_eur": round(amount_cents / 100, 2),
        "status": p.get("status"),
        "source_type": p.get("source_type"),
        "location_id": p.get("location_id"),
        "cost_cents": total_cost_cents,
        "cost_eur": round(total_cost_cents / 100, 2),
        "split_eur": {k: round(v / 100, 2) for k, v in split.items()},
        "items": items
    }


def main():
    print("🔄 Récupération des paiements Square...")
    payments_raw = get_payments()
    completed_raw = [p for p in payments_raw if p.get("status") == "COMPLETED"]
    print(f"✅ {len(payments_raw)} paiements récupérés, {len(completed_raw)} COMPLETED")

    print("🧾 Récupération des commandes (articles vendus)...")
    orders = get_orders(p.get("order_id") for p in completed_raw)
    print(f"✅ {len(orders)} commandes")

    print("📦 Récupération du catalogue...")
    catalog = get_catalog()
    print(f"✅ {len(catalog)} déclinaisons catalogue")

    print("🔄 Normalisation des données...")
    completed = [normalize_payment(p, orders, catalog) for p in completed_raw]
    with_items = sum(1 for p in completed if p["items"])
    print(f"✅ {len(completed)} paiements, dont {with_items} avec articles")

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
