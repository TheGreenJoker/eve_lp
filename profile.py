# profile.py
import random
import yaml
from typing import Optional

from utils import (
    bulk_names_to_ids,
    debug_print,
    get_jita_buy_price_total_cached,
    get_type_name_cached
)

from cache import get_lp_store_dict_cached



def load_blueprint_from_fsd(type_id: int, filepath: str = "./fsd/blueprints.yaml"):
    """
    Read the big blueprint.yaml line-by-line and return the YAML block for type_id.
    Returns a dict representing the blueprint entry (same shape as one item in the full yaml).
    Raises ValueError if not found.
    """
    start_token = f"{type_id}:"
    block_lines = []
    found = False

    def is_top_level_id_line(s: str):
        # top-level blueprint lines have format: 17919:
        # we consider "starts with digits then ':'" and no leading spaces
        stripped = s.rstrip("\n")
        if not stripped:
            return False
        # top-level lines have no leading spaces
        if stripped[0].isspace():
            return False
        # starts with digits and then colon
        # example: "17919:" or "17919:  "
        first_part = stripped.split()[0]
        return first_part.endswith(":") and first_part[:-1].isdigit()

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if not found:
                if line.startswith(start_token):
                    found = True
                    block_lines.append(line)
            else:
                # stop when next top-level id line encountered
                if is_top_level_id_line(line):
                    break
                block_lines.append(line)

    if not block_lines:
        raise ValueError(f"Blueprint {type_id} not found in {filepath}")

    # safe_load the small YAML fragment; it yields a dict {type_id: {...}}
    fragment = yaml.safe_load("".join(block_lines))
    if not isinstance(fragment, dict) or type_id not in fragment:
        raise ValueError(f"Blueprint {type_id} parse error or missing in fragment")
    return fragment[type_id]


