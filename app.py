import os
import sqlite3
import uuid
import datetime
import zipfile
import io
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import pandas as pd
from werkzeug.utils import secure_filename

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY", "palabapakkau")

DB_NAME = "rekrutmen.db"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pt_name TEXT,
            department TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            reg_no TEXT,
            full_name TEXT,
            operator_type TEXT,
            whatsapp TEXT,
            education TEXT,
            major TEXT,
            reg_card TEXT,
            submitted_at TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT,
            session_id INTEGER,
            expires_at TEXT,
            is_used INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.before_request
def check_midnight_reset():
    if 'login_time' in session:
        if session['login_time'] != datetime.date.today().isoformat():
            session.clear()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        admin_password = os.environ.get("ADMIN_PASSWORD", "kontol123")
        
        if password == admin_password:
            session['logged_in'] = True
            session['login_time'] = datetime.date.today().isoformat()
            return redirect(url_for('admin_dashboard'))
        return render_template('login.html', error="Password Salah!")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin', methods=['GET', 'POST'])
def admin_dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'start':
            pt = request.form.get('pt_name').upper()
            dept = request.form.get('department').upper()
            now = datetime.datetime.now().isoformat()
            
            cursor.execute("UPDATE sessions SET is_active = 0")
            cursor.execute("INSERT INTO sessions (pt_name, department, is_active, created_at) VALUES (?, ?, 1, ?)", (pt, dept, now))
            conn.commit()
            
        elif action == 'stop':
            cursor.execute("UPDATE sessions SET is_active = 0")
            conn.commit()
            
        elif action == 'download':
            active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE is_active = 1").fetchone()
            if not active_session:
                active_session = cursor.execute("SELECT id, pt_name, department FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
            
            if active_session:
                s_id, pt, dept = active_session
                
                # Ekspor data teks murni ke Excel (Tanpa Gambar di dalam sel)
                df = pd.read_sql_query(f"SELECT reg_no AS 'No Registrasi', full_name AS 'Nama Lengkap', operator_type AS 'Operator', whatsapp AS 'No WA Aktif', education AS 'Pendidikan', major AS 'Jurusan', reg_card AS 'Nama File Foto', submitted_at AS 'Waktu Submit' FROM candidates WHERE session_id = {s_id}", conn)
                
                file_path = f"Data_Kandidat_{pt}_{dept}.xlsx".replace(" ", "_")
                df.to_excel(file_path, index=False, sheet_name='Kandidat')
                
                # Bersihkan database dan file upload setelah didownload
                cursor.execute("SELECT reg_card FROM candidates WHERE session_id = ?", (s_id,))
                rows = cursor.fetchall()
                for row in rows:
                    if row[0]:
                        img_path = os.path.join(UPLOAD_FOLDER, row[0])
                        if os.path.exists(img_path):
                            try: os.remove(img_path)
                            except: pass
                
                cursor.execute("DELETE FROM candidates WHERE session_id = ?", (s_id,))
                cursor.execute("DELETE FROM tokens WHERE session_id = ?", (s_id,))
                cursor.execute("DELETE FROM sessions WHERE id = ?", (s_id,))
                conn.commit()
                conn.close()
                
                return send_file(file_path, as_attachment=True)

    active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE is_active = 1").fetchone()
    candidate_count = 0
    if active_session:
        candidate_count = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (active_session[0],)).fetchone()[0]
        
    conn.close()
    
    return render_template('admin.html', active_session=active_session, candidate_count=candidate_count)

