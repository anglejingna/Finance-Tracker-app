import os
import datetime
import json
from flask import Flask, render_template_string, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd # ยังคงใช้สำหรับย้ายข้อมูลเก่า

# --- 1. SETUP & CONFIGURATION ---
app = Flask(__name__)
# ตำแหน่งของไฟล์ฐานข้อมูล (สำคัญสำหรับ Render)
db_path = os.path.join(os.environ.get('RENDER_DISK_PATH', os.path.dirname(os.path.abspath(__file__))), 'database.sqlite3')
app.config['SECRET_KEY'] = 'a_very_robust_database_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "กรุณาเข้าสู่ระบบเพื่อใช้งาน"

# --- 2. DATABASE MODELS (โครงสร้างตาราง) ---

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
    type = db.Column(db.String(10), nullable=False) # 'income' or 'expense'
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Debt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    initial_balance = db.Column(db.Float, nullable=False)
    current_balance = db.Column(db.Float, nullable=False)
    rate_percent = db.Column(db.Float, nullable=False)
    rate_type = db.Column(db.String(10), nullable=False) # 'yearly' or 'monthly'
    min_payment = db.Column(db.Float, nullable=False)
    due_day = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(10), nullable=False) # 'income' or 'expense'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 3. AUTHENTICATION ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user, remember=True)
            return redirect(url_for('index'))
        flash('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง', 'danger')
    return render_template_string(AUTH_TEMPLATE, form_type='login')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        if User.query.filter_by(username=request.form['username']).first():
            flash('ชื่อผู้ใช้นี้มีคนใช้แล้ว', 'warning'); return redirect(url_for('register'))
        hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        new_user = User(username=request.form['username'], password_hash=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        # เพิ่มหมวดหมู่เริ่มต้นให้ผู้ใช้ใหม่
        default_expenses = ['อาหาร', 'เดินทาง', 'ชำระหนี้', 'บันเทิง']
        default_incomes = ['เงินเดือน', 'รายได้เสริม']
        for cat_name in default_expenses: db.session.add(Category(name=cat_name, type='expense', owner=new_user))
        for cat_name in default_incomes: db.session.add(Category(name=cat_name, type='income', owner=new_user))
        db.session.commit()
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
    today = datetime.date.today()
    year = request.args.get('year', default=today.year, type=int)
    month = request.args.get('month', default=today.month, type=int)
    
    start_date = datetime.date(year, month, 1)
    end_date = (start_date + datetime.timedelta(days=31)).replace(day=1) - datetime.timedelta(days=1)

    transactions = Transaction.query.filter_by(owner=current_user).filter(Transaction.date.between(start_date, end_date)).all()
    
    total_income = sum(t.amount for t in transactions if t.type == 'income')
    total_expense = sum(t.amount for t in transactions if t.type == 'expense')
    
    expense_by_category = db.session.query(
        Transaction.category, db.func.sum(Transaction.amount)
    ).filter_by(owner=current_user, type='expense').filter(
        Transaction.date.between(start_date, end_date)
    ).group_by(Transaction.category).all()
    
    summary = {
        'total_income': total_income,
        'total_expense': total_expense,
        'net_balance': total_income - total_expense,
        'expense_by_category_json': json.dumps({'labels': [cat[0] for cat in expense_by_category], 'data': [cat[1] for cat in expense_by_category]})
    }
    
    all_years = list(range(today.year - 5, today.year + 2))
    
    # จัดการ data dictionary ให้มีรูปแบบเหมือนเดิมเพื่อ template
    data = {
        'transactions': Transaction.query.filter_by(owner=current_user).order_by(Transaction.date.desc()).limit(15).all(),
        'debts': Debt.query.filter_by(owner=current_user).all(),
        'categories': {
            'income': [c.name for c in Category.query.filter_by(owner=current_user, type='income').all()],
            'expense': [c.name for c in Category.query.filter_by(owner=current_user, type='expense').all()]
        }
    }
    
    return render_template_string(HTML_TEMPLATE, data=data, summary=summary, current_year=year, current_month=month, all_years=all_years, today=today)

# ... (Route อื่นๆ ก็ต้องถูกปรับปรุงทั้งหมด) ...

@app.route('/add_transaction', methods=['POST'])
@login_required
def add_transaction():
    try:
        date_obj = datetime.datetime.strptime(request.form['date'], '%Y-%m-%d').date()
        amount = float(request.form['amount'])
        
        new_tx = Transaction(date=date_obj, description=request.form['description'], type=request.form['type'], category=request.form['category'], amount=amount, owner=current_user)
        db.session.add(new_tx)

        if request.form['type'] == 'expense' and request.form['category'] == 'ชำระหนี้':
            debt_paid_name = request.form.get('debt_paid')
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
    return redirect(request.referrer or url_for('index'))

# ... และ Route อื่นๆ ... (เพื่อความกระชับ ขอแสดงเฉพาะฟังก์ชันหลัก)
# ฟังก์ชัน add_debt, edit_debt, delete_transaction, add_category จะต้องถูกเขียนใหม่ทั้งหมดโดยใช้ db.session.add(), db.session.delete(), db.session.commit()

# --- 5. ADMIN AND MIGRATION ---
@app.cli.command("init-db")
def init_db_command():
    """สร้างตารางในฐานข้อมูล"""
    with app.app_context():
        db.create_all()
    print("Initialized the database.")

@app.cli.command("migrate-from-pkl")
def migrate_from_pkl_command():
    """ย้ายข้อมูลจาก finance_data.pkl (ไฟล์เก่า) เข้าสู่ SQLite"""
    OLD_FILE = 'finance_app_data.pkl'
    if not os.path.exists(OLD_FILE):
        print(f"ไม่พบไฟล์ข้อมูลเก่า '{OLD_FILE}'")
        return
    
    with app.app_context():
        try:
            print("กำลังอ่านข้อมูลจากไฟล์ pkl...")
            all_data = pd.read_pickle(OLD_FILE)
            
            # ย้ายข้อมูล Users
            print("กำลังย้ายข้อมูลผู้ใช้...")
            for user_id_str, user_info in all_data.get('users', {}).items():
                if not User.query.get(int(user_id_str)):
                    # ต้องมี password hash แต่เราไม่มี password จริง
                    # จึงสร้างเป็น user ที่ disable ไว้ก่อน หรือตั้งรหัสผ่านชั่วคราว
                    # ในที่นี้จะข้ามไปก่อนเพื่อความง่าย
                    print(f"ข้าม User ID: {user_id_str} เนื่องจากไม่มีรหัสผ่าน")

            # ย้ายข้อมูล Transaction, Debt, Category
            # สมมติว่าข้อมูลทั้งหมดเป็นของ User ID 1
            user = User.query.get(1)
            if not user:
                print("ไม่พบ User ID 1 ในฐานข้อมูล. กรุณาสร้างผู้ใช้คนแรกก่อนย้ายข้อมูล")
                return

            user_data = all_data.get('user_data', {}).get('1', {})
            print(f"กำลังย้ายข้อมูลสำหรับ {user.username}...")

            for tx_data in user_data.get('transactions', []):
                new_tx = Transaction(
                    date=datetime.datetime.strptime(tx_data['date'], '%Y-%m-%d').date(),
                    description=tx_data['description'],
                    type=tx_data['type'], category=tx_data['category'],
                    amount=tx_data['amount'], owner=user
                )
                db.session.add(new_tx)
            
            # ... ทำเช่นเดียวกันสำหรับ Debt และ Category ...

            db.session.commit()
            os.rename(OLD_FILE, OLD_FILE + '.migrated')
            print("ย้ายข้อมูลสำเร็จ!")

        except Exception as e:
            db.session.rollback()
            print(f"เกิดข้อผิดพลาดระหว่างการย้ายข้อมูล: {e}")

# --- 6. HTML TEMPLATES ---
# (AUTH_TEMPLATE, HTML_TEMPLATE, DEBT_DETAIL_TEMPLATE จะเหมือนเดิม ไม่ต้องแก้ไข)
# ...

# --- 7. APP RUNNER ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)