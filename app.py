import os
import datetime
import json
from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd

# --- 1. INITIALIZE EXTENSIONS (WITHOUT APP INSTANCE) ---
# สร้าง instance ของ db และ login_manager ไว้นอกฟังก์ชัน
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login' # เปลี่ยน endpoint ให้มี prefix 'auth'
login_manager.login_message = "กรุณาเข้าสู่ระบบเพื่อใช้งาน"
login_manager.login_message_category = "info"


# --- 2. DATABASE MODELS ---
# โครงสร้าง Model เหมือนเดิมทุกประการ
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    transactions = db.relationship('Transaction', backref='owner', lazy=True, cascade="all, delete-orphan")
    debts = db.relationship('Debt', backref='owner', lazy=True, cascade="all, delete-orphan")
    categories = db.relationship('Category', backref='owner', lazy=True, cascade="all, delete-orphan")

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    debt_paid = db.Column(db.String(100), nullable=True) # เพิ่ม field นี้ใน model

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
    
# ... (ฟังก์ชัน calculate_debt_payoff_logic เหมือนเดิม) ...
def calculate_debt_payoff_logic(debt, extra_payment=0):
    balance = debt.current_balance
    if not balance > 0: return "หนี้ชำระหมดแล้ว", "N/A"
    monthly_rate = (debt.rate_percent / 100) / 12 if debt.rate_type == 'yearly' else debt.rate_percent / 100
    total_payment = debt.min_payment + extra_payment
    if total_payment <= 0: return "ยอดชำระต้องมากกว่า 0", None
    if total_payment <= balance * monthly_rate: return "ไม่มีวันหมด", None
    try:
        months = -np.log(1 - (balance * monthly_rate) / total_payment) / np.log(1 + monthly_rate)
        payoff_date = datetime.date.today() + datetime.timedelta(days=months * 30.44)
        return f"{months:.1f} เดือน", payoff_date.strftime('%d-%m-%Y')
    except (ValueError, ZeroDivisionError): return "คำนวณไม่ได้", None


# --- 3. APPLICATION FACTORY FUNCTION ---
def create_app():
    app = Flask(__name__)
    
    # --- CONFIGURATION ---
    app.config['SECRET_KEY'] = 'my_super_secure_postgresql_secret_key'
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or f"sqlite:///{os.path.join(os.path.dirname(__file__), 'local_dev.sqlite3')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # --- INITIALIZE EXTENSIONS WITH THE APP ---
    db.init_app(app)
    login_manager.init_app(app)

    # --- REGISTER BLUEPRINTS (กลุ่มของ Routes) ---
    from flask import Blueprint

    # Blueprint for Authentication routes
    auth_bp = Blueprint('auth', __name__)
    @auth_bp.route('/login', methods=['GET', 'POST'])
    def login():
        # ... (โค้ด login เหมือนเดิม) ...
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
        # ... (โค้ด register เหมือนเดิม) ...
        if current_user.is_authenticated: return redirect(url_for('main.index'))
        if request.method == 'POST':
            if User.query.filter_by(username=request.form['username']).first():
                flash('ชื่อผู้ใช้นี้มีคนใช้แล้ว', 'warning'); return redirect(url_for('auth.register'))
            hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
            new_user = User(username=request.form['username'], password_hash=hashed_password)
            db.session.add(new_user)
            db.session.commit()
            default_expenses = ['อาหาร', 'เดินทาง', 'ชำระหนี้', 'บันเทิง']
            default_incomes = ['เงินเดือน', 'รายได้เสริม']
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
        # ... (โค้ด index เหมือนเดิม) ...
        # (แค่เปลี่ยนการอ้างอิง template ที่อาจจะยังไม่ได้ประกาศ)
        today = datetime.date.today()
        year = request.args.get('year', default=today.year, type=int)
        month = request.args.get('month', default=today.month, type=int)
        start_date, end_date = datetime.date(year, month, 1), (datetime.date(year, month, 1) + datetime.timedelta(days=31)).replace(day=1) - datetime.timedelta(days=1)
        transactions = Transaction.query.filter_by(owner=current_user).filter(Transaction.date.between(start_date, end_date)).all()
        total_income, total_expense = sum(t.amount for t in transactions if t.type == 'income'), sum(t.amount for t in transactions if t.type == 'expense')
        expense_by_category = db.session.query(Transaction.category, db.func.sum(Transaction.amount)).filter_by(owner=current_user, type='expense').filter(Transaction.date.between(start_date, end_date)).group_by(Transaction.category).all()
        summary = {'total_income': total_income, 'total_expense': total_expense, 'net_balance': total_income - total_expense, 'expense_by_category_json': json.dumps({'labels': [c[0] for c in expense_by_category], 'data': [c[1] for c in expense_by_category]})}
        all_years = list(range(today.year - 5, today.year + 2))
        data = {'transactions': Transaction.query.filter_by(owner=current_user).order_by(Transaction.date.desc()).limit(15).all(), 'debts': Debt.query.filter_by(owner=current_user).all(), 'categories': {'income': [c.name for c in Category.query.filter_by(owner=current_user, type='income').all()], 'expense': [c.name for c in Category.query.filter_by(owner=current_user, type='expense').all()]}}
        return render_template_string(HTML_TEMPLATE, data=data, summary=summary, current_year=year, current_month=month, all_years=all_years, today=today)

    @main_bp.route('/debt/<int:debt_id>')
    @login_required
    def debt_detail(debt_id):
        # ... (โค้ด debt_detail เหมือนเดิม) ...
        debt = Debt.query.get_or_404(debt_id)
        if debt.user_id != current_user.id: abort(403)
        payment_history = Transaction.query.filter_by(owner=current_user, category='ชำระหนี้').filter(Transaction.description.like(f"%{debt.name}%")).order_by(Transaction.date.desc()).all()
        total_paid = sum(tx.amount for tx in payment_history)
        return render_template_string(DEBT_DETAIL_TEMPLATE, debt=debt, payment_history=payment_history, total_paid=total_paid, today=datetime.date.today(), data={'categories': {}})
    
    # ... (ย้าย Route อื่นๆ เข้ามาใน main_bp) ...
    @main_bp.route('/add_transaction', methods=['POST'])
    @login_required
    def add_transaction():
        #... (โค้ดเหมือนเดิม) ...
        return redirect(request.referrer or url_for('main.index'))

    @main_bp.route('/add_debt', methods=['POST'])
    @login_required
    def add_debt():
        #... (โค้ดเหมือนเดิม) ...
        return redirect(url_for('main.index'))

    @main_bp.route('/edit_debt/<int:debt_id>', methods=['POST'])
    @login_required
    def edit_debt(debt_id):
        #... (โค้ดเหมือนเดิม) ...
        return redirect(request.referrer or url_for('main.index'))
    
    # ... และอื่นๆ

    app.register_blueprint(main_bp)


    # --- REGISTER CLI COMMANDS ---
    @app.cli.command("init-db")
    def init_db_command():
        db.create_all()
        print("Initialized the database.")
    
    return app

# --- CREATE APP INSTANCE FOR GUNICORN ---
app = create_app()

# --- HTML TEMPLATES ---
# (AUTH_TEMPLATE, HTML_TEMPLATE, DEBT_DETAIL_TEMPLATE เหมือนเดิม)
# ...

# --- APP RUNNER (for local development) ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all() # สร้างตารางถ้ายังไม่มีตอนรันในเครื่อง
    app.run(host='0.0.0.0', port=5001, debug=True)