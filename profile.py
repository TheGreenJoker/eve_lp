import requests, random

from utils import bulk_names_to_ids, get_type_name_cached, get_jita_buy_price_total_cached, load_blueprint_from_fsd
from cache import get_lp_store_dict_cached, debug_print

class Profile:
    def __init__(self, character_name, corp_name, lp, max_isk):
        from utils import bulk_names_to_ids
        self.character_id = bulk_names_to_ids(character_name)["characters"][0]["id"]
        self.corporation_id = bulk_names_to_ids(corp_name)["corporations"][0]["id"]
        self.lp = lp
        self.max_investment = max_isk
    
    def compute_blueprint_cost(self, type_id, quantity=1, cache=None):
        """
        Calcule le coût total d'un blueprint depuis ./fsd/blueprint.yaml
        """
        if cache is None:
            cache = {}

        key = (f"bp_{type_id}", quantity)
        if key in cache:
            return cache[key]

        bp_data = load_blueprint_from_fsd(type_id)
        materials = bp_data.get("activities", {}).get("manufacturing", {}).get("materials", [])

        total_lp = 0
        total_isk = 0
        requirements = []

        lp_store = get_lp_store_dict_cached(self.corporation_id)

        for mat in materials:
            mat_id = mat["typeID"]
            mat_qty = mat["quantity"] * quantity

            if mat_id in lp_store and lp_store[mat_id]["lp_cost"] > 0 and lp_store[mat_id]["isk_cost"] > 0:
                # Acheter via LP store si rentable
                req_cost = self.compute_total_cost(mat_id, mat_qty, lp_store, cache)
            else:
                # Sinon prix Jita
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

        if type_id not in lp_store:
            total = {"lp": 0, "isk": 0, "requirements": []}
            cache[key] = total
            return total

        offer = lp_store[type_id]
        if offer.get("lp_cost",0)<=0 or offer.get("isk_cost",0)<=0:
            total = {"lp":0,"isk":0,"requirements":[]}
            cache[key] = total
            return total

        total_lp = offer['lp_cost']*quantity
        total_isk = offer['isk_cost']*quantity
        requirements = []

        for req in offer.get("required_items", []):
            req_cost = self.compute_total_cost(req["type_id"], req["quantity"]*quantity, lp_store, cache)
            total_lp += req_cost["lp"]

            total_isk += req_cost["isk"]
            requirements.append({
                "name": get_type_name_cached(req["type_id"]),
                "quantity": req["quantity"]*quantity,
                "isk": req_cost["isk"]
            })

        total = {"lp": total_lp, "isk": total_isk, "requirements": requirements}
        cache[key] = total
        debug_print(f"[TOTAL_COST] {get_type_name_cached(type_id)} x{quantity} => LP={total_lp}, ISK={total_isk}")
        return total

    def get_best_lp_items(self, num_menus=10):
        lp_store = get_lp_store_dict_cached(self.corporation_id)
        cache = {}

        candidates = []
        for type_id, offer in lp_store.items():
            if offer.get('lp_cost',0)<=0 or offer.get('isk_cost',0)<=0:
                continue
            max_qty_lp = self.lp // offer['lp_cost']
            max_qty_isk = self.max_investment // offer['isk_cost']
            max_qty = min(max_qty_lp, max_qty_isk)
            if max_qty<=0:
                continue

            if "lueprint" in get_type_name_cached(type_id):
                cost_info = self.compute_blueprint_cost(type_id, max_qty, cache)
            else:
                cost_info = self.compute_total_cost(type_id, max_qty, lp_store, cache)

            market_value_total = get_jita_buy_price_total_cached(type_id,max_qty)
            if market_value_total<=0:
                continue

            profit = market_value_total - cost_info['isk']
            profit_ratio = profit/max(cost_info['lp'],1)

            candidates.append({
                "type_id": type_id,
                "name": get_type_name_cached(type_id),
                "quantity": max_qty,
                "lp": cost_info['lp'],
                "isk": cost_info['isk'],
                "market_value": market_value_total,
                "unit_price_jita": market_value_total/max_qty,
                "profit_ratio": profit_ratio,
                "profit_total": profit,
                "requirements": cost_info['requirements'],
                "is_blueprint": "Blueprint" in get_type_name_cached(type_id)
            })

        # --- Generate diverse menus ---
        menus = []
        strategies = [
            lambda x: x['profit_total'],
            lambda x: x['profit_ratio'],
            lambda x: x['market_value'],
            lambda x: x['lp']
        ]

        while len(menus) < num_menus:
            menu = []
            lp_left = self.lp
            isk_left = self.max_investment

            # random strategy or force blueprint first
            strategy = random.choice(strategies)
            sorted_candidates = sorted(candidates, key=strategy, reverse=True)

            # optionally force blueprints first if rentable
            blueprints = [c for c in sorted_candidates if c['is_blueprint']]
            non_blueprints = [c for c in sorted_candidates if not c['is_blueprint']]
            sorted_candidates = blueprints + non_blueprints

            for item in sorted_candidates:
                if item['lp']>lp_left or item['isk']>isk_left:
                    continue
                menu.append(item)
                lp_left -= item['lp']
                isk_left -= item['isk']

            # add small variations by shuffling non-blueprint items
            random.shuffle(menu)
            menus.append(menu)

        debug_print(f"[BEST_ITEMS] Generated {len(menus)} menus")
        return menus