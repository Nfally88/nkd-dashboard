"""
compute_tva.py — Calcule la TVA sur marge en croisant ventes Square
et coûts d'achat extraits des factures Drive.
Produit data/analytics.json utilisé par le dashboard.
"""

import json
import re
from datetime import datetime, timezone
from collections import defaultdict


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


def calculate_tva_marge(sale_price_ttc, purchase_price_ht):
    """
    Calcule la TVA sur marge.
    Formule : TVA = (PV TTC - PA TTC) × 20/120
    Note : on considère que le prix d'achat HT ≈ TTC pour articles d'occasion
    (fournisseurs souvent non assujettis ou marge régime)
    """
    if purchase_price_ht is None or purchase_price_ht <= 0:
        return None

    # Approximation : PA TTC ≈ PA HT pour achats à particuliers/petits fournisseurs
    marge_ttc = sale_price_ttc - purchase_price_ht
    if marge_ttc <= 0:
        return 0.0

    tva = marge_ttc * 20 / 120
    return round(tva, 4)


def compute_analytics(transactions, purchase_costs):
    """Calcule tous les indicateurs pour le dashboard."""

    purchase_items = purchase_costs.get("items", [])
    payments = [p for p in transactions.get("payments", []) if p.get("status") == "COMPLETED"]

    # ---- TVA sur marge ----
    tva_data = []
    tva_matched = 0
    tva_unmatched = 0

    for p in payments:
        payment_tva = 0
        has_cost = False

        for item in p.get("items", []):
            cost = find_purchase_cost(item["name"], purchase_items)
            if cost is not None:
                sale_price = item["unit_price_cents"] / 100
                tva = calculate_tva_marge(sale_price, cost)
                if tva is not None:
                    payment_tva += tva * item.get("qty", 1)
                    has_cost = True

        # Si pas d'articles détaillés mais coût dispo via Square catalogue
        if not p.get("items") and p.get("cost_eur", 0) > 0:
            tva_calc = calculate_tva_marge(p["amount_eur"], p["cost_eur"])
            if tva_calc:
                payment_tva = tva_calc
                has_cost = True

        # Fallback : estimation TVA sur marge (35% de marge hypothétique)
        if not has_cost:
            estimated_margin = p["amount_eur"] * 0.35
            payment_tva = estimated_margin * 20 / 120
            tva_unmatched += 1
        else:
            tva_matched += 1

        tva_data.append({
            "date": p["date"],
            "payment_id": p["id"],
            "amount_eur": p["amount_eur"],
            "tva_marge_eur": round(payment_tva, 4),
            "is_estimated": not has_cost
        })

    # ---- CA mensuel ----
    monthly_ca = defaultdict(float)
    monthly_tva = defaultdict(float)
    monthly_tva_estimated = defaultdict(float)
    monthly_transactions = defaultdict(int)

    for p, t in zip(payments, tva_data):
        month = p["date"][:7]  # YYYY-MM
        monthly_ca[month] += p["amount_eur"]
        monthly_transactions[month] += 1
        if t["is_estimated"]:
            monthly_tva_estimated[month] += t["tva_marge_eur"]
        else:
            monthly_tva[month] += t["tva_marge_eur"]

    months_sorted = sorted(set(list(monthly_ca.keys())))

    monthly_summary = []
    for m in months_sorted:
        ca = round(monthly_ca[m], 2)
        tva_real = round(monthly_tva[m], 2)
        tva_est = round(monthly_tva_estimated[m], 2)
        monthly_summary.append({
            "month": m,
            "ca_eur": ca,
            "transactions": monthly_transactions[m],
            "avg_ticket": round(ca / monthly_transactions[m], 2) if monthly_transactions[m] else 0,
            "tva_marge_real_eur": tva_real,
            "tva_marge_estimated_eur": tva_est,
            "tva_marge_total_eur": round(tva_real + tva_est, 2),
            "tva_match_rate": round(tva_matched / len(payments) * 100, 1) if payments else 0
        })

    # ---- KPIs globaux ----
    total_ca = sum(p["amount_eur"] for p in payments)
    total_tva = sum(t["tva_marge_eur"] for t in tva_data)
    total_transactions = len(payments)
    avg_ticket = round(total_ca / total_transactions, 2) if total_transactions else 0

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
        last_ca = monthly_ca[last_month]
        today = datetime.now()
        days_elapsed = today.day
        days_in_month = 31 if today.month in [1, 3, 5, 7, 8, 10, 12] else 30
        projection = round(last_ca / days_elapsed * days_in_month, 2) if days_elapsed > 0 else 0
    else:
        projection = 0

    target_annual = 300000
    months_remaining = 12 - len(months_sorted)
    needed_monthly = round((target_annual - total_ca) / max(months_remaining, 1), 2)

    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "kpis": {
            "total_ca_eur": round(total_ca, 2),
            "total_transactions": total_transactions,
            "avg_ticket_eur": avg_ticket,
            "total_tva_marge_eur": round(total_tva, 2),
            "tva_match_rate_pct": round(tva_matched / total_transactions * 100, 1) if total_transactions else 0,
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
    print(f"✅ TVA sur marge : {kpis['total_tva_marge_eur']:,.2f} €")
    print(f"✅ Taux matching : {kpis['tva_match_rate_pct']}%")
    print(f"💾 Sauvegardé dans data/analytics.json")


if __name__ == "__main__":
    main()
