import os
import sqlite3
import uuid
import zipfile
from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import qrcode
import arabic_reshaper
from bidi.algorithm import get_display

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

def get_font(custom_font_path, size):
    """تحميل الخط مع دعم للخطوط المرفوعة والنظام"""
    # أولوية: الخط المرفوع من المستخدم
    if custom_font_path and os.path.exists(custom_font_path):
        try:
            return ImageFont.truetype(custom_font_path, size)
        except Exception:
            pass

    # محاولة خطوط النظام الشائعة
    system_fonts = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/tahoma.ttf',
    ]
    for fp in system_fonts:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue

    # الخط الافتراضي كملاذ أخير
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()

def reshape_arabic(text):
    """إعادة تشكيل النص العربي لعرضه صحيحاً على الصور"""
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text

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
    qr_x     = int(request.form.get('qr_x', 0))
    qr_y     = int(request.form.get('qr_y', 0))
    qr_size  = int(request.form.get('qr_size', 150))

    # ── إعدادات اسم الضيف ──────────────────────────────────────────────
    show_name   = request.form.get('show_name', 'false') == 'true'
    name_x      = int(request.form.get('name_x', 400))
    name_y      = int(request.form.get('name_y', 300))
    font_size   = int(request.form.get('font_size', 40))
    font_color  = request.form.get('font_color', '#000000')
    name_anchor = request.form.get('name_anchor', 'center')  # center | right | left

    guest_names = [n.strip() for n in guests_raw.split('\n') if n.strip()]
    if not guest_names:
        return "يرجى إضافة اسم ضيف واحد على الأقل", 400

    # حفظ القالب
    template_path = os.path.join(app.config['UPLOAD_FOLDER'], 'current_template.png')
    file.save(template_path)

    # حفظ ملف الخط المرفوع (اختياري)
    custom_font_path = None
    if 'font_file' in request.files and request.files['font_file'].filename:
        font_file = request.files['font_file']
        custom_font_path = os.path.join(app.config['UPLOAD_FOLDER'], 'custom_font.ttf')
        font_file.save(custom_font_path)

    # تهيئة قاعدة البيانات
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM guests")

    generated_files = []
    temp_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'temp_invites')
    os.makedirs(temp_dir, exist_ok=True)

    base_template = Image.open(template_path).convert("RGBA")
    host_url = request.host_url

    # تحضير الخط مرة واحدة خارج الحلقة (أداء أفضل)
    font = get_font(custom_font_path, font_size) if show_name else None

    # تحويل لون HEX → RGB مرة واحدة
    if show_name:
        hex_color = font_color.lstrip('#')
        rgb_color = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    for name in guest_names:
        guest_id   = str(uuid.uuid4())[:8]
        c.execute("INSERT INTO guests (id, name) VALUES (?, ?)", (guest_id, name))

        verify_url = f"{host_url}verify/{guest_id}"

        # ── توليد QR Code ────────────────────────────────────────────────
        qr = qrcode.QRCode(box_size=10, border=1)
        qr.add_data(verify_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        qr_img = qr_img.resize((qr_size, qr_size))

        card_copy = base_template.copy()
        card_copy.paste(qr_img, (qr_x, qr_y), qr_img)

        # ── كتابة اسم الضيف ─────────────────────────────────────────────
        if show_name and font:
            draw        = ImageDraw.Draw(card_copy)
            display_name = reshape_arabic(name)
            bbox        = draw.textbbox((0, 0), display_name, font=font)
            text_width  = bbox[2] - bbox[0]

            if name_anchor == 'center':
                text_x = name_x - text_width // 2
            elif name_anchor == 'right':
                text_x = name_x - text_width
            else:  # left
                text_x = name_x

            draw.text((text_x, name_y), display_name, font=font, fill=rgb_color)

        out_path = os.path.join(temp_dir, f"دعوة_{name}.png")
        card_copy.save(out_path)
        generated_files.append(out_path)

    conn.commit()
    conn.close()

    # ضغط الكروت في ZIP
    zip_path = os.path.join(app.config['UPLOAD_FOLDER'], 'dawaty_invitations.zip')
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for f in generated_files:
            zipf.write(f, os.path.basename(f))
            os.remove(f)

    os.rmdir(temp_dir)
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
        c.execute("UPDATE guests SET is_checked_in = 1, scan_count = ? WHERE id = ?", (new_scan_count, guest_id))
        status = "success"
    else:
        c.execute("UPDATE guests SET scan_count = ? WHERE id = ?", (new_scan_count, guest_id))
        status = "duplicate"

    conn.commit()
    conn.close()
    return render_template('verify.html', status=status, name=name, scan_count=new_scan_count)

if __name__ == '__main__':
    app.run(debug=True)
