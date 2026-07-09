import os
import sys
import json
import datetime

# Force UTF-8 output on Windows (avoids charmap codec errors for → ✓ etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, Response, stream_with_context
)
import requests as req
from bs4 import BeautifulSoup


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
app.jinja_env.filters["enumerate"] = enumerate

# ─── Constants ────────────────────────────────────────────────────────────────

BASE_URL = "https://hostelviewx.psgitech.ac.in/Hostel"

PTOKEN_MAP = {
    "CHILLI GOBI": 8,
    "CHICKEN PALLIPALAYAM/CHETINAD": 1,
    "MUSHROOM PALLIPALAYAM": 27,
    "OMELETTE": 7,
    "BOILED EGG/EGG KURMA": 85,
    "MUTTON CURRY": 2,
    "BABY CORN MANCHURIAN": 88,
    "PANEER": 89,
}

# Per-dish max quantity, matching the portal's own client-side limits
# (see e.g. "quantityInput6 > 2" for most, but omelette allows 3)
DEFAULT_MAX_QTY = 2
MAX_QTY_MAP = {
    "OMELETTE": 3,
}

def get_max_qty(dish_name):
    return MAX_QTY_MAP.get(dish_name, DEFAULT_MAX_QTY)

BUTTON_TO_DISH = {
    "Gobichilli": "CHILLI GOBI",
    "Chickentoken": "CHICKEN PALLIPALAYAM/CHETINAD",
    "MushroomManchuriantoken": "MUSHROOM PALLIPALAYAM",
    "Omelettetoken": "OMELETTE",
    "EggCurrytoken": "BOILED EGG/EGG KURMA",
    "Muttontoken": "MUTTON CURRY",
    "BabyCorntoken": "BABY CORN MANCHURIAN",
    "Paneertoken": "PANEER",
}

PORTAL_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
}

# ─── Day-of-week meal schedule ───────────────────────────────────────────────
# Dishes with an empty dict have no tokens this cycle and will be greyed out.

DISH_SCHEDULE = {
    "CHILLI GOBI": {
        "Thursday": "Dinner",
        "Sunday":   "Lunch",
    },
    "CHICKEN PALLIPALAYAM/CHETINAD": {
        "Sunday":   "Lunch",
        "Thursday": "Dinner",
    },
    "MUSHROOM PALLIPALAYAM": {
        "Thursday": "Dinner",
    },
    "OMELETTE": {
        "Monday":    "Lunch",
        "Tuesday":   "Lunch",
        "Wednesday": "Breakfast",
        "Saturday":  "Lunch",
        "Sunday":    "Breakfast",
    },
    "BOILED EGG/EGG KURMA": {
        "Thursday": "Lunch",
        "Friday":   "Dinner",
    },
    "MUTTON CURRY":        {},
    "BABY CORN MANCHURIAN": {},
    "PANEER":              {},
}

def get_meal_for_date(dish_name, date_str):
    """Return the meal time for a dish on a given date (format: DD-MM-YYYY)."""
    try:
        day = datetime.datetime.strptime(date_str, "%d-%m-%Y").strftime("%A")
        return DISH_SCHEDULE.get(dish_name, {}).get(day, "Lunch")
    except Exception:
        return "Lunch"

# ─── Session helpers ──────────────────────────────────────────────────────────

def build_requests_session():
    """Rebuild a requests.Session restoring the AspNetCore.Session cookie."""
    s = req.Session()
    cookies = session.get("portal_cookies", {})
    print(f"[SESSION] Restoring cookies: {cookies}")
    for name, value in cookies.items():
        s.cookies.set(name, value, domain="hostelviewx.psgitech.ac.in")
    return s

def save_session_cookies(s):
    session["portal_cookies"] = dict(s.cookies)

# ─── Scraping ─────────────────────────────────────────────────────────────────

