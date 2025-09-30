from flask import Flask, render_template, request
from profile import Profile
from cache import DEBUG

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    menus = []
    message = None
    if request.method == "POST":
        try:
            char_name = request.form["character"]
            corp_name = request.form["corporation"]
            lp = int(request.form["lp"])
            max_isk = int(request.form["max_isk"])
            profile = Profile(char_name, corp_name, lp, max_isk)
            menus = profile.get_best_lp_items(num_menus=3)
            if not menus or all(len(menu)==0 for menu in menus):
                message = "No items could be selected with the given LP/ISK."
        except Exception as e:
            message = f"Error: {e}"
            print(message)
    return render_template("index.html", menus=menus, message=message)

if __name__ == "__main__":
    app.run(debug=True)
