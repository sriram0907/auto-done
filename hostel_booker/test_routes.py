"""Quick smoke-test for all routes without hitting the real portal."""
import sys
import json
import re
sys.path.insert(0, '.')
import app

PASS = []
FAIL = []

def check(name, cond, note=''):
    if cond:
        PASS.append(name)
        print(f'  PASS  {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL  {name}' + (f' -- {note}' if note else ''))

# ── Helper ────────────────────────────────────────────────────────────────────
def make_client():
    return app.app.test_client()

# ── 1. GET / → login page ─────────────────────────────────────────────────────
print('\n[1] Login page')
with make_client() as c:
    r = c.get('/')
    body = r.data.decode()
    check('Status 200', r.status_code == 200)
    check('Title present', 'Hostel Token Booker' in body)
    check('Roll number shown', '715525104209' in body)
    check('Password field present', 'type="password"' in body)
    check('Sign In button present', 'Sign In' in body)

# ── 2. POST /login with empty password ───────────────────────────────────────
print('\n[2] Login — empty password')
with make_client() as c:
    r = c.post('/login', data={'password': ''})
    body = r.data.decode()
    check('Returns 200', r.status_code == 200)
    check('Error shown', 'Password is required' in body)

# ── 3. GET /dashboard without session → redirect ─────────────────────────────
print('\n[3] Dashboard without session')
with make_client() as c:
    r = c.get('/dashboard')
    check('Redirects to /', r.status_code == 302)
    check('Location is /', r.headers.get('Location', '').endswith('/'))

# ── 4. Dashboard with mock scraped dishes ────────────────────────────────────
print('\n[4] Dashboard with mock dishes')

MOCK_DISHES = [
    {'name': 'CHILLI GOBI',                'token_id': 1, 'dates': ['07-04-2026','10-04-2026'], 'meal': 'Lunch'},
    {'name': 'CHICKEN PALLIPALAYAM/CHETINAD', 'token_id': 2, 'dates': ['08-04-2026'],           'meal': 'Lunch'},
    {'name': 'MUSHROOM PALLIPALAYAM',      'token_id': 3, 'dates': [],                           'meal': 'Lunch'},
    {'name': 'OMELETTE',                   'token_id': 4, 'dates': ['09-04-2026'],               'meal': 'Breakfast'},
    {'name': 'BOILED EGG/EGG KURMA',      'token_id': 5, 'dates': ['07-04-2026','13-04-2026'], 'meal': 'Dinner'},
    {'name': 'MUTTON CURRY',               'token_id': 6, 'dates': [],                           'meal': 'Lunch'},
    {'name': 'BABY CORN MANCHURIAN',       'token_id': 7, 'dates': ['11-04-2026'],               'meal': 'Lunch'},
    {'name': 'PANEER',                     'token_id': 8, 'dates': ['07-04-2026','08-04-2026'], 'meal': 'Lunch'},
]

original_scrape = app.scrape_dishes
app.scrape_dishes = lambda s: MOCK_DISHES

with make_client() as c:
    with c.session_transaction() as sess:
        sess['portal_cookies'] = {'fake': 'cookie'}
    r = c.get('/dashboard')
    body = r.data.decode()
    check('Status 200', r.status_code == 200)
    check('All dishes rendered', all(d['name'] in body for d in MOCK_DISHES))
    check('dish_name hidden inputs', 'dish_name_0' in body and 'dish_name_7' in body)
    check('Disabled row for no-date dish', 'disabled' in body)
    check('Date chips present', '07-04-2026' in body)
    check('Select All button', 'selectAllBtn' in body)
    check('Preview button', 'Preview Bookings' in body)
    # Meal selects — Breakfast default for OMELETTE
    check('Breakfast pre-selected', 'selected>🌅 Breakfast' in body or 'selected' in body)

app.scrape_dishes = original_scrape

# ── 5. POST /preview → booking table ─────────────────────────────────────────
print('\n[5] Preview route')
with make_client() as c:
    with c.session_transaction() as sess:
        sess['portal_cookies'] = {'fake': 'cookie'}

    form = {
        # CHILLI GOBI — selected, 2 dates → 2 booking rows
        'dish_name_0': 'CHILLI GOBI',
        'dish_selected_0': 'on',
        'dish_dates_0': json.dumps(['07-04-2026', '10-04-2026']),
        'dish_qty_0': '1',
        'dish_meal_0': 'Lunch',
        # PANEER — selected, 2 dates, qty 2 → 2 booking rows
        'dish_name_1': 'PANEER',
        'dish_selected_1': 'on',
        'dish_dates_1': json.dumps(['08-04-2026', '09-04-2026']),
        'dish_qty_1': '2',
        'dish_meal_1': 'Dinner',
        # MUSHROOM — NOT selected
        'dish_name_2': 'MUSHROOM PALLIPALAYAM',
        'dish_dates_2': json.dumps([]),
        'dish_qty_2': '1',
        'dish_meal_2': 'Lunch',
    }
    r = c.post('/preview', data=form)
    body = r.data.decode()
    check('Status 200', r.status_code == 200)
    check('CHILLI GOBI in preview', 'CHILLI GOBI' in body)
    check('PANEER in preview', 'PANEER' in body)
    check('MUSHROOM excluded (not selected)', 'MUSHROOM' not in body)
    check('07-04-2026 date present', '07-04-2026' in body)

    # Check BOOKINGS_DATA JS injection
    m = re.search(r'BOOKINGS_DATA = (\[.*?\]);', body, re.DOTALL)
    if m:
        data = json.loads(m.group(1))
        check('4 bookings in JS data', len(data) == 4, f'got {len(data)}')
        check('Booking token_ids correct', all(b['token_id'] > 0 for b in data))
        check('PANEER qty=2', any(b['name'] == 'PANEER' and b['qty'] == 2 for b in data))
        check('Confirm button present', 'Confirm' in body)
    else:
        check('BOOKINGS_DATA JS present', False, 'not found in body')

# ── 6. GET /logout → clears session ──────────────────────────────────────────
print('\n[6] Logout')
with make_client() as c:
    with c.session_transaction() as sess:
        sess['portal_cookies'] = {'fake': 'cookie'}
    r = c.get('/logout')
    check('Redirects', r.status_code == 302)
    # After logout, /dashboard should redirect to /
    r2 = c.get('/dashboard')
    check('Dashboard blocked after logout', r2.status_code == 302)

# ── Summary ───────────────────────────────────────────────────────────────────
print('\n' + '-'*50)
print(f'Results: {len(PASS)} passed, {len(FAIL)} failed')
if FAIL:
    print('Failed checks:', FAIL)
    sys.exit(1)
else:
    print('All checks passed!')
