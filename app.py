import os
import datetime
import json
from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd # ใช้สำหรับ Migration (ถ้ามี)

# --- 1. INITIALIZE EXTENSIONS (WITHOUT APP INSTANCE) ---
# สร้าง instance ของ db และ login_manager ไว้นอกฟังก์ชัน create_app()
# นี่คือรูปแบบมาตรฐาน "Application Factory" เพื่อป้องกัน Circular Imports
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = "กรุณาเข้าสู่ระบบเพื่อใช้งาน"
login_manager.login_message_category = "info"


# --- 2. DATABASE MODELS ---
# โครงสร้างของตารางในฐานข้อมูล
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    # ความสัมพันธ์: เมื่อลบ User ให้ลบข้อมูลทั้งหมดที่เกี่ยวข้องไปด้วย
    transactions = db.relationship('Transaction', backref='owner', lazy=True, cascade="all, delete-orphan")
    debts = db.relationship('Debt', backref='owner', lazy=True, cascade="all, delete-orphan")
    categories = db.relationship('Category', backref='owner', lazy=True, cascade="all, delete-orphan")

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.date.today)
    description = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    debt_paid = db.Column(db.String(100), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Debt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    initial_balance = db.Column(db.Float, nullable=False)
    current_balance = db.Column(db.Float, nullable=False)
    rate_percent = db.Column(db.Float, nullable=False)
    rate_type = db.Column(db.String(10), nullable=False)
    min_payment = db.Column(db.Float, nullable=False)
    due_day = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
    
def calculate_debt_payoff_logic(debt, extra_payment=0):
    balance = debt.current_balance
    if not balance > 0: return "หนี้ชำระหมดแล้ว", "N/A"
    monthly_rate = (debt.rate_percent / 100) / 12 if debt.rate_type == 'yearly' else debt.rate_percent / 100
    total_payment = debt.min_payment + extra_payment
    if total_payment <= 0: return "ยอดชำระต้องมากกว่า 0", None
    if total_payment <= balance * monthly_rate and total_payment > 0: return "ไม่มีวันหมด (ยอดชำระน้อยกว่าดอกเบี้ย)", None
    try:
        months = -np.log(1 - (balance * monthly_rate) / total_payment) / np.log(1 + monthly_rate)
        payoff_date = datetime.date.today() + datetime.timedelta(days=months * 30.44)
        return f"{months:.1f} เดือน", payoff_date.strftime('%d-%m-%Y')
    except (ValueError, ZeroDivisionError): return "คำนวณไม่ได้", None


# --- 3. APPLICATION FACTORY FUNCTION ---
def create_app():
    app = Flask(__name__)
    
    # --- CONFIGURATION ---
    app.config['SECRET_KEY'] = 'my_final_super_secure_postgresql_secret_key'
    # อ่าน Database URL จาก Environment Variable ของ Render
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    # ถ้าหาไม่เจอ (ตอนรันในเครื่อง) ให้ใช้ไฟล์ SQLite ชั่วคราวไปก่อน
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or f"sqlite:///{os.path.join(os.path.dirname(__file__), 'local_dev.sqlite3')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # --- INITIALIZE EXTENSIONS WITH THE APP ---
    db.init_app(app)
    login_manager.init_app(app)

    # --- CREATE DATABASE TABLES IF THEY DON'T EXIST (For Render Free Tier) ---
    with app.app_context():
        db.create_all()

    # --- REGISTER BLUEPRINTS (กลุ่มของ Routes) ---
    from flask import Blueprint

    # Blueprint for Authentication routes
    auth_bp = Blueprint('auth', __name__)
    @auth_bp.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated: return redirect(url_for('main.index'))
        if request.method == 'POST':
            user = User.query.filter_by(username=request.form['username']).first()
            if user and check_password_hash(user.password_hash, request.form['password']):
                login_user(user, remember=True)
                return redirect(url_for('main.index'))
            flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง', 'danger')
        return render_template_string(AUTH_TEMPLATE, form_type='login')

    @auth_bp.route('/register', methods=['GET', 'POST'])
    def register():
        if current_user.is_authenticated: return redirect(url_for('main.index'))
        if request.method == 'POST':
            if User.query.filter_by(username=request.form['username']).first():
                flash('ชื่อผู้ใช้นี้มีคนใช้แล้ว', 'warning'); return redirect(url_for('auth.register'))
            hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
            new_user = User(username=request.form['username'], password_hash=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            default_expenses = ['อาหารและเครื่องดื่ม', 'เดินทาง', 'ที่อยู่อาศัย', 'ชำระหนี้', 'บันเทิง', 'ช้อปปิ้ง', 'สุขภาพ', 'การลงทุน']
            default_incomes = ['เงินเดือน', 'รายได้เสริม', 'โบนัส']
            for cat_name in default_expenses: db.session.add(Category(name=cat_name, type='expense', owner=new_user))
            for cat_name in default_incomes: db.session.add(Category(name=cat_name, type='income', owner=new_user))
            db.session.commit()
            flash('สมัครสมาชิกสำเร็จ! กรุณาเข้าสู่ระบบ', 'success')
            return redirect(url_for('auth.login'))
        return render_template_string(AUTH_TEMPLATE, form_type='register')

    @auth_bp.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('auth.login'))
    app.register_blueprint(auth_bp)

    # Blueprint for Main application routes
    main_bp = Blueprint('main', __name__)
    @main_bp.route('/')
    @login_required
    def index():
        today = datetime.date.today()
        year = request.args.get('year', default=today.year, type=int)
        month = request.args.get('month', default=today.month, type=int)
        start_date = datetime.date(year, month, 1)
        end_day = (start_date.replace(day=28) + datetime.timedelta(days=4)).day
        end_date = start_date.replace(day=end_day)
        transactions = Transaction.query.filter_by(owner=current_user).filter(Transaction.date.between(start_date, end_date)).all()
        total_income = sum(t.amount for t in transactions if t.type == 'income')
        total_expense = sum(t.amount for t in transactions if t.type == 'expense')
        expense_by_category = db.session.query(Transaction.category, db.func.sum(Transaction.amount)).filter_by(owner=current_user, type='expense').filter(Transaction.date.between(start_date, end_date)).group_by(Transaction.category).all()
        summary = {'total_income': total_income, 'total_expense': total_expense, 'net_balance': total_income - total_expense, 'expense_by_category_json': json.dumps({'labels': [c[0] for c in expense_by_category], 'data': [float(c[1]) for c in expense_by_category]})}
        all_years = list(range(today.year - 5, today.year + 2))
        data = {
            'transactions': Transaction.query.filter_by(owner=current_user).order_by(Transaction.date.desc()).limit(15).all(),
            'debts': Debt.query.filter_by(owner=current_user).order_by(Debt.name).all(),
            'categories': {
                'income': [c.name for c in Category.query.filter_by(owner=current_user, type='income').all()],
                'expense': [c.name for c in Category.query.filter_by(owner=current_user, type='expense').all()]
            }
        }
        return render_template_string(HTML_TEMPLATE, data=data, summary=summary, current_year=year, current_month=month, all_years=all_years, today=today)

    @main_bp.route('/debt/<int:debt_id>')
    @login_required
    def debt_detail(debt_id):
        debt = Debt.query.get_or_404(debt_id)
        if debt.user_id != current_user.id: abort(403)
        payment_history = Transaction.query.filter(Transaction.user_id==current_user.id, Transaction.category=='ชำระหนี้', Transaction.debt_paid==debt.name).order_by(Transaction.date.desc()).all()
        total_paid = sum(tx.amount for tx in payment_history)
        return render_template_string(DEBT_DETAIL_TEMPLATE, debt=debt, payment_history=payment_history, total_paid=total_paid, today=datetime.date.today())

    @main_bp.route('/add_transaction', methods=['POST'])
    @login_required
    def add_transaction():
        try:
            date_obj = datetime.datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            amount = float(request.form['amount'])
            debt_paid_name = request.form.get('debt_paid') if request.form.get('category') == 'ชำระหนี้' else None
            new_tx = Transaction(date=date_obj, description=request.form['description'], type=request.form['type'], category=request.form['category'], amount=amount, debt_paid=debt_paid_name, owner=current_user)
            db.session.add(new_tx)
            if debt_paid_name:
                debt = Debt.query.filter_by(name=debt_paid_name, owner=current_user).first()
                if debt:
                    debt.current_balance -= amount
                    flash(f"ยอดหนี้ '{debt.name}' อัปเดตแล้ว!", "info")
            db.session.commit()
            flash("เพิ่มรายการสำเร็จ!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"เกิดข้อผิดพลาด: {e}", "danger")
        return redirect(request.referrer or url_for('main.index'))

    @main_bp.route('/add_debt', methods=['POST'])
    @login_required
    def add_debt():
        try:
            balance = float(request.form['balance'])
            new_debt = Debt(name=request.form['name'], initial_balance=balance, current_balance=balance, rate_percent=float(request.form['rate_percent']), rate_type=request.form['rate_type'], min_payment=float(request.form['min_payment']), due_day=int(request.form['due_day']), owner=current_user)
            db.session.add(new_debt)
            db.session.commit()
            flash(f"เพิ่มหนี้ '{new_debt.name}' สำเร็จ", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"ข้อมูลหนี้สินไม่ถูกต้อง: {e}", "danger")
        return redirect(url_for('main.index'))

    @main_bp.route('/edit_debt/<int:debt_id>', methods=['POST'])
    @login_required
    def edit_debt(debt_id):
        debt = Debt.query.get_or_404(debt_id)
        if debt.user_id != current_user.id: abort(403)
        try:
            debt.name = request.form['name']
            debt.initial_balance = float(request.form['initial_balance'])
            debt.current_balance = float(request.form['current_balance'])
            debt.rate_percent = float(request.form['rate_percent'])
            debt.rate_type = request.form['rate_type']
            debt.min_payment = float(request.form['min_payment'])
            debt.due_day = int(request.form['due_day'])
            db.session.commit()
            flash(f"แก้ไขข้อมูลหนี้ '{debt.name}' สำเร็จ", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"ข้อมูลที่แก้ไขไม่ถูกต้อง: {e}", "danger")
        return redirect(request.referrer or url_for('main.index'))
    
    @main_bp.route('/delete_transaction/<int:tx_id>', methods=['POST'])
    @login_required
    def delete_transaction(tx_id):
        tx = Transaction.query.get_or_404(tx_id)
        if tx.user_id != current_user.id: abort(403)
        try:
            if tx.category == 'ชำระหนี้' and tx.debt_paid:
                debt = Debt.query.filter_by(name=tx.debt_paid, owner=current_user).first()
                if debt:
                    debt.current_balance += tx.amount
                    flash(f"คืนยอดเงินให้หนี้ '{debt.name}' เรียบร้อย", "info")
            db.session.delete(tx)
            db.session.commit()
            flash("ลบรายการสำเร็จ!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"เกิดข้อผิดพลาดในการลบ: {e}", "danger")
        return redirect(url_for('main.index'))

    @main_bp.route('/add_category', methods=['POST'])
    @login_required
    def add_category():
        try:
            cat_type, new_cat = request.form['type'], request.form['name'].strip()
            exists = Category.query.filter_by(name=new_cat, type=cat_type, owner=current_user).first()
            if new_cat and not exists:
                db.session.add(Category(name=new_cat, type=cat_type, owner=current_user))
                db.session.commit()
                flash(f"เพิ่มหมวดหมู่ '{new_cat}' สำเร็จ", "success")
            else: flash(f"หมวดหมู่ '{new_cat}' อาจมีอยู่แล้ว", "warning")
        except Exception as e:
            db.session.rollback()
            flash(f"เกิดข้อผิดพลาด: {e}", "danger")
        return redirect(url_for('main.index'))

    @main_bp.route('/calculate_debt', methods=['POST'])
    @login_required
    def calculate_debt():
        try:
            debt_id = int(request.form['debt_id'])
            debt = Debt.query.get_or_404(debt_id)
            if debt.user_id != current_user.id: abort(403)
            extra_payment = float(request.form.get('extra_payment', 0))
            duration, payoff_date = calculate_debt_payoff_logic(debt, extra_payment)
            return jsonify({'debt_name': debt.name, 'duration': duration, 'payoff_date': payoff_date})
        except Exception: return jsonify({'error': 'ข้อมูลไม่ถูกต้อง'}), 400

    app.register_blueprint(main_bp)
    
    return app

# --- CREATE APP INSTANCE FOR GUNICORN & LOCAL RUN ---
app = create_app()

# --- HTML TEMPLATES ---
AUTH_TEMPLATE = """<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"><title>{{'เข้าสู่ระบบ' if form_type=='login' else 'สมัครสมาชิก'}}</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{display:flex;align-items:center;padding-top:40px;padding-bottom:40px;background-color:#f5f5f5;height:100vh}.form-signin{width:100%;max-width:330px;padding:15px;margin:auto}</style></head><body class="text-center"><main class="form-signin"><form method="POST" action=""><h1 class="h3 mb-3 fw-normal">{{'กรุณาเข้าสู่ระบบ' if form_type=='login' else 'สร้างบัญชีใหม่'}}</h1>{% with messages=get_flashed_messages(with_categories=true)%}{% if messages%}{% for category,message in messages%}<div class="alert alert-{{category}}">{{message}}</div>{% endfor%}{% endif%}{% endwith %}<div class="form-floating"><input type="text" name="username" class="form-control" id="floatingInput" placeholder="Username" required><label for="floatingInput">ชื่อผู้ใช้</label></div><div class="form-floating"><input type="password" name="password" class="form-control" id="floatingPassword" placeholder="Password" required><label for="floatingPassword">รหัสผ่าน</label></div><button class="w-100 btn btn-lg btn-primary mt-3" type="submit">{{'เข้าสู่ระบบ' if form_type=='login' else 'สมัครสมาชิก'}}</button><p class="mt-3">{% if form_type=='login'%}ยังไม่มีบัญชี? <a href="{{url_for('auth.register')}}">สมัครสมาชิก</a>{% else %}มีบัญชีอยู่แล้ว? <a href="{{url_for('auth.login')}}">เข้าสู่ระบบ</a>{% endif %}</p></form></main></body></html>"""
HTML_TEMPLATE = """<!DOCTYPE html><html lang="th" data-bs-theme="light"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Finance Dashboard</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"><link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;700&display=swap" rel="stylesheet"><style>body{font-family:'Sarabun',sans-serif;background-color:#f0f2f5}.card{border:none;border-radius:.8rem;box-shadow:0 4px 12px rgba(0,0,0,.08);overflow:hidden}.debt-card{transition:transform .2s ease-in-out}.debt-card:hover{transform:translateY(-5px);box-shadow:0 8px 20px rgba(0,0,0,.12)}.table-responsive{max-height:65vh}.net-positive{color:#198754!important}.net-negative{color:#dc3545!important}.sticky-top{top:1rem}a{text-decoration:none}</style></head><body><div class="container-fluid p-4"><header class="d-flex justify-content-between align-items-center mb-4"><h1 class="h3 mb-0"><i class="bi bi-wallet2 me-2"></i>Finance Dashboard <small class="text-muted h6">({{ current_user.username }})</small></h1><div><button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addTransactionModal"><i class="bi bi-plus-circle me-1"></i> เพิ่มรายการ</button><button class="btn btn-warning text-dark" data-bs-toggle="modal" data-bs-target="#addDebtModal"><i class="bi bi-credit-card me-1"></i> เพิ่มหนี้สิน</button><a href="{{ url_for('auth.logout') }}" class="btn btn-outline-secondary"><i class="bi bi-box-arrow-right me-1"></i> ออกจากระบบ</a></div></header>{% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}{% for category, message in messages %}<div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">{{ message }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endif %}{% endwith %}<div class="row g-4"><div class="col-lg-4"><div class="sticky-top"><div class="card mb-4"><div class="card-body"><h5 class="card-title mb-3"><i class="bi bi-bar-chart-line me-2"></i>สรุปภาพรวม</h5><form method="GET" action="{{url_for('main.index')}}" class="d-flex gap-2 mb-3"><select name="month" class="form-select form-select-sm">{% for i in range(1,13) %}<option value="{{i}}" {% if i==current_month %}selected{% endif %}>เดือน {{i}}</option>{% endfor %}</select><select name="year" class="form-select form-select-sm">{% for y in all_years %}<option value="{{y}}" {% if y==current_year %}selected{% endif %}>{{y}}</option>{% endfor %}</select><button type="submit" class="btn btn-sm btn-outline-primary"><i class="bi bi-search"></i></button></form><div class="d-flex justify-content-around text-center"><div><small class="text-muted">รายรับ</small><p class="h5 net-positive mb-0">{{ "%.2f"|format(summary.total_income) }}</p></div><div><small class="text-muted">รายจ่าย</small><p class="h5 net-negative mb-0">{{ "%.2f"|format(summary.total_expense) }}</p></div><div><small class="text-muted">คงเหลือ</small><p class="h5 {{'net-positive' if summary.net_balance >=0 else 'net-negative'}} mb-0">{{ "%.2f"|format(summary.net_balance) }}</p></div></div></div></div><div class="card"><div class="card-body"><h5 class="card-title mb-3"><i class="bi bi-pie-chart me-2"></i>สัดส่วนรายจ่าย</h5><div style="height:300px"><canvas id="expenseChart"></canvas></div></div></div></div></div><div class="col-lg-8"><h4 class="mb-3"><i class="bi bi-journal-text me-2"></i>ติดตามหนี้สิน</h4><div class="row g-4">{% if data.debts %}{% for debt in data.debts %}<div class="col-md-6"><div class="card debt-card h-100"><div class="card-body position-relative"><div class="d-flex justify-content-between align-items-start mb-2"><a href="{{url_for('main.debt_detail',debt_id=debt.id)}}" class="text-dark stretched-link"><h5 class="card-title mb-1">{{debt.name}}</h5></a><button class="btn btn-sm btn-outline-secondary border-0" data-bs-toggle="modal" data-bs-target="#editDebtModal-{{debt.id}}" style="z-index:5" onclick="event.stopPropagation();"><i class="bi bi-pencil-square"></i></button></div><p class="h3 mb-1">฿{{ "%.2f"|format(debt.current_balance) }}</p><small class="text-muted">จาก {{ "%.2f"|format(debt.initial_balance) }}</small><div class="progress mt-2 mb-3" role="progressbar" style="height:5px"><div class="progress-bar bg-success" style="width:{{(100-(debt.current_balance/debt.initial_balance*100)) if debt.initial_balance>0 else 0}}%"></div></div><p class="small text-muted mb-0"><i class="bi bi-calendar-check me-1"></i>ครบกำหนดวันที่ {{debt.due_day}}{% set days_left=debt.due_day-today.day %}{% if 0<=days_left<=5 %}<span class="badge bg-danger-subtle text-danger-emphasis rounded-pill ms-2">อีก {{days_left}} วัน</span>{% endif %}</p></div></div></div>{% endfor %}{% else %}<p class="text-center text-muted">ยังไม่มีข้อมูลหนี้สิน</p>{% endif %}</div><div class="card mt-4"><div class="card-header bg-white d-flex justify-content-between align-items-center"><h5 class="mb-0"><i class="bi bi-list-ul me-2"></i>ประวัติรายการล่าสุด</h5><button class="btn btn-sm btn-outline-secondary" data-bs-toggle="modal" data-bs-target="#addCategoryModal"><i class="bi bi-tag-fill me-1"></i>จัดการหมวดหมู่</button></div><div class="table-responsive"><table class="table table-hover mb-0 align-middle"><tbody>{% for tx in data.transactions %}{% if loop.index<=15 %}<tr><td class="ps-3"><i class="bi h5 mb-0 {{'bi-arrow-down-circle-fill text-success' if tx.type=='income' else 'bi-arrow-up-circle-fill text-danger'}}"></i></td><td>{{tx.date.strftime('%Y-%m-%d')}}</td><td><strong>{{tx.description}}</strong><br><small class="text-muted">{{tx.category}}</small></td><td class="text-end fw-bold {{'net-positive' if tx.type=='income' else 'net-negative'}}">{{('+' if tx.type=='income' else '-')~"%.2f"|format(tx.amount)}}</td><td class="text-end pe-3"><form action="{{url_for('main.delete_transaction',tx_id=tx.id)}}" method="POST" onsubmit="return confirm('แน่ใจหรือไม่?')"><button type="submit" class="btn btn-sm border-0"><i class="bi bi-x-lg text-muted"></i></button></form></td></tr>{% endif %}{% endfor %}</tbody></table></div></div></div></div></div><div class="modal fade" id="addTransactionModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content"><form action="{{url_for('main.add_transaction')}}" method="POST"><div class="modal-header"><h5 class="modal-title">เพิ่มรายการ</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="row"><div class="col-md-6"><div class="mb-3"><input type="date" name="date" class="form-control" value="{{today.strftime('%Y-%m-%d')}}" required></div><div class="mb-3"><input type="text" name="description" class="form-control" placeholder="รายการ" required></div><div class="mb-3"><select name="type" class="form-select" id="transactionType"><option value="expense">รายจ่าย</option><option value="income">รายรับ</option></select></div><div class="mb-3"><label class="form-label">หมวดหมู่</label><select name="category" id="category-select" class="form-select" required></select></div><div class="mb-3" id="debt-payment-field" style="display:none"><label class="form-label">ชำระหนี้สำหรับ</label><select name="debt_paid" class="form-select"><option value="">-- ไม่ระบุ --</option>{% for debt in data.debts %}<option value="{{debt.name}}">{{debt.name}}</option>{% endfor %}</select></div><div class="mb-3"><label class="form-label">จำนวนเงินรวม</label><input type="number" step="0.01" name="amount" id="totalAmount" class="form-control" placeholder="0.00" required></div></div><div class="col-md-6 border-start"><h6><i class="bi bi-receipt"></i> เครื่องคิดเลขรายการย่อย</h6><div id="item-list"></div><button type="button" class="btn btn-sm btn-outline-secondary" id="addItemBtn"><i class="bi bi-plus-lg"></i> เพิ่มรายการย่อย</button></div></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-primary">บันทึก</button></div></form></div></div></div><div class="modal fade" id="addCategoryModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('main.add_category')}}" method="POST"><div class="modal-header"><h5 class="modal-title">จัดการหมวดหมู่</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-3"><label class="form-label">ประเภท</label><select name="type" class="form-select"><option value="expense">รายจ่าย</option><option value="income">รายรับ</option></select></div><div class="mb-3"><label class="form-label">ชื่อหมวดหมู่ใหม่</label><input type="text" name="name" class="form-control" required></div><button type="submit" class="btn btn-primary w-100">เพิ่มหมวดหมู่</button><hr><p>หมวดหมู่ที่มีอยู่:</p><ul>{% for cat in data.categories.expense %}<li>{{cat}} (รายจ่าย)</li>{% endfor %}{% for cat in data.categories.income %}<li>{{cat}} (รายรับ)</li>{% endfor %}</ul></div></form></div></div></div><div class="modal fade" id="addDebtModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('main.add_debt')}}" method="POST"><div class="modal-header"><h5 class="modal-title">เพิ่มหนี้สิน</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-3"><input type="text" name="name" class="form-control" placeholder="ชื่อหนี้" required></div><div class="mb-3"><input type="number" step="0.01" name="balance" class="form-control" placeholder="ยอดหนี้ทั้งหมด" required></div><div class="row g-2 mb-3"><div class="col-8"><input type="number" step="0.01" name="rate_percent" class="form-control" placeholder="อัตราดอกเบี้ย" required></div><div class="col-4"><select name="rate_type" class="form-select"><option value="yearly">ต่อปี</option><option value="monthly">ต่อเดือน</option></select></div></div><div class="row g-2 mb-3"><div class="col-8"><input type="number" step="0.01" name="min_payment" class="form-control" placeholder="ชำระขั้นต่ำ/เดือน" required></div><div class="col-4"><div class="input-group"><input type="number" name="due_day" class="form-control" placeholder="วันที่" value="1" min="1" max="31" required></div></div></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-warning text-dark">เพิ่มหนี้</button></div></form></div></div></div>{% for debt in data.debts %}<div class="modal fade" id="editDebtModal-{{debt.id}}" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('main.edit_debt',debt_id=debt.id)}}" method="POST"><div class="modal-header"><h5 class="modal-title">แก้ไขหนี้: {{debt.name}}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-3"><label class="form-label">ชื่อหนี้</label><input type="text" name="name" class="form-control" value="{{debt.name}}" required></div><div class="row g-2 mb-3"><div class="col"><label class="form-label">ยอดตั้งต้น</label><input type="number" step="0.01" name="initial_balance" value="{{debt.initial_balance}}" class="form-control" required></div><div class="col"><label class="form-label">ยอดปัจจุบัน</label><input type="number" step="0.01" name="current_balance" value="{{debt.current_balance}}" class="form-control" required></div></div><div class="row g-2 mb-3"><div class="col-8"><label class="form-label">ดอกเบี้ย</label><input type="number" step="0.01" name="rate_percent" value="{{debt.rate_percent}}" class="form-control" required></div><div class="col-4"><label class="form-label"> </label><select name="rate_type" class="form-select"><option value="yearly" {% if debt.rate_type=='yearly'%}selected{% endif %}>ต่อปี</option><option value="monthly" {% if debt.rate_type=='monthly'%}selected{% endif %}>ต่อเดือน</option></select></div></div><div class="row g-2 mb-3"><div class="col-8"><label class="form-label">ขั้นต่ำ/เดือน</label><input type="number" step="0.01" name="min_payment" value="{{debt.min_payment}}" class="form-control" required></div><div class="col-4"><label class="form-label">วันครบกำหนด</label><input type="number" name="due_day" class="form-control" value="{{debt.due_day}}" min="1" max="31" required></div></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ยกเลิก</button><button type="submit" class="btn btn-primary">บันทึก</button></div></form></div></div></div>{% endfor %}<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script><script src="https://cdn.jsdelivr.net/npm/chart.js"></script><script>document.addEventListener('DOMContentLoaded',function(){const e={{summary.expense_by_category_json|safe}},t=document.getElementById("expenseChart");t&&e.labels&&e.labels.length>0?new Chart(t,{type:"doughnut",data:{labels:e.labels,datasets:[{data:e.data,backgroundColor:["#ff6384","#36a2eb","#ffce56","#4bc0c0","#9966ff","#ff9f40","#c9cbcf"],borderColor:"#fff",borderWidth:2,hoverOffset:8}]},options:{responsive:!0,maintainAspectRatio:!1,animation:{animateScale:!0,animateRotate:!0},plugins:{legend:{position:"bottom",labels:{usePointStyle:!0,padding:20}},tooltip:{yAlign:"bottom",displayColors:!1,callbacks:{label:function(e){let t=e.label||"",a=e.raw,l=e.chart.getDatasetMeta(0).total,o=(a/l*100).toFixed(2)+"%";return`${t} ${new Intl.NumberFormat("th-TH",{style:"currency",currency:"THB"}).format(a)} (${o})`}}}}}}):t&&(t.getContext("2d").textAlign="center",t.getContext("2d").textBaseline="middle",t.getContext("2d").font="16px Sarabun",t.getContext("2d").fillText("ไม่มีข้อมูลรายจ่ายในเดือนนี้",t.width/2,t.height/2));const a={{data.categories|tojson}},l=document.getElementById("category-select"),o=document.getElementById("transactionType"),n=document.getElementById("debt-payment-field"),d=()=>{n.style.display="expense"===o.value&&"ชำระหนี้"===l.value?"block":"none"},c=e=>{l.innerHTML="",(a[e]||[]).forEach(e=>{const t=document.createElement("option");t.value=e,t.textContent=e,l.appendChild(t)}),d()};o&&o.addEventListener("change",()=>c(o.value)),l&&l.addEventListener("change",d),c(o?o.value:"expense");const i=document.getElementById("item-list"),s=document.getElementById("totalAmount"),r=()=>{let e=0;document.querySelectorAll(".item-price").forEach(t=>{e+=parseFloat(t.value)||0}),s.value=e.toFixed(2)};document.getElementById("addItemBtn").addEventListener("click",()=>{const e=document.createElement("div");e.className="d-flex gap-2 mb-2",e.innerHTML=`<input type="text" class="form-control form-control-sm" placeholder="ชื่อของ"><input type="number" step="0.01" class="form-control form-control-sm item-price" placeholder="ราคา"><button type="button" class="btn btn-sm btn-outline-danger" onclick="this.parentElement.remove();r();">X</button>`,i.appendChild(e),e.querySelector(".item-price").addEventListener("input",r)})});</script></body></html>"""
DEBT_DETAIL_TEMPLATE = """<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8"><title>รายละเอียดหนี้: {{debt.name}}</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"><link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;700&display=swap" rel="stylesheet"><style>body{font-family:'Sarabun',sans-serif;background-color:#f8f9fa}</style></head><body><div class="container mt-4"><nav aria-label="breadcrumb"><ol class="breadcrumb"><li class="breadcrumb-item"><a href="{{url_for('main.index')}}">Dashboard</a></li><li class="breadcrumb-item active" aria-current="page">{{debt.name}}</li></ol></nav><div class="row g-4"><div class="col-md-4"><div class="card"><div class="card-body text-center"><h5 class="card-title">{{debt.name}}</h5><p class="display-4 text-danger fw-bold">฿{{"%.2f"|format(debt.current_balance)}}</p><p class="text-muted">ยอดคงเหลือ</p><div class="progress mb-3"><div class="progress-bar bg-success" style="width:{{(100-(debt.current_balance/debt.initial_balance*100)) if debt.initial_balance > 0 else 0}}%"></div></div><form class="d-flex gap-2" onsubmit="calculatePayoff(event,{{debt.id}})"><input type="number" step="0.01" class="form-control" placeholder="โปะเพิ่ม/เดือน"><button type="submit" class="btn btn-info flex-shrink-0"><i class="bi bi-calculator"></i> คำนวณ</button></form><small id="debt-result-{{debt.id}}" class="form-text text-muted d-block mt-1"></small></div><ul class="list-group list-group-flush"><li class="list-group-item d-flex justify-content-between"><span>ยอดตั้งต้น:</span><strong>{{"%.2f"|format(debt.initial_balance)}}</strong></li><li class="list-group-item d-flex justify-content-between"><span>ชำระไปแล้ว:</span><strong class="text-success">{{"%.2f"|format(total_paid)}}</strong></li><li class="list-group-item d-flex justify-content-between"><span>ขั้นต่ำ:</span><strong>{{"%.2f"|format(debt.min_payment)}}/เดือน</strong></li><li class="list-group-item d-flex justify-content-between"><span>ครบกำหนด:</span><strong>วันที่ {{debt.due_day}}</strong></li></ul></div></div><div class="col-md-8"><div class="card"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-clock-history me-1"></i> ประวัติการชำระเงิน</span><button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#addTransactionModal"><i class="bi bi-plus-lg"></i> บันทึกการชำระ</button></div><div class="card-body">{% if payment_history %}<table class="table"><thead><tr><th>วันที่</th><th>รายละเอียด</th><th class="text-end">จำนวนเงิน</th></tr></thead><tbody>{% for tx in payment_history %}<tr><td>{{tx.date.strftime('%Y-%m-%d')}}</td><td>{{tx.description}}</td><td class="text-end text-danger">-{{"%.2f"|format(tx.amount)}}</td></tr>{% endfor %}</tbody></table>{% else %}<p class="text-center text-muted p-4">ยังไม่มีประวัติการชำระสำหรับหนี้ก้อนนี้</p>{% endif %}</div></div></div></div></div><div class="modal fade" id="addTransactionModal" tabindex="-1"><div class="modal-dialog"><div class="modal-content"><form action="{{url_for('main.add_transaction')}}" method="POST"><div class="modal-header"><h5 class="modal-title">บันทึกการชำระหนี้: {{debt.name}}</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><input type="hidden" name="type" value="expense"><input type="hidden" name="category" value="ชำระหนี้"><input type="hidden" name="debt_paid" value="{{debt.name}}"><div class="mb-3"><label class="form-label">วันที่ชำระ</label><input type="date" name="date" class="form-control" value="{{today.strftime('%Y-%m-%d')}}" required></div><div class="mb-3"><label class="form-label">จำนวนเงินที่ชำระ</label><input type="number" step="0.01" name="amount" class="form-control" placeholder="0.00" required></div><div class="mb-3"><label class="form-label">รายละเอียด (ไม่บังคับ)</label><input type="text" name="description" class="form-control" value="ชำระหนี้ {{debt.name}}"></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">ปิด</button><button type="submit" class="btn btn-primary">บันทึก</button></div></form></div></div></div><script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script><script>async function calculatePayoff(e,t){e.preventDefault();const a=e.target,l=a.querySelector('input').value||0,o=document.getElementById(`debt-result-${t}`);o.textContent='กำลังคำนวณ...';const n=new FormData;n.append('debt_id',t),n.append('extra_payment',l);try{const e=await fetch("{{url_for('main.calculate_debt')}}",{method:'POST',body:n}),t=await e.json();t.error?o.textContent=`ข้อผิดพลาด: ${t.error}`:o.innerHTML=`<strong>ผล:</strong> หมดใน <strong>${t.duration}</strong> (~${t.payoff_date})`}catch(e){o.textContent='เกิดข้อผิดพลาดในการเชื่อมต่อ'}}</script></body></html>"""

# --- APP RUNNER ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)