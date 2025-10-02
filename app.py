# main.py
from flask import Flask, render_template, request
from profile import Profile
from cache import DEBUG

app = Flask(__name__)

def format_compact(value):
    try:
        n = float(value)
    except (ValueError, TypeError):
        return value
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    elif abs_n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif abs_n >= 1_000:
        return f"{n/1_000:.1f}k"
    else:
        return f"{n:.0f}"
app.jinja_env.filters['format_compact'] = format_compact

@app.route("/", methods=["GET", "POST"])
def index():
    menus = []
    message = None
    if request.method == "POST":
        char = request.form.get("character")
        corp = request.form.get("corporation")
        lp = int(request.form.get("lp") or 0)
        isk = int(request.form.get("max_isk") or 0)
        profile = Profile(char, corp, lp, isk)
        try:
            menus_raw = profile.get_best_lp_menus(num_menus=10)
            if not menus_raw or all(len(m) == 0 for m in menus_raw):
                message = "No menus generated for these parameters."
            else:
                menus = []
                for menu in menus_raw:
                    total_lp = sum(item["lp"] for item in menu)
                    total_isk = sum(item["isk"] for item in menu)
                    total_market = sum(item["market_value"] for item in menu)
                    profit_total = total_market - total_isk
                    rentability = profit_total / total_lp if total_lp > 0 else 0
                    menus.append({
                        "menu_items": menu,
                        "total_lp": total_lp,
                        "total_isk": total_isk,
                        "profit_total": profit_total,
                        "rentability": rentability,
                    })
                menus.sort(key=lambda m: m["rentability"], reverse=True)
                # Trier menus par rentabilité décroissante
                menus.sort(key=lambda m: m["rentability"], reverse=True)
        except Exception as e:
            message = f"Error during generation: {e}"
    return render_template("index.html", menus=menus, message=message)


if __name__ == "__main__":
    app.run(debug=DEBUG)
