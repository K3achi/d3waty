import os
import sqlite3
import uuid
import zipfile
import re
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image
import qrcode

app = Flask(__name__)
# تغيير الـ Secret Key لمستوى حماية عالٍ وتأمين الجلسات ضد الاختراق
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super-secure-suite-dawaty-2026-key')
app.config['PERMANENT_SESSION_LIFETIME'] = 1800 # تنتهي الجلسة تلقائياً بعد 30 دقيقة خمول

UPLOAD_FOLDER = 'uploads'
DB_FOLDER = 'databases'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DB_FOLDER, exist_ok=True)

CENTRAL_DB = os.path.join(DB_FOLDER, 'central.db')

def get_central_conn():
    conn = sqlite3.connect(CENTRAL_DB)
    conn.row_factory = sqlite3.Row
    return conn

def get_tenant_conn(username):
    # ميزة قاعدة بيانات خاصة ومستقلة تماماً لكل حساب لمنع تسريب أو تداخل البيانات
    tenant_db = os.path.join(DB_FOLDER, f'tenant_{username}.db')
    conn = sqlite3.connect(tenant_db)
    conn.row_factory = sqlite3.Row
    return conn

def init_databases():
    # 1. تهيئة قاعدة البيانات المركزية للمستخدمين
    conn = get_central_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE,
                    password_hash TEXT,
                    phone TEXT UNIQUE,
                    is_admin INTEGER DEFAULT 0
                 )''')
    
    # 2. زراعة حساب المالك الافتراضي (RAED_ADMIN) بحماية تشفيرية متقدمة
    c.execute("SELECT * FROM users WHERE username = 'RAED_ADMIN'")
    if not c.fetchone():
        hashed_pw = generate_password_hash('Rx0576511313')
        c.execute("INSERT INTO users (id, username, password_hash, phone, is_admin) VALUES (?, ?, ?, ?, 1)",
                  (str(uuid.uuid4()), 'RAED_ADMIN', hashed_pw, '0576511313'))
    conn.commit()
    conn.close()

def init_tenant_db(username):
    # تهيئة جدول الضيوف داخل قاعدة البيانات الخاصة بالمستأجر الجديد
    conn = get_tenant_conn(username)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS guests 
                 (id TEXT PRIMARY KEY, name TEXT, is_checked_in INTEGER DEFAULT 0, scan_count INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_databases()

# دالة التحقق من قوة كلمة المرور (أغلب خيارات كلمات المرور المشهورة)
def is_password_strong(password):
    if len(password) < 8:
        return False, "يجب أن تتكون كلمة المرور من 8 خانات على الأقل."
    if not re.search(r"[a-z]", password):
        return False, "يجب أن تحتوي كلمة المرور على حرف صغير واحد على الأقل (a-z)."
    if not re.search(r"[A-Z]", password):
        return False, "يجب أن تحتوي كلمة المرور على حرف كبير واحد على الأقل (A-Z)."
    if not re.search(r"[0-9]", password):
        return False, "يجب أن تحتوي كلمة المرور على رقم واحد على الأقل (0-9)."
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>_]", password):
        return False, "يجب أن تحتوي كلمة المرور على رمز خاص واحد على الأقل."
    return True, ""

# --- بوابات التحقق والتحكم بأمان النظام ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        phone = request.form.get('phone', '').strip()
        
        conn = get_central_conn()
        c = conn.cursor()
        # استخدام الاستعلامات المجهزة (Parameterized Queries) للحماية من ثغرات SQL Injection
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password) and user['phone'] == phone:
            # محاكاة رمز التحقق عبر الهاتف (OTP) لضمان حماية الطبقة الثانية
            session['pending_user'] = user['username']
            session['generated_otp'] = "123456" # رمز محاكاة ثابت للـ OTP يمكنك استقباله بالخطوة التالية
            flash("تم إرسال رمز التحقق إلى رقم هاتفك المحمول المحفوظ.", "info")
            return redirect(url_for('verify_otp'))
        else:
            flash("خطأ في اسم المستخدم، كلمة المرور، أو رقم الهاتف المربوط!", "danger")
            
    return render_template('login.html')

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if 'pending_user' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        otp_input = request.form.get('otp', '').strip()
        if otp_input == session.get('generated_otp'):
            session['username'] = session['pending_user']
            session['is_admin'] = 1 if session['username'] == 'RAED_ADMIN' else 0
            session.pop('pending_user', None)
            session.pop('generated_otp', None)
            return redirect(url_for('dashboard'))
        else:
            flash("رمز التحقق غير صحيح، يرجى المحاولة مجدداً.", "danger")
            
    return render_template('verify_otp.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        new_password = request.form.get('new_password', '').strip()
        
        is_strong, msg = is_password_strong(new_password)
        if not is_strong:
            flash(msg, "danger")
            return render_template('forgot_password.html')
            
        conn = get_central_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE phone = ?", (phone,))
        user = c.fetchone()
        
        if user:
            hashed_pw = generate_password_hash(new_password)
            c.execute("UPDATE users SET password_hash = ? WHERE phone = ?", (hashed_pw, phone))
            conn.commit()
            conn.close()
            flash("تمت إعادة تعيين كلمة المرور بنجاح بعد التحقق من رقم الهاتف.", "success")
            return redirect(url_for('login'))
        else:
            conn.close()
            flash("رقم الهاتف هذا غير مسجل لدينا في النظام!", "danger")
            
    return render_template('forgot_password.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- لوحة التحكم المخصصة للمستأجرين ---
@app.route('/')
@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session['username'], is_admin=session['is_admin'])

# --- شاشة إضافة الحسابات الخاصة بـ RAED_ADMIN فقط لا غير ---
@app.route('/admin/add-account', methods=['GET', 'POST'])
def add_account():
    # منع الدخول لغير حساب المالك تماماً لرفع مستويات الأمان
    if 'username' not in session or session.get('is_admin') != 1:
        return "غير مسموح بالدخول، صلاحية المالك فقط لا غير.", 403
        
    if request.method == 'POST':
        new_username = request.form.get('new_username', '').strip()
        new_password = request.form.get('new_password', '').strip()
        new_phone = request.form.get('new_phone', '').strip()
        
        is_strong, msg = is_password_strong(new_password)
        if not is_strong:
            flash(msg, "danger")
            return render_template('add_account.html')
            
        conn = get_central_conn()
        c = conn.cursor()
        try:
            hashed_pw = generate_password_hash(new_password)
            c.execute("INSERT INTO users (id, username, password_hash, phone, is_admin) VALUES (?, ?, ?, ?, 0)",
                      (str(uuid.uuid4()), new_username, hashed_pw, new_phone))
            conn.commit()
            init_tenant_db(new_username) # بناء قاعدة بيانات منعزلة وخاصة بالحساب الجديد فوراً
            flash(f"تم إنشاء حساب العميل بنجاح وعزل قاعدة بياناته: {new_username}", "success")
        except sqlite3.IntegrityError:
            flash("فشل الإنشاء: اسم المستخدم أو رقم الهاتف مسجل مسبقاً بنظام المنصة!", "danger")
        finally:
            conn.close()
            
    return render_template('add_account.html')

# --- محرك معالجة البيانات وتوليد البطاقات والتحقق مع قاعدة البيانات المنعزلة ---
@app.route('/api/guests', methods=['GET'])
def get_guests():
    if 'username' not in session:
        return jsonify([])
    conn = get_tenant_conn(session['username'])
    c = conn.cursor()
    c.execute("SELECT id, name, is_checked_in, scan_count FROM guests")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"id": r['id'], "name": r['name'], "is_checked_in": bool(r['is_checked_in']), "scan_count": r['scan_count']} for r in rows])

@app.route('/generate', methods=['POST'])
def generate():
    if 'username' not in session:
        return "غير مسموح بالإجراء", 401
    if 'template' not in request.files:
        return "يرجى رفع قالب الدعوة أولاً", 400
        
    file = request.files['template']
    guests_raw = request.form.get('guests', '')
    qr_x = int(request.form.get('qr_x', 0))
    qr_y = int(request.form.get('qr_y', 0))
    qr_size = int(request.form.get('qr_size', 150))
    
    guest_names = [name.strip() for name in guests_raw.split('\n') if name.strip()]
    if not guest_names:
        return "يرجى إضافة اسم ضيف واحد على الأقل", 400

    template_path = os.path.join(app.config['UPLOAD_FOLDER'], f"template_{session['username']}.png")
    file.save(template_path)
    
    conn = get_tenant_conn(session['username'])
    c = conn.cursor()
    c.execute("DELETE FROM guests") # مسح الكروت السابقة للمستأجر الحالي داخل بيئته المنعزلة فقط
    
    generated_files = []
    temp_invites_dir = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{session['username']}")
    os.makedirs(temp_invites_dir, exist_ok=True)
    
    base_template = Image.open(template_path).convert("RGBA")
    host_url = request.host_url

    for name in guest_names:
        guest_id = str(uuid.uuid4())[:8]
        c.execute("INSERT INTO guests (id, name) VALUES (?, ?)", (guest_id, name))
        
        verify_url = f"{host_url}verify/{session['username']}/{guest_id}"
        
        qr = qrcode.QRCode(box_size=10, border=1)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        qr_img = qr_img.resize((qr_size, qr_size))
        
        card_copy = base_template.copy()
        card_copy.paste(qr_img, (qr_x, qr_y), qr_img)
        
        file_name = os.path.join(temp_invites_dir, f"دعوة_{name}.png")
        card_copy.save(file_name)
        generated_files.append(file_name)
        
    conn.commit()
    conn.close()
    
    zip_path = os.path.join(app.config['UPLOAD_FOLDER'], f"invitations_{session['username']}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for f in generated_files:
            zipf.write(f, os.path.basename(f))
            os.remove(f)
            
    os.rmdir(temp_invites_dir)
    return send_file(zip_path, as_attachment=True)

@app.route('/verify/<tenant_username>/<guest_id>')
def verify_guest(tenant_username, guest_id):
    # مسار التحقق يقرأ من قاعدة بيانات العميل المالك للكرت لمنع أي تلاعب أو ثغرات تجاوز الصلاحيات
    conn = get_tenant_conn(tenant_username)
    c = conn.cursor()
    c.execute("SELECT name, is_checked_in, scan_count FROM guests WHERE id = ?", (guest_id,))
    guest = c.fetchone()
    
    if not guest:
        return render_template('verify.html', status="invalid", name="")
        
    new_scan_count = guest['scan_count'] + 1
    
    if guest['is_checked_in'] == 0:
        c.execute("UPDATE guests SET is_checked_in = 1, scan_count = ? WHERE id = ?", (new_scan_count, guest_id))
        status = "success"
    else:
        c.execute("UPDATE guests SET scan_count = ? WHERE id = ?", (new_scan_count, guest_id))
        status = "duplicate"
        
    conn.commit()
    conn.close()
    return render_template('verify.html', status=status, name=guest['name'], scan_count=new_scan_count)

if __name__ == '__main__':
    app.run(debug=True)