def enrich_dates(dish_name, raw_dates):
    """Turn ['16-07-2026', ...] into [{'date':'16-07-2026','day':'Thu','meal':'Dinner'}, ...]."""
    enriched = []
    for d in raw_dates:
        try:
            day_short = datetime.datetime.strptime(d, "%d-%m-%Y").strftime("%a")  # Mon, Tue...
        except Exception:
            day_short = ""
        enriched.append({
            "date": d,
            "day": day_short,
            "meal": get_meal_for_date(dish_name, d),
        })
    return enriched

def scrape_dishes(s):
    # Fetch the student dashboard page
    resp = s.get(f"{BASE_URL}/Student/StudentView", headers=PORTAL_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    dishes = []
    for btn_id, dish_name in BUTTON_TO_DISH.items():
        token_id = PTOKEN_MAP.get(dish_name, 0)
        max_qty = get_max_qty(dish_name)

        btn = soup.find(id=btn_id)
        has_tokens = bool(DISH_SCHEDULE.get(dish_name))  # empty dict → no tokens
        if btn is None:
            dishes.append({"name": dish_name, "token_id": token_id, "dates": [], "has_tokens": has_tokens, "max_qty": max_qty})
            continue
        container = btn.parent
        for _ in range(8):
            if container is None:
                break
            select_el = container.find("select")
            radio_els = container.find_all("input", {"type": "radio"})
            if select_el and radio_els:
                break
            container = container.parent
        if container is None:
            dishes.append({"name": dish_name, "token_id": token_id, "dates": [], "has_tokens": has_tokens, "max_qty": max_qty})
            continue
        raw_dates = []
        if select_el:
            for option in select_el.find_all("option")[1:]:
                val = option.get("value", "").strip()
                if val:
                    raw_dates.append(val)

        dates = enrich_dates(dish_name, raw_dates)
        print(f"[SCRAPE] {dish_name} → token_id={token_id}, has_tokens={has_tokens}, max_qty={max_qty}, dates={dates}")
        dishes.append({
            "name": dish_name,
            "token_id": token_id,
            "dates": dates,
            "has_tokens": has_tokens,
            "max_qty": max_qty,
        })
    return dishes

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if session.get("portal_cookies"):
        return redirect(url_for("dashboard"))
    return render_template("index.html", view="login", error=None)


@app.route("/login", methods=["POST"])
def login():
    rollno   = request.form.get("rollno", "").strip()
    password = request.form.get("password", "").strip()

    if not rollno:
        return render_template("index.html", view="login", error="Roll number is required.")
    if not password:
        return render_template("index.html", view="login", error="Password is required.")

    s = req.Session()

    # POST to the real login endpoint (confirmed via DevTools)
    try:
        auth_resp = s.post(
            "https://hostelviewx.psgitech.ac.in/Hostel/Login/Authenticate",
            data={"name": rollno, "password": password},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://hostelviewx.psgitech.ac.in/Hostel/Home/Index",
            },
            timeout=15,
            allow_redirects=True,
        )
    except req.exceptions.RequestException as e:
        return render_template("index.html", view="login", error=f"Network error: {e}")

    # Debug — always printed to terminal
    print(f"\n[LOGIN] Status          : {auth_resp.status_code}")
    print(f"[LOGIN] Final URL       : {auth_resp.url}")
    print(f"[LOGIN] Cookies         : {dict(s.cookies)}")
    print(f"[LOGIN] Body (first 400): {auth_resp.text[:400]}")

    # Portal returns JSON — Token field present means success
    try:
        result = auth_resp.json()
        print(f"[LOGIN] JSON response   : {result}")
        if result.get("Token"):
            session["jwt_token"]      = result["Token"]
            session["portal_cookies"] = dict(s.cookies)
            session["rollno"]         = rollno
            return redirect(url_for("dashboard"))
        else:
            return render_template("index.html", view="login",
                                   error="Invalid credentials.")
    except Exception as e:
        return render_template("index.html", view="login",
                               error=f"Login error: {e}")