class Profile:
    def __init__(self, character_name: str, corp_name: str, lp: int, max_isk: int):
        # store names and ids
        ids = bulk_names_to_ids(character_name)
        self.character_id = ids.get("characters", [{}])[0].get("id")
        corp_ids = bulk_names_to_ids(corp_name)
        self.corporation_id = corp_ids.get("corporations", [{}])[0].get("id")
        self.character_name = character_name
        self.corp_name = corp_name
        self.lp = int(lp)
        self.max_investment = int(max_isk)

    # --- compute cost for an LP-store offer (recursive for required items) ---
    def compute_total_cost(self, type_id: int, quantity: int = 1, lp_store: Optional[dict] = None, cache: Optional[dict] = None):
        if lp_store is None:
            lp_store = get_lp_store_dict_cached(self.corporation_id)
        if cache is None:
            cache = {}

        key = (type_id, quantity, "offer")
        if key in cache:
            return cache[key]

        if type_id not in lp_store:
            total = {"lp": 0, "isk": 0, "requirements": []}
            cache[key] = total
            return total

        offer = lp_store[type_id]
        # protect against bad data
        if offer.get("lp_cost", 0) <= 0:
            total = {"lp": 0, "isk": 0, "requirements": []}
            cache[key] = total
            return total

        total_lp = offer["lp_cost"] * quantity
        total_isk = offer.get("isk_cost", 0) * quantity
        requirements = []

        for req in offer.get("required_items", []):
            req_type = req["type_id"]
            req_qty = req["quantity"] * quantity
            if req_type not in lp_store:
                # fallback prix marché pour item requis absent du LP store
                isk_price = get_jita_buy_price_total_cached(req_type, req_qty)
                req_cost = {"lp": 0, "isk": isk_price, "requirements": []}
            else:
                req_cost = self.compute_total_cost(req_type, req_qty, lp_store, cache)
            total_lp += req_cost["lp"]
            total_isk += req_cost["isk"]
            requirements.append({
                "name": get_type_name_cached(req_type),
                "quantity": req_qty,
                "lp": req_cost["lp"],
                "isk": req_cost["isk"]
            })

        res = {"lp": total_lp, "isk": total_isk, "requirements": requirements}
        cache[key] = res
        debug_print(f"[Offer cost] {type_id} x{quantity} => LP={total_lp}, ISK={total_isk}")
        return res

    # --- compute cost for a blueprint via SDE fragment (streamed) ---
    def compute_blueprint_cost(self, type_id: int, quantity: int = 1, cache: Optional[dict] = None):
        if cache is None:
            cache = {}
        key = (f"bp_{type_id}", quantity)
        if key in cache:
            return cache[key]

        try:
            bp_data = load_blueprint_from_fsd(type_id, filepath="./fsd/blueprints.yaml")
        except Exception as e:
            debug_print(f"[Blueprint ERROR] {type_id}: {e}")
            res = {"lp": 0, "isk": 0, "requirements": []}
            cache[key] = res
            return res

        manuf = bp_data.get("activities", {}).get("manufacturing", {})
        materials = manuf.get("materials", [])
        total_lp = 0
        total_isk = 0
        requirements = []

        lp_store = get_lp_store_dict_cached(self.corporation_id)

        for mat in materials:
            mat_id = mat.get("typeID") or mat.get("typeId") or mat.get("type_id")
            mat_qty_each = int(mat.get("quantity", 0))
            mat_qty = mat_qty_each * quantity
            if not mat_id:
                continue

            if mat_id in lp_store and lp_store[mat_id].get("lp_cost", 0) > 0:
                # compute via LP store (recursively)
                req_cost = self.compute_total_cost(mat_id, mat_qty, lp_store, cache)
            else:
                # buy components on market (Jita buy orders)
                req_cost = {"lp": 0, "isk": get_jita_buy_price_total_cached(mat_id, mat_qty), "requirements": []}

            total_lp += req_cost["lp"]
            total_isk += req_cost["isk"]
            requirements.append({
                "name": get_type_name_cached(mat_id),
                "quantity": mat_qty,
                "lp": req_cost["lp"],
                "isk": req_cost["isk"]
            })

        res = {"lp": total_lp, "isk": total_isk, "requirements": requirements}
        cache[key] = res
        debug_print(f"[Blueprint cost] {type_id} x{quantity} => LP={total_lp}, ISK={total_isk}")
        return res

    # --- helper building menu from candidate list ---
    def _build_menu_from_candidates(self, candidates):
        lp_store = get_lp_store_dict_cached(self.corporation_id)
        remaining_lp = self.lp
        remaining_isk = self.max_investment
        shopping_list = []

        for item in candidates:
            # compute how many units we can take of this item
            # item['lp'] and item['isk'] here are cost per 'max_qty' in candidate,
            # so we re-evaluate quantity possible
            max_qty = item["max_qty"]
            # compute qty by scaling lp/isk per unit:
            # first compute per-unit LP/ISK from candidate totals:
            per_unit_lp = item["lp"] / max_qty if max_qty > 0 else 0
            per_unit_isk = item["isk"] / max_qty if max_qty > 0 else 0
            # compute how many units we can buy given remaining resources
            qty_by_lp = int(remaining_lp // per_unit_lp) if per_unit_lp > 0 else 0
            qty_by_isk = int(remaining_isk // per_unit_isk) if per_unit_isk > 0 else 0
            qty = min(max_qty, qty_by_lp if per_unit_lp>0 else max_qty, qty_by_isk if per_unit_isk>0 else max_qty)
            if qty <= 0:
                continue

            # recompute exact cost for qty
            if item["is_blueprint"]:
                cost_info = self.compute_blueprint_cost(item["type_id"], qty)
            else:
                cost_info = self.compute_total_cost(item["type_id"], qty, lp_store)

            if cost_info["lp"] > remaining_lp or cost_info["isk"] > remaining_isk:
                continue

            shopping_list.append({
                "type_id": item["type_id"],
                "name": item["name"],
                "quantity": qty,
                "lp": cost_info["lp"],
                "isk": cost_info["isk"],
                "market_value": item["market_value"] * (qty / item["max_qty"]) if item["max_qty"]>0 else item["market_value"],
                "unit_price_jita": (item["market_value"] / item["max_qty"]) if item["max_qty"]>0 else 0,
                "profit_ratio": item.get("profit_ratio_lp", 0),
                "requirements": cost_info["requirements"],
                "is_blueprint": item["is_blueprint"],
            })

            remaining_lp -= cost_info["lp"]
            remaining_isk -= cost_info["isk"]
            if remaining_lp <= 0 or remaining_isk <= 0:
                break

        return shopping_list

    # --- generate 10 menus with different strategies ---
    def get_best_lp_menus(self, num_menus: int = 10):
        lp_store = get_lp_store_dict_cached(self.corporation_id)
        cache = {}
        items = []

        # build candidate items
        for type_id, offer in lp_store.items():
            if offer.get("lp_cost", 0) <= 0 or offer.get("isk_cost", 0) <= 0:
                continue
            name = get_type_name_cached(type_id)
            is_blueprint = "Blueprint" in name

            max_qty_lp = int(self.lp // offer["lp_cost"]) if offer["lp_cost"]>0 else 0
            max_qty_isk = int(self.max_investment // offer["isk_cost"]) if offer.get("isk_cost",0)>0 else 0
            max_qty = min(max_qty_lp, max_qty_isk)
            if max_qty <= 0:
                continue

            if is_blueprint:
                cost_info = self.compute_blueprint_cost(type_id, max_qty, cache)
            else:
                cost_info = self.compute_total_cost(type_id, max_qty, lp_store, cache)

            if cost_info["lp"] <= 0 or cost_info["isk"] <= 0:
                continue

            market_value = get_jita_buy_price_total_cached(type_id, max_qty)
            profit_total = market_value - cost_info["isk"]
            profit_ratio_lp = profit_total / cost_info["lp"] if cost_info["lp"]>0 else 0
            profit_ratio_isk = profit_total / cost_info["isk"] if cost_info["isk"]>0 else 0
            profit_ratio_combined = profit_total / (cost_info["lp"] + cost_info["isk"]) if (cost_info["lp"] + cost_info["isk"])>0 else 0

            items.append({
                "type_id": type_id,
                "name": name,
                "max_qty": max_qty,
                "lp": cost_info["lp"],
                "isk": cost_info["isk"],
                "market_value": market_value,
                "profit_total": profit_total,
                "profit_ratio_lp": profit_ratio_lp,
                "profit_ratio_isk": profit_ratio_isk,
                "profit_ratio_combined": profit_ratio_combined,
                "is_blueprint": is_blueprint,
                "requirements": cost_info["requirements"],
            })

        # strategies list (we will create 10 menus):
        strategies = [
            lambda arr: sorted(arr, key=lambda x: x["profit_total"], reverse=True),       # 0 profit total
            lambda arr: sorted(arr, key=lambda x: x["profit_ratio_lp"], reverse=True),   # 1 profit/LP
            lambda arr: sorted(arr, key=lambda x: x["profit_ratio_isk"], reverse=True),  # 2 profit/ISK
            lambda arr: sorted(arr, key=lambda x: x["max_qty"], reverse=True),           # 3 big qty
            lambda arr: sorted(arr, key=lambda x: x["lp"]),                              # 4 small lp first
            lambda arr: sorted(arr, key=lambda x: x["isk"]),                             # 5 small isk first
            lambda arr: sorted(arr, key=lambda x: (not x["is_blueprint"], -x["profit_total"])), # 6 blueprints first
            lambda arr: random.sample(arr, len(arr)),                                    # 7 random
            lambda arr: sorted(arr, key=lambda x: x["profit_ratio_combined"], reverse=True), # 8 combined ratio
            lambda arr: sorted(arr, key=lambda x: abs(x["lp"] - x["isk"]))               # 9 balanced LP/ISK
        ]

        menus = []
        for i in range(min(num_menus, len(strategies))):
            candidates = strategies[i](items.copy())
            menus.append(self._build_menu_from_candidates(candidates))

        debug_print(f"[MENUS] Generated {len(menus)} menus")
        return menus
