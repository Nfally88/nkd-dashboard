"""
compute_tva.py — Calcule la TVA sur marge (occasion) et la TVA normale 20 % (neuf,
accessoires, réparations, montant libre) à partir des ventes Square et des coûts
d'achat extraits des factures Drive.
Produit data/analytics.json utilisé par le dashboard.
"""

import json
import re
from datetime import datetime, timezone
from collections import defaultdict

TVA_CLASSES = ("occasion", "neuf", "accessoire_reparation", "libre")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize(text):
    """Normalise un texte pour le matching."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text


def find_purchase_cost(item_name, purchase_items):
    """
    Cherche le coût d'achat d'un article par correspondance de nom.
    Retourne le prix moyen d'achat ou None si non trouvé.
    """
    name_norm = normalize(item_name)

    # Correspondance exacte d'abord
    for item in purchase_items:
        if item["description_normalized"] == name_norm:
            return item["avg_purchase_price_ht"]

    # Correspondance partielle (mots-clés)
    name_words = set(name_norm.split())
    best_score = 0
    best_price = None

    for item in purchase_items:
        item_words = set(item["description_normalized"].split())
        # Score = nombre de mots communs / max longueur
        common = name_words & item_words
        if len(common) >= 2:
            score = len(common) / max(len(name_words), len(item_words))
            if score > best_score and score > 0.4:
                best_score = score
                best_price = item["avg_purchase_price_ht"]

    return best_price


def payment_split(p):
    """
    Part du paiement dans chaque classe TVA (calculée par fetch_square.py au prorata
    des articles). Sans détail d'articles, la vente est un « montant libre ».
    """
    return p.get("split_eur") or {"libre": p["amount_eur"]}


def compute_analytics(transactions, purchase_costs):
    """Calcule tous les indicateurs pour le dashboard."""

    purchase_items = purchase_costs.get("items", [])
    payments = [p for p in transactions.get("payments", []) if p.get("status") == "COMPLETED"]

    # ---- TVA par vente ----
    # Chaque paiement est découpé une seule fois entre les classes : aucune vente comptée deux fois.
    # - occasion (téléphones, tablettes, ordinateurs sans « neuf ») → TVA sur marge
    # - neuf, accessoires/réparations, montant libre → TVA normale 20 % = TTC / 1,2 × 0,2
    tva_data = []
    tva_matched = 0

    for p in payments:
        split = payment_split(p)
        ca_occ = split.get("occasion", 0)
        ca_normale = sum(split.get(c, 0) for c in TVA_CLASSES if c != "occasion")

        # TVA sur marge : prix d'achat réel si connu pour tous les articles d'occasion
        occ_items = [i for i in p.get("items", []) if i.get("tva_class") == "occasion"]
        cost_occ = 0.0
        has_cost = bool(occ_items)
        for item in occ_items:
            cost = find_purchase_cost(item["name"], purchase_items)
            if cost is None and item.get("unit_cost_cents"):
                cost = item["unit_cost_cents"] / 100
            if cost is None:
                has_cost = False
                break
            cost_occ += cost * item.get("qty", 1)

        if ca_occ <= 0:
            tva_marge, is_estimated = 0.0, False
        elif has_cost:
            # PA TTC ≈ PA HT pour les achats à des particuliers / régime de la marge
            tva_marge, is_estimated = max(ca_occ - cost_occ, 0) * 20 / 120, False
            tva_matched += 1
        else:
            # Estimation : marge supposée de 35 % du prix de vente
            tva_marge, is_estimated = ca_occ * 0.35 * 20 / 120, True

        tva_data.append({
            "date": p["date"],
            "payment_id": p["id"],
            "amount_eur": p["amount_eur"],
            "ca_occasion_eur": round(ca_occ, 2),
            "ca_tva_normale_eur": round(ca_normale, 2),
            "tva_marge_eur": round(tva_marge, 4),
            "tva_normale_eur": round(ca_normale / 1.2 * 0.2, 4),
            "is_estimated": is_estimated
        })

    occasion_sales = sum(1 for t in tva_data if t["ca_occasion_eur"] > 0)

    # ---- CA mensuel ----
    monthly = defaultdict(lambda: defaultdict(float))
    monthly_transactions = defaultdict(int)

    for p, t in zip(payments, tva_data):
        month = p["date"][:7]  # YYYY-MM
        m = monthly[month]
        m["ca"] += p["amount_eur"]
        for cls, v in payment_split(p).items():
            m[cls] += v
        m["tva_est" if t["is_estimated"] else "tva_real"] += t["tva_marge_eur"]
        monthly_transactions[month] += 1

    months_sorted = sorted(monthly.keys())

    monthly_summary = []
    for mo in months_sorted:
        m = monthly[mo]
        ca = round(m["ca"], 2)
        ca_normale = m["neuf"] + m["accessoire_reparation"] + m["libre"]
        tva_real = round(m["tva_real"], 2)
        tva_est = round(m["tva_est"], 2)
        tva_normale = round(ca_normale / 1.2 * 0.2, 2)
        monthly_summary.append({
            "month": mo,
            "ca_eur": ca,
            "transactions": monthly_transactions[mo],
            "avg_ticket": round(ca / monthly_transactions[mo], 2) if monthly_transactions[mo] else 0,
            "ca_occasion_eur": round(m["occasion"], 2),
            "ca_neuf_eur": round(m["neuf"], 2),
            "ca_accessoire_reparation_eur": round(m["accessoire_reparation"], 2),
            "ca_libre_eur": round(m["libre"], 2),
            "ca_tva_normale_eur": round(ca_normale, 2),
            "tva_marge_real_eur": tva_real,
            "tva_marge_estimated_eur": tva_est,
            "tva_marge_total_eur": round(tva_real + tva_est, 2),
            "tva_normale_estimated_eur": tva_normale,
            "tva_collectee_total_eur": round(tva_real + tva_est + tva_normale, 2)
        })

    # ---- KPIs globaux ----
    total_ca = sum(p["amount_eur"] for p in payments)
    total_tva = sum(t["tva_marge_eur"] for t in tva_data)
    total_by_class = {
        cls: round(sum(payment_split(p).get(cls, 0) for p in payments), 2)
        for cls in TVA_CLASSES
    }
    total_ca_normale = total_by_class["neuf"] + total_by_class["accessoire_reparation"] + total_by_class["libre"]
    total_tva_normale = total_ca_normale / 1.2 * 0.2
    total_transactions = len(payments)
    avg_ticket = round(total_ca / total_transactions, 2) if total_transactions else 0

    # ---- Catalogue : CA par catégorie Square et meilleurs articles ----
    # Montant de chaque article tel qu'enregistré dans la commande (après remises).
    cat_totals = defaultdict(float)
    item_totals = defaultdict(lambda: {"ca_eur": 0.0, "qty": 0.0, "tva_class": ""})
    for p in payments:
        for item in p.get("items", []):
            ca_item = max(item.get("total_cents", 0), 0) / 100
            cat_totals[item.get("category") or "Sans catégorie"] += ca_item
            it = item_totals[item.get("name") or "Montant libre"]
            it["ca_eur"] += ca_item
            it["qty"] += item.get("qty", 1)
            it["tva_class"] = item.get("tva_class", "")
    by_category = [
        {"category": k, "ca_eur": round(v, 2)}
        for k, v in sorted(cat_totals.items(), key=lambda x: -x[1])
    ]
    top_items = [
        {"name": k, "ca_eur": round(v["ca_eur"], 2), "qty": round(v["qty"], 2), "tva_class": v["tva_class"]}
        for k, v in sorted(item_totals.items(), key=lambda x: -x[1]["ca_eur"])[:20]
    ]
    payments_with_items = sum(1 for p in payments if p.get("items"))
    payments_with_category = sum(1 for p in payments if any(i.get("category") for i in p.get("items", [])))

    # Jour de la semaine
    day_totals = defaultdict(float)
    day_counts = defaultdict(int)
    day_names = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    for p in payments:
        try:
            dt = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
            day_idx = dt.weekday()  # 0=Monday
            day_totals[day_idx] += p["amount_eur"]
            day_counts[day_idx] += 1
        except Exception:
            pass

    by_day = [
        {
            "day": day_names[i],
            "ca_eur": round(day_totals[i], 2),
            "transactions": day_counts[i],
            "avg_ticket": round(day_totals[i] / day_counts[i], 2) if day_counts[i] else 0
        }
        for i in range(7)
    ]

    # Moyens de paiement
    method_totals = defaultdict(float)
    for p in payments:
        method_totals[p["source_type"]] += p["amount_eur"]
    by_method = [
        {"method": k, "ca_eur": round(v, 2), "pct": round(v / total_ca * 100, 1)}
        for k, v in sorted(method_totals.items(), key=lambda x: -x[1])
    ]

    # Distribution tickets
    ranges = [
        ("< 10€", 0, 10),
        ("10-25€", 10, 25),
        ("25-50€", 25, 50),
        ("50-100€", 50, 100),
        ("100-200€", 100, 200),
        ("200€+", 200, float("inf"))
    ]
    ticket_dist = []
    for label, low, high in ranges:
        count = sum(1 for p in payments if low <= p["amount_eur"] < high)
        ca = sum(p["amount_eur"] for p in payments if low <= p["amount_eur"] < high)
        ticket_dist.append({"range": label, "count": count, "ca_eur": round(ca, 2)})

    # Projection
    if months_sorted:
        last_month = months_sorted[-1]
        last_ca = monthly[last_month]["ca"]
        today = datetime.now()
        days_elapsed = today.day
        days_in_month = 31 if today.month in [1, 3, 5, 7, 8, 10, 12] else 30
        projection = round(last_ca / days_elapsed * days_in_month, 2) if days_elapsed > 0 else 0
    else:
        projection = 0

    target_annual = 250000
    months_remaining = 12 - len(months_sorted)
    needed_monthly = round((target_annual - total_ca) / max(months_remaining, 1), 2)

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "kpis": {
            "total_ca_eur": round(total_ca, 2),
            "total_transactions": total_transactions,
            "avg_ticket_eur": avg_ticket,
            "total_tva_marge_eur": round(total_tva, 2),
            "total_tva_normale_estimated_eur": round(total_tva_normale, 2),
            "total_tva_collectee_eur": round(total_tva + total_tva_normale, 2),
            "total_ca_occasion_eur": total_by_class["occasion"],
            "total_ca_neuf_eur": total_by_class["neuf"],
            "total_ca_accessoire_reparation_eur": total_by_class["accessoire_reparation"],
            "total_ca_libre_eur": total_by_class["libre"],
            "total_ca_tva_normale_eur": round(total_ca_normale, 2),
            "tva_match_rate_pct": round(tva_matched / occasion_sales * 100, 1) if occasion_sales else 0,
            "payments_with_items": payments_with_items,
            "payments_with_category": payments_with_category,
            "target_annual_eur": target_annual,
            "gap_eur": round(target_annual - total_ca, 2),
            "progress_pct": round(total_ca / target_annual * 100, 1),
            "current_month_projection_eur": projection,
            "needed_monthly_eur": needed_monthly,
            "months_analyzed": len(months_sorted)
        },
        "monthly": monthly_summary,
        "by_day": by_day,
        "by_method": by_method,
        "ticket_distribution": ticket_dist,
        "by_category": by_category,
        "top_items": top_items,
        "tva_detail": tva_data[-100:],  # 100 dernières transactions pour le détail
        "purchase_costs_loaded": len(purchase_items)
    }


def main():
    print("📊 Calcul des indicateurs TVA et analytiques...")

    transactions = load_json("data/transactions.json")
    purchase_costs = load_json("data/purchase_costs.json")

    analytics = compute_analytics(transactions, purchase_costs)

    with open("data/analytics.json", "w", encoding="utf-8") as f:
        json.dump(analytics, f, ensure_ascii=False, indent=2)

    kpis = analytics["kpis"]
    print(f"✅ CA Total : {kpis['total_ca_eur']:,.2f} €")
    print(f"✅ CA occasion : {kpis['total_ca_occasion_eur']:,.2f} € | CA TVA normale : {kpis['total_ca_tva_normale_eur']:,.2f} €")
    print(f"✅ TVA sur marge : {kpis['total_tva_marge_eur']:,.2f} € | TVA normale : {kpis['total_tva_normale_estimated_eur']:,.2f} €")
    print(f"✅ Taux matching : {kpis['tva_match_rate_pct']}%")
    print(f"💾 Sauvegardé dans data/analytics.json")


if __name__ == "__main__":
    main()
