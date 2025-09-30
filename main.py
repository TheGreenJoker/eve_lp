import threading
import time
from flask import Flask, render_template, request
import requests

DEBUG = False  # <-- active/désactive tous les prints de debug

def debug_print(*args):
    if DEBUG:
        print(*args)

app = Flask(__name__)

# --- Variables globales ---
JITA_MARKET_CACHE = {}
JITA_MARKET_LOCK = threading.Lock()
JITA_MARKET_LAST_UPDATE = 0

TYPE_NAME_CACHE = {}
TYPE_NAME_LOCK = threading.Lock()

LP_STORE_CACHE = {}
LP_STORE_LOCK = threading.Lock()
LP_STORE_LAST_UPDATE = {}

# --- Fonctions pour le cache et mise à jour ---
def bulk_names_to_ids(string):
    url = "https://esi.evetech.net/universe/ids"
    payload = [string]
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    debug_print(f"bulk_names_to_ids('{string}') -> {response.json()}")
    return response.json()

def get_jita_buy_orders(type_id):
    debug_print(f"Fetching Jita buy orders for type_id {type_id}")
    BASE_URL = "https://esi.evetech.net/latest/markets/10000002/orders/"
    orders = []
    page = 1
    while True:
        params = {"order_type": "buy", "type_id": type_id, "page": page}
        resp = requests.get(BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        orders.extend([o for o in data if o["location_id"] == 60003760])
        if "X-Pages" in resp.headers and page < int(resp.headers["X-Pages"]):
            page += 1
        else:
            break
    debug_print(f"Found {len(orders)} orders for type_id {type_id}")
    return sorted(orders, key=lambda x: x["price"], reverse=True)

def update_jita_market():
    global JITA_MARKET_CACHE, JITA_MARKET_LAST_UPDATE
    while True:
        try:
            with LP_STORE_LOCK:
                type_ids = list(LP_STORE_CACHE.keys())
            debug_print("Thread Jita update, type_ids:", type_ids)
            market_data = {}
            for type_id in type_ids:
                market_data[type_id] = get_jita_buy_orders(type_id)
            with JITA_MARKET_LOCK:
                JITA_MARKET_CACHE = market_data
                JITA_MARKET_LAST_UPDATE = time.time()
            debug_print("Jita market cache updated")
        except Exception as e:
            print("Erreur update Jita:", e)
        time.sleep(10)

def get_jita_buy_price_total_cached(type_id, quantity):
    with JITA_MARKET_LOCK:
        orders = JITA_MARKET_CACHE.get(type_id, [])
    if not orders:
        debug_print(f"No cached orders for type_id {type_id}, fetching live")
        orders = get_jita_buy_orders(type_id)
        with JITA_MARKET_LOCK:
            JITA_MARKET_CACHE[type_id] = orders
    total_value = 0
    remaining = quantity
    for o in orders:
        sell_qty = min(remaining, o["volume_remain"])
        total_value += sell_qty * o["price"]
        remaining -= sell_qty
        if remaining <= 0:
            break
    debug_print(f"Total Jita buy value for type_id {type_id}, qty {quantity} -> {total_value}")
    return total_value

def get_type_name_cached(type_id):
    with TYPE_NAME_LOCK:
        entry = TYPE_NAME_CACHE.get(type_id, {})
        if entry and (time.time() - entry.get("time", 0) < 60):
            return entry["name"]
    url = f"https://esi.evetech.net/latest/universe/types/{type_id}/"
    resp = requests.get(url, headers={"Accept": "application/json"})
    resp.raise_for_status()
    name = resp.json().get("name", f"ID {type_id}")
    with TYPE_NAME_LOCK:
        TYPE_NAME_CACHE[type_id] = {"name": name, "time": time.time()}
    debug_print(f"Type ID {type_id} -> Name: {name}")
    return name

def get_lp_store_dict_cached(corporation_id):
    global LP_STORE_LAST_UPDATE
    with LP_STORE_LOCK:
        last_time = LP_STORE_LAST_UPDATE.get(corporation_id, 0)
        if time.time() - last_time < 60 and corporation_id in LP_STORE_CACHE:
            return LP_STORE_CACHE[corporation_id]

    url = f"https://esi.evetech.net/latest/loyalty/stores/{corporation_id}/offers/"
    resp = requests.get(url, headers={"Accept": "application/json"})
    resp.raise_for_status()
    offers = resp.json()
    with LP_STORE_LOCK:
        LP_STORE_CACHE[corporation_id] = {o["type_id"]: o for o in offers}
        LP_STORE_LAST_UPDATE[corporation_id] = time.time()
    debug_print(f"LP store for corp {corporation_id} -> {len(offers)} offers")
    return LP_STORE_CACHE[corporation_id]

# --- Thread de mise à jour du market Jita ---
market_thread = threading.Thread(target=update_jita_market, daemon=True)
market_thread.start()




import xml.etree.ElementTree as ET

def fetch_blueprint_materials(type_id):
    """Interroge Fuzzwork pour obtenir les matériaux pour blueprint manufacture."""
    url = f"https://www.fuzzwork.co.uk/blueprint/api/xml.php?typeid={type_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    xml = resp.text
    root = ET.fromstring(xml)
    mats = []
    # L’API Fuzzwork donne des activities ; on cherche activity id = 1 (manufacture)
    for activity in root.findall("activity"):
        if activity.get("activityid") == "1":  # manufacture
            for material in activity.findall("materials/material"):
                mat_typeid = int(material.find("typeid").text)
                qty = int(material.find("quantity").text)
                mats.append({"type_id": mat_typeid, "quantity": qty})
    return mats

def compute_blueprint_cost(self, type_id, runs=1):
    mats = fetch_blueprint_materials(type_id)
    total_lp = 0
    total_isk = 0
    requirements = []

    for mat in mats:
        qty = mat["quantity"] * runs
        # Coût au marché
        isk_market = get_jita_buy_price_total_cached(mat["type_id"], qty)

        # Coût via LP store si possible
        lp_store = get_lp_store_dict_cached(self.corporation_id)
        if mat["type_id"] in lp_store:
            alt = self.compute_total_cost(mat["type_id"], qty, lp_store)
            if alt["lp"] > 0 and alt["isk"] > 0:
                # On compare “valeur LP convertie + isk” vs coût marché
                # Ici, on pourrait définir une “valeur de LP en ISK” pour comparer
                # Pour simplifier, on peut choisir le plus petit coût ISK (ignorant LP)
                if alt["isk"] < isk_market:
                    total_lp += alt["lp"]
                    total_isk += alt["isk"]
                    requirements.append({
                        "name": get_type_name_cached(mat["type_id"]),
                        "quantity": qty,
                        "isk": alt["isk"],
                        "lp": alt["lp"]
                    })
                    continue
        # Sinon marché pur
        total_isk += isk_market
        requirements.append({
            "name": get_type_name_cached(mat["type_id"]),
            "quantity": qty,
            "isk": isk_market,
            "lp": 0
        })

    # Coût d’installation / taxe
    install_cost = runs * 10000  # à définir plus précisément selon système
    total_isk += install_cost

    return {"lp": total_lp, "isk": total_isk, "requirements": requirements}




# --- Classe Profile ---
class Profile:
    def __init__(self, character_name, corp_name, lp, max_isk):
        debug_print(f"Initializing Profile: char='{character_name}', corp='{corp_name}', LP={lp}, ISK={max_isk}")
        self.character_id = bulk_names_to_ids(character_name)["characters"][0]["id"]
        self.corporation_id = bulk_names_to_ids(corp_name)["corporations"][0]["id"]
        self.lp = lp
        self.max_investment = max_isk
        debug_print(f"Character ID: {self.character_id}, Corporation ID: {self.corporation_id}")
    
    def compute_blueprint_cost(self, type_id, quantity=1, cache=None):
        """
        Calcule le coût total de production d'un blueprint :
        - Récupère les matériaux nécessaires via ESI.
        - Calcule le prix à Jita pour chaque composant.
        - Utilise le LP store pour les composants achetables si c'est plus rentable.
        """
        if cache is None:
            cache = {}

        key = (f"bp_{type_id}", quantity)
        if key in cache:
            return cache[key]

        url = f"https://esi.evetech.net/latest/industry/blueprints/{type_id}/"
        try:
            resp = requests.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            bp_data = resp.json()
        except Exception as e:
            debug_print(f"[Blueprint ERROR] type_id={type_id}: {e}")
            return {"lp": 0, "isk": 0, "requirements": []}

        requirements = []
        total_lp = 0
        total_isk = 0

        for activity, activity_data in bp_data.get("activities", {}).items():
            if activity == "manufacturing":
                for mat in activity_data.get("materials", []):
                    mat_id = mat["type_id"]
                    mat_qty = mat["quantity"] * quantity

                    # Vérif si dispo au LP store
                    lp_store = get_lp_store_dict_cached(self.corporation_id)
                    if mat_id in lp_store:
                        req_cost = self.compute_total_cost(mat_id, mat_qty, lp_store, cache)
                    else:
                        # sinon achat direct Jita
                        req_cost = {"lp": 0, "isk": get_jita_buy_price_total_cached(mat_id, mat_qty), "requirements": []}

                    total_lp += req_cost["lp"]
                    total_isk += req_cost["isk"]
                    requirements.append({
                        "name": get_type_name_cached(mat_id),
                        "quantity": mat_qty,
                        "lp": req_cost["lp"],
                        "isk": req_cost["isk"]
                    })

        total = {"lp": total_lp, "isk": total_isk, "requirements": requirements}
        cache[key] = total
        debug_print(f"[Blueprint cost] {type_id} x{quantity} => LP={total_lp}, ISK={total_isk}")
        return total

    def compute_total_cost(self, type_id, quantity=1, lp_store=None, cache=None):
        if lp_store is None:
            lp_store = get_lp_store_dict_cached(self.corporation_id)
        if cache is None:
            cache = {}
        key = (type_id, quantity)
        if key in cache:
            return cache[key]

        # Cas Blueprint → fabrication
        name = get_type_name_cached(type_id)
        if "Blueprint" in name:
            return self.compute_blueprint_cost(type_id, quantity, cache)

        if type_id not in lp_store:
            total = {"lp": 0, "isk": 0, "requirements": []}
            cache[key] = total
            return total

        offer = lp_store[type_id]
        if offer.get("lp_cost", 0) <= 0:  # LP obligatoire > 0
            total = {"lp": 0, "isk": 0, "requirements": []}
            cache[key] = total
            return total

        total_lp = offer['lp_cost'] * quantity
        total_isk = offer.get("isk_cost", 0) * quantity
        requirements = []

        for req in offer.get("required_items", []):
            req_cost = self.compute_total_cost(req["type_id"], req["quantity"] * quantity, lp_store, cache)
            total_lp += req_cost["lp"]
            total_isk += req_cost["isk"]
            requirements.append({
                "name": get_type_name_cached(req["type_id"]),
                "quantity": req["quantity"] * quantity,
                "lp": req_cost["lp"],
                "isk": req_cost["isk"]
            })

        debug_print(f"[compute_total_cost] {type_id} x{quantity} => LP={total_lp}, ISK={total_isk}")
        total = {"lp": total_lp, "isk": total_isk, "requirements": requirements}
        cache[key] = total
        return total

    def get_best_lp_items(self):
        lp_store = get_lp_store_dict_cached(self.corporation_id)
        remaining_lp = self.lp
        remaining_isk = self.max_investment
        shopping_list = []

        offers = {}
        for type_id, offer in lp_store.items():
            name = get_type_name_cached(type_id)

            if "Blueprint" in name:
                offers[type_id] = offer.copy()
                offers[type_id]['max_qty'] = remaining_lp // offer['lp_cost'] if offer['lp_cost'] > 0 else 0
                continue

            if offer.get('lp_cost', 0) <= 0 or offer.get('isk_cost', 0) <= 0:
                continue

            offers[type_id] = offer.copy()
            offers[type_id]['max_qty'] = remaining_lp // offer['lp_cost']

        while True:
            best_item = None
            best_value = 0

            for type_id, offer in offers.items():
                if offer['max_qty'] <= 0:
                    continue

                name = get_type_name_cached(type_id)

                max_by_lp = remaining_lp // offer['lp_cost'] if offer['lp_cost'] > 0 else 0
                max_by_isk = (remaining_isk // offer['isk_cost']) if offer.get('isk_cost', 0) > 0 else remaining_isk
                qty = min(offer['max_qty'], max_by_lp, max_by_isk)

                if qty <= 0:
                    continue

                market_value_total = get_jita_buy_price_total_cached(type_id, qty)
                if market_value_total <= 0:
                    continue

                cost_info = self.compute_total_cost(type_id, qty, lp_store)
                profit_ratio = 0
                if cost_info['lp'] > 0:
                    profit_ratio = market_value_total / cost_info['lp']

                debug_print(f"[best_lp_items] {name} qty={qty}, mv={market_value_total}, lp={cost_info['lp']}, isk={cost_info['isk']}, ratio={profit_ratio}")

                if profit_ratio > best_value:
                    best_value = profit_ratio
                    best_item = {
                        "type_id": type_id,
                        "name": name,
                        "quantity": qty,
                        "lp": cost_info['lp'],
                        "isk": cost_info['isk'],
                        "market_value": market_value_total,
                        "unit_price_jita": market_value_total / qty if qty > 0 else 0,
                        "profit_ratio": profit_ratio,
                        "requirements": cost_info['requirements']
                    }

            if not best_item:
                break

            shopping_list.append(best_item)
            remaining_lp -= best_item['lp']
            remaining_isk -= best_item['isk']
            offers[best_item['type_id']]['max_qty'] -= best_item['quantity']

        return shopping_list

# --- Flask routes ---
@app.route("/", methods=["GET", "POST"])
def index():
    best_items = []
    message = None
    if request.method == "POST":
        try:
            char_name = request.form["character"]
            corp_name = request.form["corporation"]
            lp = int(request.form["lp"])
            max_isk = int(request.form["max_isk"])
            profile = Profile(char_name, corp_name, lp, max_isk)
            best_items = profile.get_best_lp_items()
            if not best_items:
                message = "Aucun item trouvable avec les paramètres fournis."
        except Exception as e:
            message = f"Erreur : {e}"
            print("Exception:", e)
    return render_template("index.html", best_items=best_items, message=message)

if __name__ == "__main__":
    app.run(debug=DEBUG)