# API untuk membuat token barcode eksklusif (Masa aktif 2 menit)
@app.route('/api/generate-token')
def generate_token():
    if not session.get('logged_in'):
        return {"error": "Unauthorized"}, 403
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    active_session = cursor.execute("SELECT id FROM sessions WHERE is_active = 1").fetchone()
    if not active_session:
        conn.close()
        return {"error": "No active session"}, 400
    
    session_id = active_session[0]
    token = str(uuid.uuid4())[:8]
    # Token berlaku tepat 2 menit
    expires_at = (datetime.datetime.now() + datetime.timedelta(minutes=2, seconds=5)).isoformat()
    
    cursor.execute("INSERT INTO tokens (token, session_id, expires_at, is_used) VALUES (?, ?, ?, 0)", 
                   (token, session_id, expires_at))
    conn.commit()
    conn.close()
    return {"token": token}

@app.route('/api/candidate-count')
def candidate_count_api():
    if not session.get('logged_in'):
        return {"count": 0}
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    active_session = cursor.execute("SELECT id FROM sessions WHERE is_active = 1").fetchone()
    
    count = 0
    if active_session:
        count = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (active_session[0],)).fetchone()[0]
        
    conn.close()
    return {"count": count}

# Endpoint Download ZIP Foto
@app.route('/admin/download-images')
def download_images():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(UPLOAD_FOLDER):
            for root, dirs, files in os.walk(UPLOAD_FOLDER):
                for file in files:
                    zf.write(os.path.join(root, file), file)
    memory_file.seek(0)
    return send_file(memory_file, download_name='Foto_Kartu_Registrasi.zip', as_attachment=True)

@app.route('/apply/<token>', methods=['GET', 'POST'])
def apply(token):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Periksa validitas token
    token_data = cursor.execute("SELECT id, session_id, expires_at, is_used FROM tokens WHERE token = ?", (token,)).fetchone()
    
    if not token_data:
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:red;'>Maaf, link QR code tidak valid atau tidak dikenali.</h3>"
    
    t_id, session_id, expires_at, is_used = token_data
    
    # Cek apakah token sudah pernah digunakan (sekali pakai)
    if is_used == 1:
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:orange;'>Link ini sudah pernah digunakan untuk mengirim data dan sekarang sudah tidak dapat diakses kembali.</h3>"
    
    # Cek apakah token sudah kedaluwarsa (> 2 menit)
    exp_time = datetime.datetime.fromisoformat(expires_at)
    if datetime.datetime.now() > exp_time:
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:red;'>Maaf, link QR code ini telah kedaluwarsa (lebih dari 2 menit). Silakan gunakan QR code terbaru di panel admin.</h3>"
    
    active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE id = ? AND is_active = 1", (session_id,)).fetchone()
    conn.close()
    
    if not active_session:
        return "<h3 style='text-align:center; margin-top:50px;'>Maaf, sesi rekrutmen ini telah ditutup oleh Admin.</h3>"
        
    if request.method == 'POST':
        reg_no = request.form.get('reg_no', '').upper()
        full_name = request.form.get('full_name', '').upper()
        operator_type = request.form.get('operator_type', '').upper()
        whatsapp = request.form.get('whatsapp', '').upper()
        education = request.form.get('education', '').upper()
        major = request.form.get('major', '').upper()
        submitted_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        file = request.files.get('reg_card')
        filename = ""
        if file and file.filename != '':
            safe_name = secure_filename(file.filename)
            filename = f"{reg_no}_{int(datetime.datetime.now().timestamp())}_{safe_name}"
            file.save(os.path.join(UPLOAD_FOLDER, filename))
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # Simpan data kandidat
        cursor.execute("INSERT INTO candidates (session_id, reg_no, full_name, operator_type, whatsapp, education, major, reg_card, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (active_session[0], reg_no, full_name, operator_type, whatsapp, education, major, filename, submitted_at))
        # Kunci token agar tidak bisa dipakai lagi (Sekali Pakai)
        cursor.execute("UPDATE tokens SET is_used = 1 WHERE id = ?", (t_id,))
        conn.commit()
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:green;'>Data Anda berhasil dikirim! Terima kasih. Akses ke halaman ini telah ditutup.</h3>"
        
    return render_template('form.html', session_info=active_session)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