@app.route("/dashboard")
def dashboard():
    if not session.get("portal_cookies"):
        return redirect(url_for("index"))
    s = build_requests_session()
    try:
        dishes = scrape_dishes(s)
    except req.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            session.clear()
            return redirect(url_for("index"))
        return render_template("index.html", view="login", error=f"Scrape error: {e}")
    except Exception as e:
        return render_template("index.html", view="login", error=f"Error loading dashboard: {e}")
    save_session_cookies(s)
    rollno = session.get("rollno", "Unknown")
    return render_template("index.html", view="dashboard", dishes=dishes, rollno=rollno)


@app.route("/preview", methods=["POST"])
def preview():
    if not session.get("portal_cookies"):
        return redirect(url_for("index"))

    print(f"[PREVIEW] Raw form data: {dict(request.form)}")

    bookings = []
    idx = 0
    while True:
        name_key = f"dish_name_{idx}"
        if name_key not in request.form:
            break
        name = request.form.get(name_key, "")
        # selections_json is a list of {"date": "...", "qty": N} — one entry per
        # date the user actually ticked, each with its own quantity.
        selections_json = request.form.get(f"dish_selections_{idx}", "[]")
        try:
            selections = json.loads(selections_json)
        except Exception:
            selections = []

        max_qty = get_max_qty(name)
        for sel in selections:
            date = sel.get("date", "")
            qty = int(sel.get("qty", 0))
            if not date or qty <= 0:
                continue
            qty = min(qty, max_qty)  # enforce server-side cap regardless of client
            meal = get_meal_for_date(name, date)
            bookings.append({
                "name": name,
                "token_id": PTOKEN_MAP.get(name, 0),
                "date": date,
                "meal": meal,
                "qty": qty,
            })
        idx += 1

    rollno = session.get("rollno", "Unknown")
    return render_template("index.html", view="preview", bookings=bookings, rollno=rollno)


BOOKING_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/Student/StudentView",
}


@app.route("/book", methods=["POST"])
def book():
    if not session.get("portal_cookies"):
        return Response('data: {"error":"not authenticated"}\n\n', mimetype="text/event-stream")

    bookings = request.get_json(force=True, silent=True) or []

    def generate():
        success_count = 0
        failed_count = 0
        s = build_requests_session()

        for index, b in enumerate(bookings):
            dish_name = b.get("name", "")
            date = b.get("date", "")
            meal = get_meal_for_date(dish_name, date)
            qty = min(int(b.get("qty", 1)), get_max_qty(dish_name))
            token_id = b.get("token_id") or PTOKEN_MAP.get(dish_name, 0)

            yield f"data: {json.dumps({'status': True, 'index': index, 'total': len(bookings), 'dish': dish_name, 'date': date})}\n\n"

            ok = False
            msg = ""
            try:
                payload = {
                    "PTOKEN_ID": token_id,
                    "ddtokenqty": qty,
                    "Tokendatetime": date,
                    "MEALTIME": meal,
                }
                print(f"[BOOK] POST newStudentTokenApply {payload}")
                resp = s.post(
                    f"{BASE_URL}/Student/newStudentTokenApply",
                    data=payload,
                    headers=BOOKING_HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
                result = resp.json()
                print(f"[BOOK] Response: {result}")

                if result.get("oresult") == 1:
                    ok = True
                    msg = "Booked successfully"
                    success_count += 1
                else:
                    msg = f"Portal rejected booking: {result}"
                    failed_count += 1

            except req.exceptions.RequestException as e:
                msg = f"Network error: {str(e)[:200]}"
                failed_count += 1
            except Exception as e:
                msg = str(e)[:200]
                failed_count += 1

            yield f"data: {json.dumps({'index': index, 'dish': dish_name, 'date': date, 'meal': meal, 'qty': qty, 'ok': ok, 'msg': msg})}\n\n"

        # Persist any refreshed cookies back to the session
        save_session_cookies(s)
        yield f"data: {json.dumps({'done': True, 'success': success_count, 'failed': failed_count})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )





@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
