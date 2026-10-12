"""Erzeugt eine Test-/Demo-Preisdatei (Preise.xlsx) fuer den Finanzplaner-Preisimport.

Format und Regeln gemaess docs/PREISDATEN-PROMPT.md bzw. docs/IMPORTFORMATE.md
Abschnitt "Preisvorlage Lebensmittel (XLSX)".

Aufruf:  python scripts/generate_preisdaten.py
Ausgabe: Preise.xlsx im Repo-Root, plus eine kurze Statistik auf stdout.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

random.seed(20260805)

TODAY = date(2026, 8, 5)
WEEKS = 52  # letzte 52 volle Kalenderwochen (keine laufende, unabgeschlossene Woche)

STORES = ["Aldi", "Lidl", "Netto", "Penny", "Norma", "Rewe", "Edeka", "Kaufland", "Alnatura", "Denns"]
OWNER_OPERATED = {"Rewe", "Edeka"}  # nur hier wird "Ort" gefuellt
BIO_SPECIALISTS = {"Alnatura", "Denns"}

# Preisfaktor je Laden relativ zur Rewe/Edeka-Basis (=1.0)
STORE_FACTOR = {
    "Aldi": 0.78, "Lidl": 0.80, "Netto": 0.83, "Penny": 0.85, "Norma": 0.82,
    "Rewe": 1.00, "Edeka": 1.02, "Kaufland": 0.92, "Alnatura": 1.08, "Denns": 1.10,
}

BRANCHES = {
    "Rewe": ["Rewe Bahnhofstraße", "Rewe Marktplatz", "Rewe Südstadt", "Rewe Nord", "Rewe Center"],
    "Edeka": ["Edeka Hauptstraße", "Edeka Waldweg", "Edeka am Park", "Edeka City", "Edeka Ost"],
}

NOTIZEN_POOL = ["Angebot am Eingang", "Mengenrabatt ab 3 Stück", "nur solange Vorrat reicht", "Sonderposten"]


@dataclass
class Article:
    name: str
    category: str
    unit: str
    package: float
    base_price: float  # Eigenmarke, konventionell, Rewe/Edeka-Basis
    bio_capable: bool = True
    brands: list = field(default_factory=list)
    alt_package: tuple | None = None  # (Packung, Preisfaktor) fuer die "andere Packungsgroesse"-Messiness


def art(name, category, unit, package, base_price, bio_capable=True, brands=None, alt_package=None):
    return Article(name, category, unit, package, base_price, bio_capable, brands or [], alt_package)


ARTICLES: list[Article] = []

# --- Nudeln / Reis / Mehl ---
for name, unit, pack, price, brands in [
    ("Spaghetti", "g", 500, 0.89, ["Barilla", "De Cecco", "Birkel"]),
    ("Fusilli", "g", 500, 0.89, ["Barilla", "Birkel"]),
    ("Penne", "g", 500, 0.89, ["Barilla", "De Cecco"]),
    ("Tagliatelle", "g", 500, 0.99, ["Barilla"]),
    ("Vollkornnudeln", "g", 500, 1.19, ["Barilla", "Birkel"]),
    ("Lasagneplatten", "g", 250, 1.29, ["Barilla"]),
    ("Basmatireis", "g", 1000, 2.49, ["Oryza", "Milsani"]),
    ("Jasminreis", "g", 1000, 2.29, ["Oryza"]),
    ("Milchreis", "g", 1000, 1.49, []),
    ("Parboiled Reis", "g", 1000, 1.99, ["Oryza"]),
    ("Vollkornreis", "g", 1000, 1.79, []),
    ("Risotto-Reis", "g", 400, 1.69, ["Oryza"]),
    ("Weizenmehl Type 405", "g", 1000, 0.69, []),
    ("Weizenmehl Type 550", "g", 1000, 0.79, []),
    ("Dinkelmehl", "g", 1000, 1.39, []),
    ("Roggenmehl", "g", 1000, 0.99, []),
    ("Maisstärke", "g", 250, 0.99, []),
    ("Grieß", "g", 500, 0.89, []),
    ("Couscous", "g", 500, 1.29, []),
    ("Bulgur", "g", 500, 1.19, []),
    ("Quinoa", "g", 300, 2.49, []),
    ("Haferflocken", "g", 500, 0.99, []),
    ("Polenta", "g", 500, 1.19, []),
]:
    ARTICLES.append(art(name, "Nudeln/Reis/Mehl", unit, pack, price, brands=brands))
ARTICLES[6].alt_package = (5000, 4.3)  # Basmatireis auch als 5-kg-Sack

# --- Konserven ---
for name, unit, pack, price, brands in [
    ("Mais", "g", 285, 0.79, ["Bonduelle"]),
    ("Kidneybohnen", "g", 400, 0.89, ["Bonduelle"]),
    ("Kichererbsen", "g", 400, 0.79, []),
    ("Weiße Bohnen", "g", 400, 0.79, []),
    ("Linsen", "g", 400, 0.89, []),
    ("Tomaten stückig", "g", 400, 0.69, ["Mutti"]),
    ("Tomatenmark", "g", 200, 0.69, ["Mutti"]),
    ("Passierte Tomaten", "g", 500, 0.79, ["Mutti"]),
    ("Pfirsiche", "g", 480, 1.19, ["Del Monte"]),
    ("Ananas", "g", 340, 1.29, ["Del Monte"]),
    ("Thunfisch in Öl", "g", 185, 1.69, ["Rio Mare"]),
    ("Thunfisch in Wasser", "g", 185, 1.59, ["Rio Mare"]),
    ("Erbsen", "g", 300, 0.69, ["Bonduelle"]),
    ("Rote Bete", "g", 330, 0.99, []),
    ("Sauerkraut", "g", 580, 1.09, []),
    ("Salzgurken", "g", 330, 1.19, []),
    ("Rotkohl", "g", 580, 1.09, []),
    ("Champignons", "g", 314, 0.99, []),
]:
    ARTICLES.append(art(name, "Konserven", unit, pack, price, brands=brands))

# --- Molkerei ---
for name, unit, pack, price, bio, brands in [
    ("Vollmilch", "l", 1, 0.99, True, ["Weihenstephan"]),
    ("Fettarme Milch", "l", 1, 0.95, True, []),
    ("H-Milch", "l", 1, 0.89, True, []),
    ("Buttermilch", "l", 1, 0.99, True, []),
    ("Sahne", "ml", 200, 0.89, True, []),
    ("Schlagsahne", "ml", 200, 0.99, True, []),
    ("Crème fraîche", "g", 200, 1.09, True, []),
    ("Schmand", "g", 200, 1.09, True, []),
    ("Joghurt Natur", "g", 500, 0.79, True, []),
    ("Joghurt Erdbeere", "g", 500, 0.99, True, ["Landliebe", "Müller", "Ehrmann"]),
    ("Joghurt Kirsche", "g", 500, 0.99, True, ["Landliebe", "Müller"]),
    ("Joghurt Vanille", "g", 500, 0.99, True, ["Müller", "Ehrmann"]),
    ("Joghurt Honig", "g", 500, 1.19, True, ["Landliebe"]),
    ("Joghurt Mango", "g", 500, 0.99, True, ["Ehrmann"]),
    ("Skyr", "g", 450, 1.79, True, []),
    ("Quark Magerstufe", "g", 500, 0.89, True, []),
    ("Quark 40%", "g", 500, 0.99, True, []),
    ("Butter", "g", 250, 1.79, True, ["Kerrygold"]),
    ("Margarine", "g", 500, 1.49, False, ["Rama"]),
    ("Frischkäse Natur", "g", 200, 1.19, True, ["Philadelphia"]),
    ("Frischkäse Kräuter", "g", 200, 1.29, True, ["Philadelphia"]),
    ("Gouda", "g", 400, 2.49, True, []),
    ("Emmentaler", "g", 400, 2.79, True, []),
    ("Mozzarella", "g", 125, 0.89, True, ["Galbani"]),
    ("Feta", "g", 200, 1.79, True, []),
    ("Parmesan", "g", 100, 2.29, True, ["Grana Padano"]),
    ("Schmelzkäse", "g", 200, 1.39, False, []),
    ("Eier (10er)", "Stk", 10, 2.49, True, []),
]:
    ARTICLES.append(art(name, "Molkerei", unit, pack, price, bio_capable=bio, brands=brands))

# --- Fleisch und Fisch ---
for name, unit, pack, price, brands in [
    ("Rinderhack", "g", 500, 4.49, []),
    ("Schweinehack", "g", 500, 3.29, []),
    ("Hähnchenbrust", "g", 500, 5.49, ["Wiesenhof"]),
    ("Hähnchenschenkel", "g", 500, 3.49, ["Wiesenhof"]),
    ("Rinderfilet", "g", 300, 8.99, []),
    ("Schweinefilet", "g", 500, 5.99, []),
    ("Bratwurst", "g", 400, 2.99, ["Reinert"]),
    ("Wiener Würstchen", "g", 400, 2.49, ["Reinert"]),
    ("Salami", "g", 100, 1.79, ["Rügenwalder"]),
    ("Kochschinken", "g", 100, 1.59, ["Rügenwalder"]),
    ("Speck", "g", 150, 1.99, []),
    ("Lachsfilet", "g", 250, 5.99, []),
    ("Kabeljaufilet", "g", 300, 4.99, []),
    ("Garnelen", "g", 200, 4.49, []),
    ("Forelle", "g", 300, 3.99, []),
    ("Leberwurst", "g", 150, 1.49, ["Rügenwalder"]),
    ("Bacon", "g", 150, 2.29, []),
    ("Putenbrust", "g", 200, 2.99, ["Wiesenhof"]),
]:
    ARTICLES.append(art(name, "Fleisch und Fisch", unit, pack, price, brands=brands))

# --- Obst und Gemüse ---
for name, unit, pack, price in [
    ("Bananen", "kg", 1, 1.49), ("Äpfel", "kg", 1, 1.99), ("Birnen", "kg", 1, 2.19),
    ("Orangen", "kg", 1, 1.79), ("Zitronen", "kg", 1, 2.49), ("Trauben", "kg", 1, 3.49),
    ("Erdbeeren", "g", 500, 2.49), ("Blaubeeren", "g", 250, 2.29), ("Kartoffeln", "kg", 2, 2.49),
    ("Zwiebeln", "kg", 1, 1.29), ("Knoblauch", "g", 100, 0.99), ("Karotten", "kg", 1, 1.19),
    ("Tomaten", "kg", 1, 2.49), ("Gurken", "Stk", 1, 0.89), ("Paprika", "kg", 1, 2.99),
    ("Zucchini", "kg", 1, 1.99), ("Brokkoli", "Stk", 1, 1.49), ("Blumenkohl", "Stk", 1, 1.69),
    ("Spinat", "g", 300, 1.79), ("Kopfsalat", "Stk", 1, 0.99), ("Champignons frisch", "g", 400, 1.49),
    ("Avocado", "Stk", 1, 1.29), ("Süßkartoffeln", "kg", 1, 2.79), ("Lauch", "Stk", 1, 0.99),
]:
    ARTICLES.append(art(name, "Obst und Gemüse", unit, pack, price))
ARTICLES[-9].alt_package = (250, 0.55)  # Tomaten auch als 250g-Schale

# --- Tiefkühl ---
for name, unit, pack, price, brands in [
    ("TK-Erbsen", "g", 1000, 1.79, ["Iglo"]),
    ("TK-Spinat", "g", 750, 1.99, ["Iglo"]),
    ("TK-Pommes", "g", 1000, 1.99, []),
    ("TK-Pizza", "g", 400, 2.49, ["Wagner", "Dr. Oetker"]),
    ("TK-Beerenmischung", "g", 500, 2.99, []),
    ("TK-Gemüsemischung", "g", 1000, 1.99, ["Iglo"]),
    ("Fischstäbchen", "g", 450, 2.29, ["Iglo"]),
    ("TK-Hähnchennuggets", "g", 450, 2.99, []),
    ("Eiscreme Vanille", "ml", 900, 2.49, ["Langnese"]),
    ("Eiscreme Schoko", "ml", 900, 2.49, ["Langnese"]),
]:
    ARTICLES.append(art(name, "Tiefkühl", unit, pack, price, brands=brands))

# --- Backwaren ---
for name, unit, pack, price in [
    ("Toastbrot", "g", 500, 1.29), ("Mischbrot", "g", 500, 1.79), ("Vollkornbrot", "g", 500, 1.99),
    ("Brötchen (6er)", "Stk", 6, 1.89), ("Baguette", "Stk", 1, 1.19), ("Croissants (4er)", "Stk", 4, 1.99),
    ("Knäckebrot", "g", 250, 1.19), ("Zwieback", "g", 225, 1.49), ("Rührkuchen", "g", 400, 2.49),
    ("Muffins (4er)", "Stk", 4, 2.29),
]:
    ARTICLES.append(art(name, "Backwaren", unit, pack, price, bio_capable=False))

# --- Getränke ---
for name, unit, pack, price, bio, brands in [
    ("Mineralwasser Medium", "l", 1, 0.55, False, ["Gerolsteiner", "Apollinaris"]),
    ("Mineralwasser Still", "l", 1, 0.55, False, ["Gerolsteiner", "Rewe Regional"]),
    ("Apfelsaft", "l", 1, 1.29, True, ["Rauch"]),
    ("Orangensaft", "l", 1, 1.79, True, ["Rauch", "Hohes C"]),
    ("Cola", "l", 1.5, 1.49, False, ["Coca-Cola"]),
    ("Limonade Zitrone", "l", 1.5, 1.09, False, ["Fanta"]),
    ("Bier Pils", "l", 0.5, 0.89, False, []),
    ("Weißwein", "l", 0.75, 3.99, True, []),
    ("Rotwein", "l", 0.75, 4.49, True, []),
    ("Eistee", "l", 1.5, 1.19, False, []),
]:
    ARTICLES.append(art(name, "Getränke", unit, pack, price, bio_capable=bio, brands=brands))
ARTICLES[-8].alt_package = (0.5, 0.45)  # Cola auch als kleine 0,5l-Flasche

# --- Süßwaren ---
for name, unit, pack, price, brands in [
    ("Vollmilchschokolade", "g", 100, 1.19, ["Milka", "Ritter Sport"]),
    ("Zartbitterschokolade", "g", 100, 1.29, ["Lindt", "Ritter Sport"]),
    ("Gummibärchen", "g", 200, 1.19, ["Haribo"]),
    ("Kekse Butter", "g", 200, 1.49, []),
    ("Kekse Schoko", "g", 200, 1.69, ["Oreo"]),
    ("Chips Paprika", "g", 175, 1.79, ["Funny-frisch"]),
    ("Chips Natur", "g", 175, 1.79, ["Pringles"]),
    ("Salzstangen", "g", 250, 0.99, []),
    ("Erdnüsse geröstet", "g", 200, 1.49, []),
    ("Müsliriegel (6er)", "Stk", 6, 2.49, []),
]:
    ARTICLES.append(art(name, "Süßwaren", unit, pack, price, bio_capable=False, brands=brands))

# --- Kaffee/Tee ---
for name, unit, pack, price, brands in [
    ("Kaffee gemahlen", "g", 500, 4.99, ["Jacobs", "Dallmayr"]),
    ("Kaffeebohnen", "g", 1000, 9.99, ["Lavazza", "Jacobs"]),
    ("Instantkaffee", "g", 200, 5.49, ["Nescafé"]),
    ("Kaffeepads", "Stk", 18, 3.49, ["Senseo"]),
    ("Schwarzer Tee", "g", 100, 1.99, ["Meßmer"]),
    ("Grüner Tee", "g", 100, 2.19, ["Meßmer"]),
    ("Kräutertee", "g", 100, 1.79, ["Meßmer"]),
    ("Kakaopulver", "g", 400, 2.49, ["Kaba"]),
]:
    ARTICLES.append(art(name, "Kaffee/Tee", unit, pack, price, bio_capable=True, brands=brands))

# --- Öl/Essig/Gewürze ---
for name, unit, pack, price, brands in [
    ("Sonnenblumenöl", "l", 1, 1.99, []),
    ("Olivenöl", "l", 0.5, 4.49, ["Bertolli"]),
    ("Rapsöl", "l", 1, 1.79, []),
    ("Balsamico Essig", "ml", 500, 2.49, []),
    ("Weinessig", "ml", 500, 1.49, []),
    ("Salz", "g", 500, 0.49, []),
    ("Pfeffer gemahlen", "g", 50, 1.29, []),
    ("Paprikapulver", "g", 50, 0.99, []),
    ("Currypulver", "g", 50, 1.19, []),
    ("Zimt", "g", 40, 1.09, []),
    ("Senf", "g", 200, 0.99, ["Thomy", "Develey"]),
    ("Ketchup", "ml", 500, 1.49, ["Heinz", "Develey"]),
    ("Mayonnaise", "ml", 400, 1.59, ["Thomy", "Develey"]),
]:
    ARTICLES.append(art(name, "Öl/Essig/Gewürze", unit, pack, price, bio_capable=True, brands=brands))

# --- Drogerie-Randsortiment ---
for name, unit, pack, price, brands in [
    ("Zahnpasta", "ml", 75, 1.99, ["Colgate", "Elmex"]),
    ("Duschgel", "ml", 250, 1.79, ["Dove", "Nivea"]),
    ("Shampoo", "ml", 300, 1.99, ["Head & Shoulders", "Nivea"]),
    ("Toilettenpapier (8er)", "Stk", 8, 3.99, []),
    ("Küchenrolle (4er)", "Stk", 4, 2.99, []),
    ("Spülmittel", "ml", 500, 1.29, ["Fairy", "Pril"]),
    ("Waschmittel", "g", 1350, 6.99, ["Persil", "Ariel"]),
    ("Müllbeutel (20er)", "Stk", 20, 1.99, []),
    ("Rasierklingen (4er)", "Stk", 4, 4.99, ["Gillette"]),
    ("Deo", "ml", 150, 1.99, ["Nivea", "Dove"]),
]:
    ARTICLES.append(art(name, "Drogerie-Randsortiment", unit, pack, price, bio_capable=False, brands=brands))

# --- Zusaetzliche Artikel je Kategorie (fuer die geforderte Bandbreite 300-400) ---
for name, unit, pack, price in [
    ("Kiwi", "kg", 1, 3.49), ("Mango", "Stk", 1, 1.49), ("Wassermelone", "kg", 1, 0.99),
    ("Honigmelone", "Stk", 1, 2.49), ("Pflaumen", "kg", 1, 2.99), ("Aprikosen", "kg", 1, 3.49),
    ("Kirschen", "kg", 1, 4.99), ("Himbeeren", "g", 250, 2.49), ("Brombeeren", "g", 250, 2.49),
    ("Rhabarber", "kg", 1, 2.99), ("Spargel", "kg", 1, 5.99), ("Rosenkohl", "kg", 1, 2.99),
    ("Weißkohl", "Stk", 1, 1.29), ("Kohlrabi", "Stk", 1, 0.99), ("Radieschen", "Bund", 1, 0.89),
    ("Rucola", "g", 75, 1.29), ("Feldsalat", "g", 100, 1.79), ("Eisbergsalat", "Stk", 1, 1.09),
    ("Staudensellerie", "Stk", 1, 1.49), ("Fenchel", "Stk", 1, 1.29), ("Pastinaken", "kg", 1, 2.49),
    ("Steckrüben", "kg", 1, 1.79), ("Maiskolben", "Stk", 1, 0.99), ("Kürbis", "kg", 1, 1.79),
    ("Ingwer", "g", 100, 0.99), ("Chili", "g", 50, 0.99), ("Frühlingszwiebeln", "Bund", 1, 0.79),
    ("Petersilie", "Bund", 1, 0.89), ("Basilikum frisch", "Stk", 1, 1.49), ("Schnittlauch", "Bund", 1, 0.89),
    ("Minze", "Bund", 1, 0.99),
]:
    ARTICLES.append(art(name, "Obst und Gemüse", unit, pack, price))

for name, unit, pack, price, bio, brands in [
    ("Bergkäse", "g", 400, 2.99, True, []), ("Camembert", "g", 125, 1.49, True, []),
    ("Brie", "g", 125, 1.59, True, []), ("Ricotta", "g", 250, 1.69, True, []),
    ("Hüttenkäse", "g", 200, 1.29, True, []), ("Ziegenkäse", "g", 150, 2.29, True, []),
    ("Kefir", "l", 1, 1.19, True, []), ("Streichkäse", "g", 200, 1.49, False, []),
    ("Bergbauernbutter", "g", 250, 1.99, True, []), ("Ziegenjoghurt", "g", 400, 1.79, True, []),
]:
    ARTICLES.append(art(name, "Molkerei", unit, pack, price, bio_capable=bio, brands=brands))

for name, unit, pack, price, brands in [
    ("Lammkeule", "g", 500, 7.99, []), ("Kalbsschnitzel", "g", 300, 6.99, []),
    ("Rinderbraten", "g", 500, 6.49, []), ("Gyrosfleisch", "g", 400, 4.49, []),
    ("Currywurst", "g", 300, 2.49, []), ("Frikadellen", "g", 400, 2.99, []),
    ("Cevapcici", "g", 400, 3.49, []), ("Rollmops", "g", 200, 1.99, []),
    ("Matjes", "g", 200, 2.49, []), ("Scholle", "g", 300, 3.99, []),
    ("Zander", "g", 300, 5.49, []), ("Garnelenspieße", "g", 200, 3.99, []),
    ("Hackfleisch gemischt", "g", 500, 3.79, []), ("Kasseler", "g", 400, 3.49, []),
]:
    ARTICLES.append(art(name, "Fleisch und Fisch", unit, pack, price, brands=brands))

for name, unit, pack, price, bio, brands in [
    ("Traubensaft", "l", 1, 1.79, True, []), ("Multivitaminsaft", "l", 1, 1.59, False, ["Hohes C"]),
    ("Energy Drink", "ml", 250, 1.09, False, ["Red Bull"]), ("Kombucha", "ml", 330, 1.99, True, []),
    ("Tonic Water", "l", 1, 1.29, False, ["Schweppes"]), ("Ingwer-Shot", "ml", 60, 1.99, True, []),
    ("Sekt", "l", 0.75, 3.99, False, []), ("Whisky", "l", 0.7, 14.99, False, []),
    ("Wodka", "l", 0.7, 9.99, False, []), ("Gin", "l", 0.7, 12.99, False, []),
    ("Kaffeegetränk to go", "ml", 250, 1.79, False, []), ("Kakao trinkfertig", "l", 1, 1.29, False, []),
    ("Sportgetränk isotonisch", "l", 0.75, 1.49, False, []),
]:
    ARTICLES.append(art(name, "Getränke", unit, pack, price, bio_capable=bio, brands=brands))

for name, unit, pack, price, brands in [
    ("Marshmallows", "g", 150, 1.49, []), ("Lakritz", "g", 200, 1.29, ["Haribo"]),
    ("Pralinen", "g", 200, 3.99, ["Lindt"]), ("Nuss-Nougat-Creme", "g", 400, 2.49, ["Nutella"]),
    ("Marmelade Erdbeer", "g", 400, 1.79, ["Schwartau"]), ("Marmelade Aprikose", "g", 400, 1.79, ["Schwartau"]),
    ("Honig", "g", 500, 3.49, ["Langnese"]), ("Ahornsirup", "ml", 250, 3.99, []),
    ("Popcorn süß", "g", 100, 1.29, []), ("Popcorn salzig", "g", 100, 1.29, []),
    ("Fruchtgummi vegan", "g", 175, 1.49, ["Katjes"]), ("Schokoriegel", "Stk", 1, 0.79, ["Kitkat", "Twix"]),
    ("Eis am Stiel", "Stk", 4, 2.49, ["Langnese"]),
]:
    ARTICLES.append(art(name, "Süßwaren", unit, pack, price, bio_capable=False, brands=brands))

for name, unit, pack, price in [
    ("Ciabatta", "Stk", 1, 1.79), ("Fladenbrot", "Stk", 1, 1.49), ("Tortilla Wraps (8er)", "Stk", 8, 1.99),
    ("Pumpernickel", "g", 500, 1.99), ("Laugenbrezel", "Stk", 1, 0.99), ("Donuts", "Stk", 1, 1.29),
    ("Berliner", "Stk", 1, 1.19), ("Waffeln", "Stk", 4, 1.99), ("Blätterteig", "g", 275, 1.49),
    ("Pizzateig fertig", "g", 400, 1.29),
]:
    ARTICLES.append(art(name, "Backwaren", unit, pack, price, bio_capable=False))

for name, unit, pack, price, brands in [
    ("Spargel (Glas)", "g", 330, 1.99, []), ("Oliven", "g", 200, 1.79, []), ("Kapern", "g", 100, 1.49, []),
    ("Pesto", "g", 190, 1.79, ["Barilla"]), ("Antipasti gemischt", "g", 300, 2.49, []),
    ("Artischockenherzen", "g", 280, 2.29, []), ("Pilze in Öl", "g", 300, 2.49, []),
    ("Kokosmilch", "ml", 400, 1.29, []), ("Süßkartoffelpüree", "g", 400, 1.99, []),
]:
    ARTICLES.append(art(name, "Konserven", unit, pack, price, brands=brands))

for name, unit, pack, price, brands in [
    ("TK-Kräutermischung", "g", 100, 1.19, []), ("TK-Blätterteig", "g", 275, 1.49, []),
    ("TK-Croissants", "Stk", 4, 2.49, []), ("TK-Waffeln", "Stk", 8, 1.99, []),
    ("TK-Fischfilet", "g", 400, 3.99, []), ("TK-Garnelen", "g", 300, 4.99, []),
    ("TK-Gemüsepfanne", "g", 750, 2.49, []), ("TK-Kartoffelecken", "g", 750, 1.79, []),
]:
    ARTICLES.append(art(name, "Tiefkühl", unit, pack, price, brands=brands))

for name, unit, pack, price, brands in [
    ("Espresso Bohnen", "g", 1000, 10.99, ["Lavazza"]), ("Filterkaffee entkoffeiniert", "g", 500, 5.49, ["Jacobs"]),
    ("Chai Tee", "g", 100, 2.29, ["Meßmer"]), ("Matcha Pulver", "g", 50, 6.99, []),
    ("Rooibos Tee", "g", 100, 1.99, ["Meßmer"]),
]:
    ARTICLES.append(art(name, "Kaffee/Tee", unit, pack, price, bio_capable=True, brands=brands))

for name, unit, pack, price, brands in [
    ("Kokosöl", "ml", 500, 4.49, []), ("Sesamöl", "ml", 250, 3.49, []), ("Walnussöl", "ml", 250, 4.99, []),
    ("Sojasauce", "ml", 250, 1.79, ["Kikkoman"]), ("Worcestersauce", "ml", 150, 1.99, ["Lea & Perrins"]),
    ("Chilisauce", "ml", 250, 1.79, []), ("Barbecue-Sauce", "ml", 400, 1.99, []),
    ("Vanillezucker", "g", 8, 0.29, ["Dr. Oetker"]), ("Backpulver", "g", 15, 0.29, ["Dr. Oetker"]),
    ("Hefe frisch", "g", 42, 0.35, []), ("Muskatnuss", "g", 20, 1.29, []), ("Kurkuma", "g", 40, 0.99, []),
    ("Kreuzkümmel", "g", 40, 1.09, []), ("Oregano", "g", 12, 0.99, []), ("Basilikum getrocknet", "g", 10, 0.99, []),
    ("Thymian getrocknet", "g", 15, 0.99, []), ("Lorbeerblätter", "g", 5, 0.79, []),
    ("Chiliflocken", "g", 40, 1.09, []), ("Knoblauchpulver", "g", 45, 0.99, []), ("Zwiebelpulver", "g", 45, 0.99, []),
]:
    ARTICLES.append(art(name, "Öl/Essig/Gewürze", unit, pack, price, bio_capable=True, brands=brands))

for name, unit, pack, price, brands in [
    ("Handseife", "ml", 250, 1.49, []), ("Bodylotion", "ml", 250, 2.49, ["Nivea"]),
    ("Sonnencreme", "ml", 200, 6.99, ["Nivea"]), ("Wattepads (100er)", "Stk", 100, 1.29, []),
    ("Ohrstäbchen", "Stk", 100, 0.99, []), ("Taschentücher (10er)", "Stk", 10, 1.99, ["Tempo"]),
    ("Allzweckreiniger", "ml", 750, 1.79, ["Meister Proper"]), ("Glasreiniger", "ml", 500, 1.99, []),
    ("Badreiniger", "ml", 750, 2.29, ["Domestos"]), ("WC-Reiniger", "ml", 750, 1.99, ["WC-Ente"]),
    ("Weichspüler", "ml", 1000, 2.99, ["Lenor"]), ("Geschirrspültabs (30er)", "Stk", 30, 6.99, ["Finish"]),
    ("Küchenpapier feucht", "Stk", 1, 1.99, []),
]:
    ARTICLES.append(art(name, "Drogerie-Randsortiment", unit, pack, price, bio_capable=False, brands=brands))

for name, unit, pack, price in [
    ("Vollkornmehl Type 1050", "g", 1000, 1.29), ("Kartoffelpüree Flocken", "g", 400, 1.49),
    ("Hirse", "g", 500, 1.99), ("Amaranth", "g", 300, 2.49), ("Buchweizen", "g", 500, 2.29),
    ("Nudeln glutenfrei", "g", 400, 1.99), ("Reisnudeln", "g", 250, 1.49), ("Glasnudeln", "g", 250, 1.69),
    ("Sushi-Nori (10er)", "Stk", 10, 2.99), ("Wan-Tan-Teigblätter", "g", 250, 2.49),
]:
    ARTICLES.append(art(name, "Nudeln/Reis/Mehl", unit, pack, price))

assert 300 <= len(ARTICLES) <= 400, f"Artikelzahl außerhalb der Vorgabe: {len(ARTICLES)}"

# Inflation je Artikel: 3-5% Gesamtanstieg ueber die 52 Wochen
ARTICLE_INFLATION = {a.name: random.uniform(0.03, 0.05) for a in ARTICLES}

# 15 Artikel mit erkennbarem Angebotszyklus (Referenzloesung fuer spaetere Zykluserkennung)
CYCLE_CANDIDATES = [
    ("Mineralwasser Medium", "Rewe", "Gerolsteiner", 5),
    ("Spaghetti", "Edeka", "Barilla", 4),
    ("Vollmilchschokolade", "Aldi", "Milka", 6),
    ("Cola", "Edeka", "Coca-Cola", 4),
    ("Kaffee gemahlen", "Rewe", "Jacobs", 6),
    ("Waschmittel", "Kaufland", "Persil", 5),
    ("Gummibärchen", "Lidl", "Haribo", 4),
    ("Butter", "Edeka", "Kerrygold", 5),
    ("Hähnchenbrust", "Rewe", "Wiesenhof", 4),
    ("Zartbitterschokolade", "Netto", "Lindt", 6),
    ("Chips Natur", "Penny", "Pringles", 5),
    ("Orangensaft", "Edeka", "Hohes C", 4),
    ("Kaffeebohnen", "Kaufland", "Lavazza", 6),
    ("Bier Pils", "Rewe", "", 4),
    ("Toilettenpapier (8er)", "Norma", "", 5),
]
CYCLES = []
for name, store, brand, cycle_weeks in CYCLE_CANDIDATES:
    phase = random.randint(0, cycle_weeks - 1)
    jitter = random.randint(-1, 1)
    CYCLES.append({
        "name": name, "store": store, "brand": brand,
        "cycle_weeks": cycle_weeks, "phase": phase, "jitter": jitter,
    })

ARTICLE_BY_NAME = {a.name: a for a in ARTICLES}
THIS_MONDAY = TODAY - timedelta(days=TODAY.weekday())


def week_monday(weeks_ago: int) -> date:
    """weeks_ago=1 ist die letzte vollstaendig abgeschlossene Kalenderwoche."""
    return THIS_MONDAY - timedelta(weeks=weeks_ago)


def normal_price(article: Article, store: str, weeks_ago: int, is_bio: bool, brand: str | None) -> float:
    price = article.base_price
    if brand and brand != "Eigenmarke":
        price *= random.uniform(1.25, 1.60)
    if is_bio:
        price *= random.uniform(1.40, 1.80)
    price *= STORE_FACTOR[store]
    price *= random.uniform(0.92, 1.08)  # Jahresschwankung ohne Angebot
    inflation_total = ARTICLE_INFLATION[article.name]
    price *= 1 + inflation_total * (1 - weeks_ago / WEEKS)
    return round(price, 2)


def pick_brand(article: Article, forced: str | None = None) -> str:
    if forced is not None:
        return forced if forced else "Eigenmarke"
    if article.brands and random.random() < 0.55:
        return random.choice(article.brands)
    if random.random() < 0.78:
        return "Eigenmarke"
    return ""


def pick_bio(article: Article, store: str, forced: bool | None = None) -> bool:
    if not article.bio_capable:
        return False
    if forced is not None:
        return forced
    if store in BIO_SPECIALISTS:
        return random.random() < 0.92
    return random.random() < 0.12


def build_row(article: Article, store: str, weeks_ago: int, *, forced_offer: bool = False,
              forced_brand: str | None = None) -> dict:
    monday = week_monday(weeks_ago)
    is_bio = pick_bio(article, store)
    brand = pick_brand(article, forced=forced_brand)
    price = normal_price(article, store, weeks_ago, is_bio, brand)

    is_offer = forced_offer or (random.random() < 0.20)
    guelt_von = guelt_bis = None
    if is_offer:
        price = round(price * random.uniform(0.60, 0.80), 2)
        guelt_von = monday
        guelt_bis = monday + timedelta(days=5)  # Montag bis Samstag

    seen_offset = random.randint(0, 6)
    gesehen_am = monday + timedelta(days=seen_offset)

    package = article.package
    unit = article.unit
    if article.alt_package and random.random() < 0.12:
        alt_pack, alt_factor = article.alt_package
        package = alt_pack
        price = round(price * alt_factor, 2)

    ort = ""
    if store in OWNER_OPERATED:
        ort = random.choice(BRANCHES[store])

    notiz = random.choice(NOTIZEN_POOL) if random.random() < 0.04 else ""

    return {
        "Zutat": article.name,
        "Laden": store,
        "Ort": ort,
        "Marke": brand,
        "Bio": "ja" if is_bio else "nein",
        "Packung": package,
        "Einheit": unit,
        "Preis": price,
        "Angebot": "ja" if is_offer else "nein",
        "gültig von": guelt_von,
        "gültig bis": guelt_bis,
        "gesehen am": gesehen_am,
        "Notiz": notiz,
    }


def build_rows() -> list[dict]:
    rows: list[dict] = []
    cycle_keys = {(c["name"], c["store"]) for c in CYCLES}

    # 1) Zyklusartikel: garantierte woechentliche Beobachtung im jeweiligen Laden
    for cyc in CYCLES:
        article = ARTICLE_BY_NAME[cyc["name"]]
        for weeks_ago in range(1, WEEKS + 1):
            due = (weeks_ago - cyc["phase"]) % cyc["cycle_weeks"] == 0
            offer_week = due
            if cyc["jitter"] and random.random() < 0.5:
                offer_week = (weeks_ago - cyc["phase"] + cyc["jitter"]) % cyc["cycle_weeks"] == 0
            rows.append(build_row(article, cyc["store"], weeks_ago,
                                   forced_offer=offer_week,
                                   forced_brand=cyc["brand"] if cyc["brand"] else "Eigenmarke"))

    # 2) Restliche Zeilen: pro Laden/Woche eine Zufallsauswahl an Artikeln
    for store in STORES:
        pool = [a for a in ARTICLES if (a.name, store) not in cycle_keys]
        if store in BIO_SPECIALISTS:
            bio_pool = [a for a in pool if a.bio_capable]
            other_pool = [a for a in pool if not a.bio_capable]
        for weeks_ago in range(1, WEEKS + 1):
            n = random.randint(14, 22)
            if store in BIO_SPECIALISTS:
                n_other = max(0, int(n * 0.1))
                sample = random.sample(bio_pool, min(n - n_other, len(bio_pool)))
                sample += random.sample(other_pool, min(n_other, len(other_pool)))
            else:
                sample = random.sample(pool, min(n, len(pool)))
            for article in sample:
                rows.append(build_row(article, store, weeks_ago))

    # 3) Messiness (< 2 % der Zeilen zusammen): vereinzelt "Laden" unbekannt
    messy_count = max(1, int(len(rows) * 0.01))
    for idx in random.sample(range(len(rows)), min(messy_count, len(rows))):
        rows[idx]["Laden"] = ""
        rows[idx]["Ort"] = ""

    random.shuffle(rows)
    return rows


def write_workbook(rows: list[dict], out_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Preise"
    headers = ["Zutat", "Laden", "Ort", "Marke", "Bio", "Packung", "Einheit", "Preis",
               "Angebot", "gültig von", "gültig bis", "gesehen am", "Notiz"]
    ws.append(headers)
    for row in rows:
        ws.append([row[h] for h in headers])
    wb.save(out_path)


def main() -> None:
    rows = build_rows()
    out_path = Path(__file__).resolve().parents[1] / "Preise.xlsx"
    write_workbook(rows, out_path)

    total = len(rows)
    distinct_articles = len({r["Zutat"] for r in rows})
    offers = sum(1 for r in rows if r["Angebot"] == "ja")

    print(f"Datei geschrieben: {out_path}")
    print(f"Zeilen gesamt:      {total}")
    print(f"Verschiedene Artikel: {distinct_articles}")
    print(f"Anteil Angebote:    {offers / total:.1%}")
    print()
    print("Angebotszyklen (Referenzloesung):")
    for cyc in CYCLES:
        brand = cyc["brand"] or "Eigenmarke"
        print(f"  - {cyc['name']} bei {cyc['store']} ({brand}): alle {cyc['cycle_weeks']} Wochen"
              f", Streuung ±{1 if cyc['jitter'] else 0} Woche(n)")


if __name__ == "__main__":
    main()
