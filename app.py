import os
import datetime
import pandas as pd
import numpy as np
import json
from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify, abort
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# --- 1. SETUP & CONFIGURATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'my_super_secure_multi_user_secret_key'
DATA_FILE = 'finance_app_data.pkl'

# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "กรุณาเข้าสู่ระบบเพื่อใช้งาน"
login_manager.login_message_category = "info"

# --- 2. USER & DATA HANDLING ---
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password = password_hash
    @staticmethod
    def get(user_id):
        users = load_all_data().get('users', {})
        user_data = users.get(str(user_id))
        if user_data:
            return User(id=str(user_id), username=user_data['username'], password_hash=user_data['password_hash'])
        return None

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

def load_all_data():
    if os.path.exists(DATA_FILE):
        try: return pd.read_pickle(DATA_FILE)
        except Exception: return {'users': {}, 'user_data': {}}
    return {'users': {}, 'user_data': {}}

def load_current_user_data():
    all_data = load_all_data()
    return all_data.get('user_data', {}).get(current_user.id, {
        'transactions': [], 'categories': {'income': ['เงินเดือน', 'รายได้เสริม'], 'expense': ['อาหาร', 'เดินทาง', 'ชำระหนี้']}, 'debts': []
    })

def save_current_user_data(user_data):
    all_data = load_all_data()
    all_data.setdefault('user_data', {})[current_user.id] = user_data
    pd.to_pickle(all_data, DATA_FILE)

def migrate_old_data_to_user(user_id):
    OLD_FILE = 'finance_data.pkl'
    if os.path.exists(OLD_FILE):
        try:
            print(f"Migrating old data for user ID: {user_id}...")
            old_data = pd.read_pickle(OLD_FILE)
            all_data = load_all_data()
            all_data.setdefault('user_data', {})[user_id] = old_data
            pd.to_pickle(all_data, DATA_FILE)
            os.rename(OLD_FILE, OLD_FILE + '.migrated')
            print("Migration successful!")
            return True
        except Exception as e:
            print(f"Error migrating old data: {e}")
    return False

def calculate_debt_payoff_logic(debt, extra_payment=0):
    balance = debt.get('current_balance', 0)
    if not balance > 0: return "หนี้ชำระหมดแล้ว", "N/A"
    monthly_rate = (debt.get('rate_percent', 0) / 100) / 12 if debt.get('rate_type') == 'yearly' else debt.get('rate_percent', 0) / 100
    total_payment = debt.get('min_payment', 0) + extra_payment
    if total_payment <= 0: return "ยอดชำระต้องมากกว่า 0", None
    if total_payment <= balance * monthly_rate: return "ไม่มีวันหมด", None
    try:
        months = -np.log(1 - (balance * monthly_rate) / total_payment) / np.log(1 + monthly_rate)
        payoff_date = datetime.date.today() + datetime.timedelta(days=months * 30.44)
        return f"{months:.1f} เดือน", payoff_date.strftime('%d-%m-%Y')
    except (ValueError, ZeroDivisionError): return "คำนวณไม่ได้", None

