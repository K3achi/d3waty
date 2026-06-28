import os
import sqlite3
import uuid
import zipfile
from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image
import qrcode

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

DB_PATH = 'dawaty.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS guests 
                 (id TEXT PRIMARY KEY, name TEXT, is_checked_in INTEGER DEFAULT 0, scan_count INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/guests', methods=['GET'])
def get_guests():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, is_checked_in, scan_count FROM guests")
    rows = c.fetchall()
    conn.close()
    
    guests = [{"id": r[0], "name": r[1], "is_checked_in": bool(r[2]), "scan_count": r[3]} for r in rows]
    return jsonify(guests)

@app.route('/generate', methods=['POST'])
def generate():
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

    # حفظ القالب مؤقتاً
    template_path = os.path.join(app.config['UPLOAD_FOLDER'], 'current_template.png')
    file.save(template_path)
    
    # تفريغ قاعدة البيانات القديمة لبدء مناسبة جديدة (SaaS Simulation)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM guests")
    
    generated_files = []
    temp_invites_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'temp_invites')
    os.makedirs(temp_invites_dir, exist_ok=True)
    
    base_template = Image.open(template_path).convert("RGBA")
    host_url = request.host_url # جلب رابط الموقع الحالي ديناميكياً سواء محلي أو مرفوع

    for name in guest_names:
        guest_id = str(uuid.uuid4())[:8] # توليد معرف فريد قصير لكل ضيف
        c.execute("INSERT INTO guests (id, name) VALUES (?, ?)", (guest_id, name))
        
        # رابط التحقق التابع للمنصة
        verify_url = f"{host_url}verify/{guest_id}"
        
        # توليد الباركود
        qr = qrcode.QRCode(box_size=10, border=1)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        qr_img = qr_img.resize((qr_size, qr_size))
        
        # دمج الباركود مع الكرت
        card_copy = base_template.copy()
        card_copy.paste(qr_img, (qr_x, qr_y), qr_img)
        
        file_name = os.path.join(temp_invites_dir, f"دعوة_{name}.png")
        card_copy.save(file_name)
        generated_files.append(file_name)
        
    conn.commit()
    conn.close()
    
    # ضغط الكروت في ملف ZIP
    zip_path = os.path.join(app.config['UPLOAD_FOLDER'], 'dawaty_invitations.zip')
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for f in generated_files:
            zipf.write(f, os.path.basename(f))
            os.remove(f)
            
    os.rmdir(temp_invites_dir)
    return send_file(zip_path, as_attachment=True)

@app.route('/verify/<guest_id>')
def verify_guest(guest_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name, is_checked_in, scan_count FROM guests WHERE id = ?", (guest_id,))
    guest = c.fetchone()
    
    if not guest:
        return render_template('verify.html', status="invalid", name="")
        
    name, is_checked_in, scan_count = guest
    new_scan_count = scan_count + 1
    
    if is_checked_in == 0:
        # الدخول الأول والشرعي للضيف
        c.execute("UPDATE guests SET is_checked_in = 1, scan_count = ? WHERE id = ?", (new_scan_count, guest_id))
        status = "success"
    else:
        # تم مسحه مسبقاً (محاولة تزوير أو تكرار دخول)
        c.execute("UPDATE guests SET scan_count = ? WHERE id = ?", (new_scan_count, guest_id))
        status = "duplicate"
        
    conn.commit()
    conn.close()
    return render_template('verify.html', status=status, name=name, scan_count=new_scan_count)

if __name__ == '__main__':
    app.run(debug=True)