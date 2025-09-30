from cache import get_jita_buy_orders, TYPE_NAME_CACHE, TYPE_NAME_LOCK, JITA_MARKET_CACHE, JITA_MARKET_LOCK, debug_print
import requests, time, yaml

def bulk_names_to_ids(string):
    url = "https://esi.evetech.net/universe/ids"
    payload = [string]
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

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

def load_blueprint_from_fsd(type_id, filepath="./fsd/blueprints.yaml"):
    blueprint_lines = []
    found = False
    next_id_prefix = None

    with open(filepath, "r") as f:
        for line in f:
            # Détecte le début du blueprint
            if line.startswith(f"{type_id}:"):
                found = True
                blueprint_lines.append(line)
                continue

            if found:
                # Si on rencontre un nouveau blueprint (ligne commençant par un entier suivi de ":")
                if line.strip() and line.lstrip()[0].isdigit() and line.rstrip().endswith(":"):
                    break  # fin du blueprint actuel
                blueprint_lines.append(line)

    if not blueprint_lines:
        raise ValueError(f"Blueprint {type_id} not found in {filepath}")

    # Charger juste ce bloc YAML
    blueprint_yaml = yaml.safe_load("".join(blueprint_lines))
    return blueprint_yaml[type_id]
