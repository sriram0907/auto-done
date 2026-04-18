import os
import re
import sys
import json
import time

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
app.secret_key = os.urandom(24)
app.jinja_env.filters["enumerate"] = enumerate

# ─── Constants ────────────────────────────────────────────────────────────────

BASE_URL = "https://hostelviewx.psgitech.ac.in/Hostel"

PTOKEN_MAP = {
    "CHILLI GOBI": 1,
    "CHICKEN PALLIPALAYAM/CHETINAD": 2,
    "MUSHROOM PALLIPALAYAM": 3,
    "OMELETTE": 4,
    "BOILED EGG/EGG KURMA": 5,
    "MUTTON CURRY": 6,
    "BABY CORN MANCHURIAN": 7,
    "PANEER": 8,
}

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

# Hardcoded meal times per dish (confirmed accurate; portal radio detection unreliable)
MEAL_TIME_MAP = {
    "CHILLI GOBI":                   "Lunch",
    "CHICKEN PALLIPALAYAM/CHETINAD": "Lunch",
    "MUSHROOM PALLIPALAYAM":         "Dinner",
    "OMELETTE":                      "Breakfast",
    "BOILED EGG/EGG KURMA":         "Lunch",
    "MUTTON CURRY":                  "Dinner",
    "BABY CORN MANCHURIAN":          "Dinner",
    "PANEER":                        "Dinner",
}

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

def get_ptoken_from_js(js_text, button_id):
    """
    Scan the portal's JS bundle for the PTOKEN_ID associated with a button.
    The bundle contains click-handler code like:
      $('#Gobichilli').click(function(){ ... 'PTOKEN_ID': 8 ... })
    Two patterns are tried to cover different formatting styles.
    Returns the integer PTOKEN_ID, or None if not found.
    """
    # Pattern 1: string key  'Gobichilli' ... PTOKEN_ID=8  or  PTOKEN_ID: 8
    p1 = rf"'{re.escape(button_id)}'[^}}]{{0,600}}PTOKEN_ID[\s=:]+([0-9]+)"
    m = re.search(p1, js_text, re.DOTALL)
    if m:
        return int(m.group(1))
    # Pattern 2: unquoted   Gobichilli ... PTOKEN_ID=8
    p2 = rf"{re.escape(button_id)}[^}}]{{0,600}}PTOKEN_ID[\s=:]+([0-9]+)"
    m = re.search(p2, js_text, re.DOTALL)
    if m:
        return int(m.group(1))
    return None

