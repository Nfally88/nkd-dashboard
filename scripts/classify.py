"""
classify.py — Classe un article vendu selon son régime de TVA (règle fixée par Nfally) :
  - téléphones, tablettes, ordinateurs → "occasion" (TVA sur marge),
    sauf si le nom contient « neuf » → "neuf" (TVA normale 20 %)
  - accessoires et réparations → "accessoire_reparation" (TVA normale 20 %)
  - vente sans nom d'article (montant libre) → "libre" (TVA normale 20 %)
"""

import re
import unicodedata


def simplify(text):
    """Minuscules, sans accents ni ponctuation."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# Mots qui signalent un accessoire ou une réparation : ils passent avant les marques
# (« Coque iPhone 13 », « Écran Samsung A52 » ne sont pas des téléphones).
ACCESSORY_REPAIR_WORDS = [
    "coque", "etui", "housse", "protection", "protege", "verre", "vitre", "film",
    "chargeur", "charge", "cable", "adaptateur", "prise", "batterie externe", "powerbank",
    "ecouteur", "ecoute", "casque", "kit pieton", "enceinte", "airpods", "support", "perche", "power bank", "cordon",
    "carte sd", "carte memoire", "cle usb", "accessoire", "bracelet", "stylet", "clavier", "souris",
    "reparation", "remplacement", "changement", "ecran", "batterie", "connecteur",
    "camera", "lentille", "nappe", "bouton", "haut parleur", "micro", "desoxydation",
    "diagnostic", "main d oeuvre", "forfait", "deblocage", "desimlockage", "transfert",
    "installation", "nettoyage", "sav", "carte sim",
]

DEVICE_WORDS = [
    "iphone", "ipad", "macbook", "imac", "samsung", "galaxy", "xiaomi", "redmi", "poco",
    "oppo", "huawei", "pixel", "honor", "oneplus", "motorola", "moto g", "nokia", "realme",
    "vivo", "sony xperia", "xperia", "wiko", "alcatel", "crosscall", "fairphone", "nothing phone",
    "telephone", "smartphone", "portable", "tablette", "tab", "ordinateur", "laptop", "pc",
    "notebook", "chromebook", "surface", "lenovo", "asus", "acer", "dell", "thinkpad",
]

# Catégories Square de NKD TECH qui contiennent des appareils revendus
# (« TELEPHONE OCCASION », « ACHAT VENTE TELEPHONE », « ACHAT VENTE TABLETTE »…).
# Toute autre catégorie (NKD REPARATION, NKD ACCESSOIRES, PALM, COQUES, BATTERIE TELEPHONE…)
# est un accessoire ou une réparation, même si son nom contient « téléphone ».
DEVICE_CATEGORY_WORDS = ["occasion", "achat vente", "reconditionne"]
DEVICE_ONLY_CATEGORIES = {"telephone", "telephones", "smartphone", "smartphones", "tablette", "tablettes",
                          "ordinateur", "ordinateurs", "pc"}

# Noms que Square donne aux ventes « montant libre »
CUSTOM_AMOUNT_NAMES = {"", "custom amount", "montant personnalise", "montant libre", "article personnalise"}


def has_word(text, words):
    padded = f" {text} "
    return any(f" {w} " in padded for w in words)


def is_device_category(c):
    return c in DEVICE_ONLY_CATEGORIES or has_word(c, DEVICE_CATEGORY_WORDS)


def classify_item(name, category=""):
    n = simplify(name)
    c = simplify(category)

    if n in CUSTOM_AMOUNT_NAMES and not c:
        return "libre"

    is_neuf = has_word(n, ["neuf", "neufs", "neuve", "neuves"])

    # Article explicitement vendu sous le régime de la marge (ex. « PLAYSTATION PS5 … TVA sur la marge »)
    if "tva sur la marge" in n:
        return "neuf" if is_neuf else "occasion"

    # La catégorie Square, quand elle existe, fait foi
    if c:
        if is_device_category(c) and not has_word(n, ACCESSORY_REPAIR_WORDS):
            return "neuf" if is_neuf else "occasion"
        return "accessoire_reparation"

    # Sans catégorie : d'après le nom
    if has_word(n, ACCESSORY_REPAIR_WORDS):
        return "accessoire_reparation"
    if has_word(n, DEVICE_WORDS):
        return "neuf" if is_neuf else "occasion"
    return "accessoire_reparation"