# --- 3. AUTHENTICATION ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        users = load_all_data().get('users', {})
        user_id = next((uid for uid, udata in users.items() if udata['username'] == username), None)
        if user_id:
            user = User.get(user_id)
            if user and check_password_hash(user.password, password):
                login_user(user, remember=True)
                return redirect(url_for('index'))
        flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง', 'danger')
    return render_template_string(AUTH_TEMPLATE, form_type='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username, password = request.form['username'], request.form['password']
        all_data = load_all_data()
        users = all_data.setdefault('users', {})
        if any(u['username'] == username for u in users.values()):
            flash('ชื่อผู้ใช้นี้มีคนใช้แล้ว', 'warning'); return redirect(url_for('register'))
        new_id = str(len(users) + 1)
        users[new_id] = {'username': username, 'password_hash': generate_password_hash(password, method='pbkdf2:sha256')}
        pd.to_pickle(all_data, DATA_FILE)
        if new_id == '1': migrate_old_data_to_user(new_id)
        flash('สมัครสมาชิกสำเร็จ! กรุณาเข้าสู่ระบบ', 'success')
        return redirect(url_for('login'))
    return render_template_string(AUTH_TEMPLATE, form_type='register')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- 4. MAIN APPLICATION ROUTES ---
@app.route('/')
@login_required
def index():
    data = load_current_user_data()
    today = datetime.date.today()
    year, month = request.args.get('year', today.year, int), request.args.get('month', today.month, int)
    df = pd.DataFrame(data.get('transactions', []))
    summary = {'total_income': 0, 'total_expense': 0, 'net_balance': 0, 'expense_by_category_json': json.dumps({})}
    if not df.empty and 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'], errors='coerce'); df.dropna(subset=['date'], inplace=True)
        monthly_df = df[(df['date'].dt.year == year) & (df['date'].dt.month == month)]
        summary['total_income'] = monthly_df[monthly_df['type'] == 'income']['amount'].sum()
        summary['total_expense'] = monthly_df[monthly_df['type'] == 'expense']['amount'].sum()
        summary['net_balance'] = summary['total_income'] - summary['total_expense']
        expense_df = monthly_df[monthly_df['type'] == 'expense']
        if not expense_df.empty:
            expense_cat = expense_df.groupby('category')['amount'].sum()
            summary['expense_by_category_json'] = json.dumps({'labels': list(expense_cat.index), 'data': list(expense_cat.values)})
    all_years = list(range(today.year - 5, today.year + 2))
    return render_template_string(HTML_TEMPLATE, data=data, summary=summary, current_year=year, current_month=month, all_years=all_years, today=today)

@app.route('/debt/<int:debt_id>')
@login_required
def debt_detail(debt_id):
    data = load_current_user_data()
    debt = next((d for d in data.get('debts', []) if d.get('id') == debt_id), None)
    if not debt: abort(404)
    payment_history = sorted([tx for tx in data.get('transactions', []) if tx.get('category') == 'ชำระหนี้' and (debt['name'] in tx.get('debt_paid', '') or debt['name'] in tx.get('description', ''))], key=lambda x: x.get('date', ''), reverse=True)
    total_paid = sum(tx.get('amount', 0) for tx in payment_history)
    return render_template_string(DEBT_DETAIL_TEMPLATE, debt=debt, payment_history=payment_history, total_paid=total_paid, today=datetime.date.today(), data=data)

@app.route('/add_transaction', methods=['POST'])
@login_required
def add_transaction():
    user_data = load_current_user_data()
    try:
        amount = float(request.form['amount'])
        debt_paid_name = request.form.get('debt_paid')
        description = request.form['description']
        if debt_paid_name and request.form['category'] == 'ชำระหนี้': description += f" (สำหรับ: {debt_paid_name})"
        new_transaction = {'date': request.form['date'], 'description': description, 'type': request.form['type'], 'category': request.form['category'], 'amount': amount, 'debt_paid': debt_paid_name}
        user_data.setdefault('transactions', []).insert(0, new_transaction)
        if request.form['type'] == 'expense' and request.form['category'] == 'ชำระหนี้' and debt_paid_name:
            for debt in user_data.get('debts', []):
                if debt.get('name') == debt_paid_name:
                    debt['current_balance'] = float(debt.get('current_balance', 0)) - amount
                    flash(f"ยอดหนี้ '{debt_paid_name}' อัปเดตแล้ว!", "info"); break
        save_current_user_data(user_data)
        flash("เพิ่มรายการสำเร็จ!", "success")
    except (ValueError, KeyError) as e: flash(f"ข้อมูลไม่ถูกต้อง: {e}", "danger")
    return redirect(request.referrer or url_for('index'))

@app.route('/add_debt', methods=['POST'])
@login_required
def add_debt():
    user_data = load_current_user_data()
    try:
        balance = float(request.form['balance'])
        debts = user_data.setdefault('debts', [])
        max_id = max([d.get('id', 0) for d in debts] + [0])
        new_debt = {'id': max_id + 1, 'name': request.form['name'], 'initial_balance': balance, 'current_balance': balance, 'rate_percent': float(request.form['rate_percent']), 'rate_type': request.form['rate_type'], 'min_payment': float(request.form['min_payment']), 'due_day': int(request.form['due_day'])}
        debts.append(new_debt)
        save_current_user_data(user_data)
        flash(f"เพิ่มหนี้ '{new_debt['name']}' สำเร็จ", "success")
    except (ValueError, KeyError) as e: flash(f"ข้อมูลหนี้สินไม่ถูกต้อง: {e}", "danger")
    return redirect(url_for('index'))

@app.route('/edit_debt/<int:debt_id>', methods=['POST'])
@login_required
def edit_debt(debt_id):
    user_data = load_current_user_data()
    debt_to_edit = next((d for d in user_data.get('debts', []) if d.get('id') == debt_id), None)
    if not debt_to_edit: flash("ไม่พบข้อมูลหนี้", "danger"); return redirect(url_for('index'))
    try:
        debt_to_edit.update({'name': request.form['name'], 'initial_balance': float(request.form['initial_balance']), 'current_balance': float(request.form['current_balance']),'rate_percent': float(request.form['rate_percent']), 'rate_type': request.form['rate_type'],'min_payment': float(request.form['min_payment']), 'due_day': int(request.form['due_day'])})
        save_current_user_data(user_data)
        flash(f"แก้ไขข้อมูลหนี้ '{debt_to_edit['name']}' สำเร็จ", "success")
    except (ValueError, KeyError) as e: flash(f"ข้อมูลที่แก้ไขไม่ถูกต้อง: {e}", "danger")
    return redirect(request.referrer or url_for('index'))

@app.route('/delete_transaction/<int:index>', methods=['POST'])
@login_required
def delete_transaction(index):
    user_data = load_current_user_data()
    try:
        transactions = user_data.get('transactions', [])
        if index >= len(transactions): raise IndexError
        tx_to_delete = transactions[index]
        if tx_to_delete.get('category') == 'ชำระหนี้' and tx_to_delete.get('debt_paid'):
            for debt in user_data.get('debts', []):
                if debt.get('name') == tx_to_delete.get('debt_paid'):
                    debt['current_balance'] = float(debt.get('current_balance', 0)) + float(tx_to_delete.get('amount', 0))
                    flash(f"คืนยอดเงินให้หนี้ '{debt['name']}' เรียบร้อย", "info"); break
        del transactions[index]
        save_current_user_data(user_data)
        flash("ลบรายการสำเร็จ!", "success")
    except IndexError: flash("ไม่พบรายการที่ต้องการลบ", "danger")
    return redirect(url_for('index'))

@app.route('/add_category', methods=['POST'])
@login_required
def add_category():
    user_data = load_current_user_data()
    try:
        cat_type, new_cat = request.form['type'], request.form['name'].strip()
        if new_cat and new_cat not in user_data.get('categories', {}).get(cat_type, []):
            user_data.setdefault('categories', {}).setdefault(cat_type, []).append(new_cat)
            save_current_user_data(user_data)
            flash(f"เพิ่มหมวดหมู่ '{new_cat}' สำเร็จ", "success")
        else: flash(f"หมวดหมู่ '{new_cat}' อาจมีอยู่แล้ว", "warning")
    except KeyError: flash("ประเภทหมวดหมู่ไม่ถูกต้อง", "danger")
    return redirect(url_for('index'))

@app.route('/calculate_debt', methods=['POST'])
@login_required
def calculate_debt():
    user_data = load_current_user_data()
    try:
        debt_id = int(request.form['debt_id'])
        extra_payment = float(request.form.get('extra_payment', 0))
        debt = next((d for d in user_data.get('debts', []) if d.get('id') == debt_id), None)
        if not debt: return jsonify({'error': 'ไม่พบหนี้สิน'}), 404
        duration, payoff_date = calculate_debt_payoff_logic(debt, extra_payment)
        return jsonify({'debt_name': debt['name'], 'duration': duration, 'payoff_date': payoff_date})
    except (ValueError, KeyError, IndexError): return jsonify({'error': 'ข้อมูลไม่ถูกต้อง'}), 400

# --- 5. HTML TEMPLATES (FULL VERSION) ---
AUTH_TEMPLATE = """
<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"><title>{{'เข้าสู่ระบบ' if form_type=='login' else 'สมัครสมาชิก'}}</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{display:flex;align-items:center;padding-top:40px;padding-bottom:40px;background-color:#f5f5f5;height:100vh}.form-signin{width:100%;max-width:330px;padding:15px;margin:auto}</style></head><body class="text-center"><main class="form-signin"><form method="POST"><h1 class="h3 mb-3 fw-normal">{{'กรุณาเข้าสู่ระบบ' if form_type=='login' else 'สร้างบัญชีใหม่'}}</h1>{% with messages=get_flashed_messages(with_categories=true)%}{% if messages%}{% for category,message in messages%}<div class="alert alert-{{category}}">{{message}}</div>{% endfor%}{% endif%}{% endwith %}<div class="form-floating"><input type="text" name="username" class="form-control" id="floatingInput" placeholder="Username" required><label for="floatingInput">ชื่อผู้ใช้</label></div><div class="form-floating"><input type="password" name="password" class="form-control" id="floatingPassword" placeholder="Password" required><label for="floatingPassword">รหัสผ่าน</label></div><button class="w-100 btn btn-lg btn-primary mt-3" type="submit">{{'เข้าสู่ระบบ' if form_type=='login' else 'สมัครสมาชิก'}}</button><p class="mt-3">{% if form_type=='login'%}ยังไม่มีบัญชี? <a href="{{url_for('register')}}">สมัครสมาชิก</a>{% else %}มีบัญชีอยู่แล้ว? <a href="{{url_for('login')}}">เข้าสู่ระบบ</a>{% endif %}</p></form></main></body></html>
"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="th" data-bs-theme="light">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Finance Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;700&display=swap" rel="stylesheet">
    <style>body{font-family:'Sarabun',sans-serif;background-color:#f0f2f5}.card{border:none;border-radius:.8rem;box-shadow:0 4px 12px rgba(0,0,0,.08);overflow:hidden}.debt-card{transition:transform .2s ease-in-out}.debt-card:hover{transform:translateY(-5px);box-shadow:0 8px 20px rgba(0,0,0,.12)}.table-responsive{max-height:65vh}.net-positive{color:#198754!important}.net-negative{color:#dc3545!important}.sticky-top{top:1rem}a{text-decoration:none}</style>
</head>
<body>
    <div class="container-fluid p-4">
        <header class="d-flex justify-content-between align-items-center mb-4">
            <h1 class="h3 mb-0"><i class="bi bi-wallet2 me-2"></i>Finance Dashboard <small class="text-muted h6">({{ current_user.username }})</small></h1>
            <div>
                <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addTransactionModal"><i class="bi bi-plus-circle me-1"></i> เพิ่มรายการ</button>
                <button class="btn btn-warning text-dark" data-bs-toggle="modal" data-bs-target="#addDebtModal"><i class="bi bi-credit-card me-1"></i> เพิ่มหนี้สิน</button>
                <a href="{{ url_for('logout') }}" class="btn btn-outline-secondary"><i class="bi bi-box-arrow-right me-1"></i> ออกจากระบบ</a>
            </div>
        </header>
        {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">{{ message }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endif %}{% endwith %}
        <div class="row g-4">
            <div class="col-lg-4"><div class="sticky-top"><div class="card mb-4"><div class="card-body"><h5 class="card-title mb-3"><i class="bi bi-bar-chart-line me-2"></i>สรุปภาพรวม</h5><form method="GET" action="{{url_for('index')}}" class="d-flex gap-2 mb-3"><select name="month" class="form-select form-select-sm">{% for i in range(1,13) %}<option value="{{i}}" {% if i==current_month %}selected{% endif %}>เดือน {{i}}</option>{% endfor %}</select><select name="year" class="form-select form-select-sm">{% for y in all_years %}<option value="{{y}}" {% if y==current_year %}selected{% endif %}>{{y}}</option>{% endfor %}</select><button type="submit" class="btn btn-sm btn-outline-primary"><i class="bi bi-search"></i></button></form><div class="d-flex justify-content-around text-center"><div><small class="text-muted">รายรับ</small><p class="h5 net-positive mb-0">{{ "%.2f"|format(summary.total_income) }}</p></div><div><small class="text-muted">รายจ่าย</small><p class="h5 net-negative mb-0">{{ "%.2f"|format(summary.total_expense) }}</p></div><div><small class="text-muted">คงเหลือ</small><p class="h5 {{'net-positive' if summary.net_balance >=0 else 'net-negative'}} mb-0">{{ "%.2f"|format(summary.net_balance) }}</p></div></div></div></div><div class="card"><div class="card-body"><h5 class="card-title mb-3"><i class="bi bi-pie-chart me-2"></i>สัดส่วนรายจ่าย</h5><div style="height:300px"><canvas id="expenseChart"></canvas></div></div></div></div></div>
            <div class="col-lg-8"><h4 class="mb-3"><i class="bi bi-journal-text me-2"></i>ติดตามหนี้สิน</h4><div class="row g-4">{% if data.debts %}{% for debt in data.debts %}<div class="col-md-6"><div class="card debt-card h-100"><div class="card-body position-relative"><div class="d-flex justify-content-between align-items-start mb-2"><a href="{{url_for('debt_detail',debt_id=debt.id)}}" class="text-dark stretched-link"><h5 class="card-title mb-1">{{debt.name}}</h5></a><button class="btn btn-sm btn-outline-secondary border-0" data-bs-toggle="modal" data-bs-target="#editDebtModal-{{debt.id}}" style="z-index:5" onclick="event.stopPropagation();"><i class="bi bi-pencil-square"></i></button></div><p class="h3 mb-1">฿{{ "%.2f"|format(debt.current_balance) }}</p><small class="text-muted">จาก {{ "%.2f"|format(debt.initial_balance) }}</small><div class="progress mt-2 mb-3" role="progressbar" style="height:5px"><div class="progress-bar bg-success" style="width:{{(100-(debt.current_balance/debt.initial_balance*100)) if debt.initial_balance>0 else 0}}%"></div></div><p class="small text-muted mb-0"><i class="bi bi-calendar-check me-1"></i>ครบกำหนดวันที่ {{debt.due_day}}{% set days_left=debt.due_day-today.day %}{% if 0<=days_left<=5 %}<span class="badge bg-danger-subtle text-danger-emphasis rounded-pill ms-2">อีก {{days_left}} วัน</span>{% endif %}</p></div></div></div>{% endfor %}{% else %}<p class="text-center text-muted">ยังไม่มีข้อมูลหนี้สิน</p>{% endif %}</div>
                <div class="card mt-4"><div class="card-header bg-white"><h5 class="mb-0"><i class="bi bi-list-ul me-2"></i>ประวัติรายการล่าสุด (15)</h5></div><div class="table-responsive"><table class="table table-hover mb-0 align-middle"><tbody>{% for tx in data.transactions %}{% if loop.index<=15 %}<tr><td class="ps-3"><i class="bi h5 mb-0 {{'bi-arrow-down-circle-fill text-success' if tx.type=='income' else 'bi-arrow-up-circle-fill text-danger'}}"></i></td><td>{{tx.date}}</td><td><strong>{{tx.description}}</strong><br><small class="text-muted">{{tx.category}}</small></td><td class="text-end fw-bold {{'net-positive' if tx.type=='income' else 'net-negative'}}">{{('+' if tx.type=='income' else '-')~"%.2f"|format(tx.amount)}}</td><td class="text-end pe-3"><form action="{{url_for('delete_transaction',index=loop.index0)}}" method="POST" onsubmit="return confirm('แน่ใจหรือไม่?')"><button type="submit" class="btn btn-sm border-0"><i class="bi bi-x-lg text-muted"></i></button></form></td></tr>{% endif %}{% endfor %}</tbody></table></div></div>
            </div>
        </div>
    </div>
    <div class="modal fade" id="addTransactionModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content"><form action="{{url_for('add_transaction')}}" method="POST"><div class="modal-header"><h5 class="modal-title">เพิ่มรายการ</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="row"><div class="col-md-6"><div class="mb-3"><input type="date" name="date" class="form-control" value="{{today.strftime('%Y-%m-%d')}}" required></div><div class="mb-3"><input type="text" name="description" class="form-control" placeholder="รายการ" required></div><div class="mb-3"><select name="type" class="form-select" id="transactionType"><option value="expense">รายจ่าย</option><option value="income">รายรับ</option></select></div><div class="mb-3"><label class="form-label">หมวดหมู่</label><select name="category" id="category-select" class="form-select" required></select></div><div class="mb-3" id="debt-payment-field" style="display:none"><label class="form-label">ชำระหนี้สำหรับ</label><select name="debt_paid" class="form-select"><option value="">-- ไม่ระบุ --</option>{% for debt in data.debts %}<option value="{{debt.name}}">{{debt.name}}</option>{% endfor %}</select></div><div class="mb-3"><label class="form-label">จำนวนเงินรวม</label><input type="number" step="0.01" name="amount" id="totalAmount" class="form-control" placeholder="0.00" required></div></div><div class="col-md-6 border-start"><h6><i class="bi bi-receipt"></i> เครื่องคิดเลขรายการย่อย</h6><div id="item-list"></div><button type="button" class="btn btn-sm btn-outline-secondary" id="addItemBtn"><i class="bi bi-plus-lg"></i> เพิ่มรายการย่อย</button></div></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-primary">บันทึก</button></div></form></div></div></div>
    <div class="modal fade" id="addCategoryModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('add_category')}}" method="POST"><div class="modal-header"><h5 class="modal-title">เพิ่มหมวดหมู่</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-3"><label class="form-label">ประเภท</label><select name="type" class="form-select"><option value="expense">รายจ่าย</option><option value="income">รายรับ</option></select></div><div class="mb-3"><label class="form-label">ชื่อหมวดหมู่</label><input type="text" name="name" class="form-control" required></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-primary">เพิ่ม</button></div></form></div></div></div>
    <div class="modal fade" id="addDebtModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('add_debt')}}" method="POST"><div class="modal-header"><h5 class="modal-title">เพิ่มหนี้สิน</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-3"><input type="text" name="name" class="form-control" placeholder="ชื่อหนี้" required></div><div class="mb-3"><input type="number" step="0.01" name="balance" class="form-control" placeholder="ยอดหนี้ทั้งหมด" required></div><div class="row g-2 mb-3"><div class="col-8"><input type="number" step="0.01" name="rate_percent" class="form-control" placeholder="อัตราดอกเบี้ย" required></div><div class="col-4"><select name="rate_type" class="form-select"><option value="yearly">ต่อปี</option><option value="monthly">ต่อเดือน</option></select></div></div><div class="row g-2 mb-3"><div class="col-8"><input type="number" step="0.01" name="min_payment" class="form-control" placeholder="ชำระขั้นต่ำ/เดือน" required></div><div class="col-4"><div class="input-group"><input type="number" name="due_day" class="form-control" placeholder="วันที่" value="1" min="1" max="31" required></div></div></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-warning text-dark">เพิ่มหนี้</button></div></form></div></div></div>
    {% for debt in data.debts %}<div class="modal fade" id="editDebtModal-{{debt.id}}" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('edit_debt',debt_id=debt.id)}}" method="POST"><div class="modal-header"><h5 class="modal-title">แก้ไขหนี้: {{debt.name}}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-3"><label class="form-label">ชื่อหนี้</label><input type="text" name="name" class="form-control" value="{{debt.name}}" required></div><div class="row g-2 mb-3"><div class="col"><label class="form-label">ยอดตั้งต้น</label><input type="number" step="0.01" name="initial_balance" value="{{debt.initial_balance}}" class="form-control" required></div><div class="col"><label class="form-label">ยอดปัจจุบัน</label><input type="number" step="0.01" name="current_balance" value="{{debt.current_balance}}" class="form-control" required></div></div><div class="row g-2 mb-3"><div class="col-8"><label class="form-label">ดอกเบี้ย</label><input type="number" step="0.01" name="rate_percent" value="{{debt.rate_percent}}" class="form-control" required></div><div class="col-4"><label class="form-label"> </label><select name="rate_type" class="form-select"><option value="yearly" {% if debt.rate_type=='yearly'%}selected{% endif %}>ต่อปี</option><option value="monthly" {% if debt.rate_type=='monthly'%}selected{% endif %}>ต่อเดือน</option></select></div></div><div class="row g-2 mb-3"><div class="col-8"><label class="form-label">ขั้นต่ำ/เดือน</label><input type="number" step="0.01" name="min_payment" value="{{debt.min_payment}}" class="form-control" required></div><div class="col-4"><label class="form-label">วันครบกำหนด</label><input type="number" name="due_day" class="form-control" value="{{debt.due_day}}" min="1" max="31" required></div></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ยกเลิก</button><button type="submit" class="btn btn-primary">บันทึก</button></div></form></div></div></div>{% endfor %}
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
    document.addEventListener('DOMContentLoaded',function(){const e={{summary.expense_by_category_json|safe}},t=document.getElementById("expenseChart");t&&e.labels&&e.labels.length>0?new Chart(t,{type:"doughnut",data:{labels:e.labels,datasets:[{data:e.data,backgroundColor:["#ff6384","#36a2eb","#ffce56","#4bc0c0","#9966ff","#ff9f40","#c9cbcf"],borderColor:"#fff",borderWidth:2,hoverOffset:8}]},options:{responsive:!0,maintainAspectRatio:!1,animation:{animateScale:!0,animateRotate:!0},plugins:{legend:{position:"bottom",labels:{usePointStyle:!0,padding:20}},tooltip:{yAlign:"bottom",displayColors:!1,callbacks:{label:function(e){let t=e.label||"",a=e.raw,l=e.chart.getDatasetMeta(0).total,o=(a/l*100).toFixed(2)+"%";return`${t} ${new Intl.NumberFormat("th-TH",{style:"currency",currency:"THB"}).format(a)} (${o})`}}}}}}):t&&(t.getContext("2d").textAlign="center",t.getContext("2d").textBaseline="middle",t.getContext("2d").font="16px Sarabun",t.getContext("2d").fillText("ไม่มีข้อมูลรายจ่ายในเดือนนี้",t.width/2,t.height/2));const a={{data.categories|tojson}},l=document.getElementById("category-select"),o=document.getElementById("transactionType"),n=document.getElementById("debt-payment-field"),d=()=>{n.style.display="expense"===o.value&&"ชำระหนี้"===l.value?"block":"none"},c=e=>{l.innerHTML="",(a[e]||[]).forEach(e=>{const t=document.createElement("option");t.value=e,t.textContent=e,l.appendChild(t)}),d()};o&&o.addEventListener("change",()=>c(o.value)),l&&l.addEventListener("change",d),c(o?o.value:"expense");const i=document.getElementById("item-list"),s=document.getElementById("totalAmount"),r=()=>{let e=0;document.querySelectorAll(".item-price").forEach(t=>{e+=parseFloat(t.value)||0}),s.value=e.toFixed(2)};document.getElementById("addItemBtn").addEventListener("click",()=>{const e=document.createElement("div");e.className="d-flex gap-2 mb-2",e.innerHTML=`<input type="text" class="form-control form-control-sm item-name" placeholder="ชื่อของ"><input type="number" step="0.01" class="form-control form-control-sm item-price" placeholder="ราคา"><button type="button" class="btn btn-sm btn-outline-danger" onclick="this.parentElement.remove(); r();">X</button>`,i.appendChild(e),e.querySelector(".item-price").addEventListener("input",r)})});
    </script>
</body></html>
"""

DEBT_DETAIL_TEMPLATE = """
<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"><title>รายละเอียดหนี้: {{debt.name}}</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"><link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;700&display=swap" rel="stylesheet"><style>body{font-family:'Sarabun',sans-serif;background-color:#f8f9fa}</style></head><body>
<div class="container mt-4">
    <nav aria-label="breadcrumb"><ol class="breadcrumb"><li class="breadcrumb-item"><a href="{{url_for('index')}}">Dashboard</a></li><li class="breadcrumb-item active" aria-current="page">{{debt.name}}</li></ol></nav>
    <div class="row g-4">
        <div class="col-md-4"><div class="card"><div class="card-body text-center"><h5 class="card-title">{{debt.name}}</h5><p class="display-4 text-danger fw-bold">฿{{"%.2f"|format(debt.current_balance)}}</p><p class="text-muted">ยอดคงเหลือ</p><div class="progress mb-3"><div class="progress-bar bg-success" style="width:{{(100-(debt.current_balance/debt.initial_balance*100)) if debt.initial_balance > 0 else 0}}%"></div></div><form class="d-flex gap-2" onsubmit="calculatePayoff(event,{{debt.id}})"><input type="number" step="0.01" class="form-control" placeholder="โปะเพิ่ม/เดือน"><button type="submit" class="btn btn-info flex-shrink-0"><i class="bi bi-calculator"></i> คำนวณ</button></form><small id="debt-result-{{debt.id}}" class="form-text text-muted d-block mt-1"></small></div><ul class="list-group list-group-flush"><li class="list-group-item d-flex justify-content-between"><span>ยอดตั้งต้น:</span><strong>{{"%.2f"|format(debt.initial_balance)}}</strong></li><li class="list-group-item d-flex justify-content-between"><span>ชำระไปแล้ว:</span><strong class="text-success">{{"%.2f"|format(total_paid)}}</strong></li><li class="list-group-item d-flex justify-content-between"><span>ขั้นต่ำ:</span><strong>{{"%.2f"|format(debt.min_payment)}}/เดือน</strong></li><li class="list-group-item d-flex justify-content-between"><span>ครบกำหนด:</span><strong>วันที่ {{debt.due_day}}</strong></li></ul></div></div>
        <div class="col-md-8"><div class="card"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-clock-history me-1"></i> ประวัติการชำระเงิน</span><button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#addTransactionModal"><i class="bi bi-plus-lg"></i> บันทึกการชำระ</button></div><div class="card-body">{% if payment_history %}<table class="table"><thead><tr><th>วันที่</th><th>รายละเอียด</th><th class="text-end">จำนวนเงิน</th></tr></thead><tbody>{% for tx in payment_history %}<tr><td>{{tx.date}}</td><td>{{tx.description}}</td><td class="text-end text-danger">-{{"%.2f"|format(tx.amount)}}</td></tr>{% endfor %}</tbody></table>{% else %}<p class="text-center text-muted p-4">ยังไม่มีประวัติการชำระสำหรับหนี้ก้อนนี้</p>{% endif %}</div></div></div>
    </div>
</div>
<div class="modal fade" id="addTransactionModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('add_transaction')}}" method="POST"><div class="modal-header"><h5 class="modal-title">บันทึกการชำระหนี้: {{debt.name}}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><input type="hidden" name="type" value="expense"><input type="hidden" name="category" value="ชำระหนี้"><input type="hidden" name="debt_paid" value="{{debt.name}}"><div class="mb-3"><label class="form-label">วันที่ชำระ</label><input type="date" name="date" class="form-control" value="{{today.strftime('%Y-%m-%d')}}" required></div><div class="mb-3"><label class="form-label">จำนวนเงินที่ชำระ</label><input type="number" step="0.01" name="amount" class="form-control" placeholder="0.00" required></div><div class="mb-3"><label class="form-label">รายละเอียดเพิ่มเติม (ไม่บังคับ)</label><input type="text" name="description" class="form-control" value="ชำระหนี้ {{debt.name}}"></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-primary">บันทึก</button></div></form></div></div></div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
async function calculatePayoff(e,t){e.preventDefault();const a=e.target,l=a.querySelector('input').value||0,o=document.getElementById(`debt-result-${t}`);o.textContent='กำลังคำนวณ...';const n=new FormData;n.append('debt_id',t),n.append('extra_payment',l);try{const e=await fetch("{{url_for('calculate_debt')}}",{method:'POST',body:n}),t=await e.json();t.error?o.textContent=`ข้อผิดพลาด: ${t.error}`:o.innerHTML=`<strong>ผล:</strong> หมดใน <strong>${t.duration}</strong> (~${t.payoff_date})`}catch(e){o.textContent='เกิดข้อผิดพลาดในการเชื่อมต่อ'}}
</script>
</body></html>
"""

# --- 6. APP RUNNER ---
if __name__ == '__main__':
    app.run(debug=True, port=5001)