def scrape_dishes(s):
    # Fetch the student dashboard page
    resp = s.get(f"{BASE_URL}/Student/StudentView", headers=PORTAL_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Fetch the JS bundle to extract dynamic PTOKEN_IDs
    js_text = ""
    try:
        js_resp = s.get(
            f"{BASE_URL}/assets/js/scripts.bundle.js",
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=15,
        )
        if js_resp.status_code == 200:
            js_text = js_resp.text
            print(f"[SCRAPE] JS bundle fetched ({len(js_text)} chars)")
        else:
            print(f"[SCRAPE] JS bundle fetch failed: HTTP {js_resp.status_code}")
    except Exception as e:
        print(f"[SCRAPE] JS bundle fetch error: {e}")

    dishes = []
    for btn_id, dish_name in BUTTON_TO_DISH.items():
        # ── Dynamic PTOKEN_ID from JS ─────────────────────────────────────────
        ptoken = get_ptoken_from_js(js_text, btn_id) if js_text else None
        if ptoken:
            token_id = ptoken
            print(f"[SCRAPE] {dish_name}: PTOKEN_ID={token_id} (from JS)")
        else:
            token_id = PTOKEN_MAP.get(dish_name, 0)
            print(f"[SCRAPE] {dish_name}: PTOKEN_ID={token_id} (fallback hardcoded)")

        btn = soup.find(id=btn_id)
        if btn is None:
            dishes.append({"name": dish_name, "token_id": token_id, "dates": [], "meal": MEAL_TIME_MAP.get(dish_name, "Lunch")})
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
            dishes.append({"name": dish_name, "token_id": token_id, "dates": [], "meal": MEAL_TIME_MAP.get(dish_name, "Lunch")})
            continue
        dates = []
        if select_el:
            for option in select_el.find_all("option")[1:]:
                val = option.get("value", "").strip()
                if val:
                    dates.append(val)

        meal = MEAL_TIME_MAP.get(dish_name, "Lunch")
        print(f"[SCRAPE] {dish_name} → token_id={token_id}, meal={meal}, dates={dates}")
        dishes.append({"name": dish_name, "token_id": token_id, "dates": dates, "meal": meal})
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
            session["password"]       = password  # stored for Playwright re-auth
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
    print(f"[PREVIEW] All keys: {list(request.form.keys())}")

    bookings = []
    idx = 0
    while True:
        name_key = f"dish_name_{idx}"
        if name_key not in request.form:
            break
        name       = request.form.get(name_key, "")
        dates_json = request.form.get(f"dish_dates_{idx}", "[]")
        qty        = int(request.form.get(f"dish_qty_{idx}", 0))
        meal       = request.form.get(f"dish_meal_{idx}", "Lunch")  # scraped default

        # qty == 0 means the user wants to skip this dish
        if qty > 0:
            try:
                dates = json.loads(dates_json)
            except Exception:
                dates = []
            for date in dates:
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


@app.route("/book", methods=["POST"])
def book():
    if not session.get("portal_cookies"):
        return Response('data: {"error":"not authenticated"}\n\n', mimetype="text/event-stream")
    bookings     = request.get_json(force=True, silent=True) or []
    rollno       = session.get("rollno", "")
    password     = session.get("password", "")

    def generate():
        # Reverse map: dish name → button DOM id
        DISH_TO_BUTTON = {v: k for k, v in BUTTON_TO_DISH.items()}
        success_count = 0
        failed_count  = 0

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            yield 'data: {"error":"Playwright not installed. Run: pip install playwright && playwright install chromium"}\n\n'
            return

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--ignore-certificate-errors",
                        "--disable-web-security",
                        "--no-proxy-server",
                        "--disable-extensions",
                    ]
                )
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()

                # ── Step 1: Log in via the real browser ───────────────────────
                print("[PLAYWRIGHT] Logging in…")
                page.goto(
                    "https://hostelviewx.psgitech.ac.in/Hostel/Home/Index",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                # Dump all inputs for diagnosis
                inputs = page.eval_on_selector_all(
                    "input", "els => els.map(e => ({name:e.name,id:e.id,type:e.type}))"
                )
                print(f"[PLAYWRIGHT] Login page inputs: {inputs}")
                # Use type-based selectors — more robust than name-based
                page.locator('input[type="text"]').first.fill(rollno)
                page.locator('input[type="password"]').first.fill(password)
                # Tick the terms checkbox if present
                terms = page.locator('input[type="checkbox"]')
                if terms.count() > 0:
                    terms.first.check()
                page.locator('button[type="submit"]').click()
                # The portal login is XHR-based: JS POSTs creds, stores JWT in
                # localStorage, then navigates. wait_for_load_state returns too
                # early (before the JS navigation). Wait for URL to change instead.
                page.wait_for_url(
                    lambda url: "home/index" not in url.lower() and "login" not in url.lower(),
                    timeout=20_000,
                )
                current_url = page.url
                print(f"[PLAYWRIGHT] After login URL: {current_url}")
                print("[PLAYWRIGHT] Login succeeded.")

                # Always navigate explicitly to StudentView before booking
                student_view_url = f"{BASE_URL}/Student/StudentView"
                print(f"[PLAYWRIGHT] Navigating to StudentView: {student_view_url}")
                page.goto(student_view_url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(3000)  # extra wait for JS to initialize
                print(f"[PLAYWRIGHT] StudentView loaded — URL: {page.url}")

                # StudentView opens on Profile/Overview tab — click "Token Booking" tab
                print("[PLAYWRIGHT] Clicking Token Booking tab...")
                token_tab = page.locator("a:has-text('Token Booking'), li:has-text('Token Booking')")
                token_tab.first.click()
                page.wait_for_timeout(2000)  # wait for Token Booking tab JS to initialize
                print("[PLAYWRIGHT] Token Booking tab active.")

                # Auto-accept any confirm/alert dialogs the portal shows after clicking Buy
                def handle_dialog(dialog):
                    print(f"[PLAYWRIGHT] Dialog: type={dialog.type} msg={dialog.message[:100]}")
                    dialog.accept()
                page.on("dialog", handle_dialog)

                for i, b in enumerate(bookings):
                    dish_name = b.get("name", "")
                    btn_id    = DISH_TO_BUTTON.get(dish_name)
                    date      = b.get("date", "")
                    meal      = b.get("meal", "Lunch")   # from preview via MEAL_TIME_MAP
                    qty       = b.get("qty", 1)
                    ok        = False
                    msg       = ""

                    try:
                        if not btn_id:
                            raise ValueError(f"No button mapping for: {dish_name}")

                        print(f"[PLAYWRIGHT] Booking {dish_name} | date={date} meal={meal} qty={qty}")

                        # ── One-time diagnostic: screenshot + HTML dump (first booking only)
                        if i == 0:
                            page.screenshot(path="d:/auto-done/hostel_booker/studentview_debug.png")
                            btn_exists = page.locator(f"#{btn_id}").count()
                            print(f"[PLAYWRIGHT] Button #{btn_id} found on page: {btn_exists}")
                            if btn_exists:
                                html_ctx = page.eval_on_selector(
                                    f"#{btn_id}",
                                    """el => {
                                        let n = el;
                                        for(let i=0;i<5;i++){n=n.parentElement;if(!n)break;}
                                        return n ? n.outerHTML.substring(0,1500) : 'no parent';
                                    }"""
                                )
                                print(f"[PLAYWRIGHT] HTML context around button:\n{html_ctx}")

                        # Derive the quantity input id (btn_id without 'token' suffix, + 'qty' or similar).
                        # We pass btn_id as the "Buy" button id; the evaluate block locates the card
                        # via DOM traversal, sets date/meal/qty, then clicks the Add button.
                        result = page.evaluate("""
                            ([btnId, date, meal, qty]) => {
                                const buyBtn = document.getElementById(btnId);
                                if (!buyBtn) return {ok: false, msg: 'Buy button not found: ' + btnId};

                                // Walk up the DOM to find the card container that has a <select>
                                let card = buyBtn;
                                for (let i = 0; i < 10; i++) {
                                    card = card.parentElement;
                                    if (!card) return {ok: false, msg: 'Card container not found'};
                                    if (card.querySelector('select')) break;
                                }

                                // Set date in the <select> dropdown
                                const sel = card.querySelector('select');
                                if (!sel) return {ok: false, msg: 'No <select> found in card'};
                                sel.value = date;
                                sel.dispatchEvent(new Event('change', {bubbles: true}));

                                // Set meal radio
                                const radios = card.querySelectorAll('input[type=radio]');
                                radios.forEach(r => {
                                    if (r.value && r.value.toLowerCase() === meal.toLowerCase()) {
                                        r.checked = true;
                                        r.click();
                                    }
                                });

                                // Set quantity on any number input in the card
                                const qtyInput = card.querySelector('input[type=number], input[type=text][id*=qty], input[type=text][id*=Qty]');
                                if (qtyInput) {
                                    qtyInput.value = qty;
                                    qtyInput.dispatchEvent(new Event('input', {bubbles: true}));
                                    qtyInput.dispatchEvent(new Event('change', {bubbles: true}));
                                }

                                // Click the Add button (blue primary button, NOT the Buy button)
                                const allBtns = card.querySelectorAll('button');
                                let addClicked = false;
                                allBtns.forEach(b => {
                                    if (!addClicked && b.innerText.trim().toLowerCase() === 'add') {
                                        b.click();
                                        addClicked = true;
                                    }
                                });

                                return {ok: true, addClicked: addClicked};
                            }
                        """, [btn_id, date, meal, qty])

                        print(f"[PLAYWRIGHT] evaluate result: {result}")
                        if not result.get("ok"):
                            raise RuntimeError(result.get("msg", "evaluate failed"))
                        if not result.get("addClicked"):
                            print(f"[PLAYWRIGHT] WARNING: Add button was not found/clicked for {dish_name}")

                        # Wait for the portal to register the Add click
                        page.wait_for_timeout(1500)

                        # Click the Buy button using its stable DOM id
                        page.locator(f"#{btn_id}").click()
                        page.wait_for_timeout(2000)

                        # Screenshot after first booking to see portal response
                        if i == 0:
                            page.screenshot(path="d:/auto-done/hostel_booker/after_buy_debug.png")
                            print("[PLAYWRIGHT] Screenshot saved: after_buy_debug.png")

                        ok  = True
                        msg = "Booked successfully"
                        print(f"[PLAYWRIGHT] OK {dish_name} {date}")

                    except Exception as e:
                        msg = str(e)[:200]
                        print(f"[PLAYWRIGHT] ✗ {dish_name} {date}: {msg}")

                    if ok:
                        success_count += 1
                    else:
                        failed_count += 1

                    event = json.dumps({
                        "index": i, "dish": dish_name, "date": date,
                        "meal": meal, "qty": qty, "ok": ok, "msg": msg,
                    })
                    yield f"data: {event}\n\n"

                browser.close()

        except Exception as outer_e:
            yield f"data: {json.dumps({'error': str(outer_e)[:300]})}\n\n"

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
