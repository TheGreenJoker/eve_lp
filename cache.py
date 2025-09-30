import threading
import requests
import time

DEBUG = True
def debug_print(msg):
    if DEBUG:
        print(msg)

JITA_MARKET_CACHE = {}
JITA_MARKET_LOCK = threading.Lock()
JITA_MARKET_LAST_UPDATE = 0

TYPE_NAME_CACHE = {}
TYPE_NAME_LOCK = threading.Lock()

LP_STORE_CACHE = {}
LP_STORE_LOCK = threading.Lock()
LP_STORE_LAST_UPDATE = {}

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
    while True:
        try:
            with LP_STORE_LOCK:
                type_ids = list(LP_STORE_CACHE.keys())
            market_data = {}
            for type_id in type_ids:
                market_data[type_id] = get_jita_buy_orders(type_id)
            with JITA_MARKET_LOCK:
                JITA_MARKET_CACHE.update(market_data)
        except Exception as e:
            print("Error updating Jita market:", e)
        time.sleep(10)

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

threading.Thread(target=update_jita_market, daemon=True).start()