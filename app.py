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
            session_token TEXT,
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
    conn.commit()
    conn.close()

init_db()

@app.before_request
def check_midnight_reset():
    if 'login_time' in session:
        if session['login_time'] != datetime.date.today().isoformat():
            session.clear()

# Rute khusus untuk pencegah sleep (dipakai UptimeRobot)
@app.route('/ping')
def ping():
    return "OK", 200

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
            session_token = str(uuid.uuid4())[:8]
            
            cursor.execute("UPDATE sessions SET is_active = 0")
            cursor.execute("INSERT INTO sessions (pt_name, department, session_token, is_active, created_at) VALUES (?, ?, ?, 1, ?)", (pt, dept, session_token, now))
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
                
                df = pd.read_sql_query(f"SELECT reg_no AS 'No Registrasi', full_name AS 'Nama Lengkap', operator_type AS 'Operator', whatsapp AS 'No WA Aktif', education AS 'Pendidikan', major AS 'Jurusan', reg_card AS 'Nama File Foto', submitted_at AS 'Waktu Submit' FROM candidates WHERE session_id = {s_id}", conn)
                
                file_path = f"Data_Kandidat_{pt}_{dept}.xlsx".replace(" ", "_")
                
                with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Kandidat')
                    worksheet = writer.sheets['Kandidat']
                    for col in worksheet.columns:
                        max_len = max(len(str(cell.value or '')) for cell in col)
                        col_letter = col[0].column_letter
                        worksheet.column_dimensions[col_letter].width = max(max_len + 5, 18)
                
                # Hapus file foto fisik dari folder uploads
                cursor.execute("SELECT reg_card FROM candidates WHERE session_id = ?", (s_id,))
                rows = cursor.fetchall()
                for row in rows:
                    if row[0]:
                        img_path = os.path.join(UPLOAD_FOLDER, row[0])
                        if os.path.exists(img_path):
                            try: os.remove(img_path)
                            except: pass
                
                # Hapus data dari database setelah di-download
                cursor.execute("DELETE FROM candidates WHERE session_id = ?", (s_id,))
                cursor.execute("DELETE FROM sessions WHERE id = ?", (s_id,))
                conn.commit()
                conn.close()
                
                return send_file(file_path, as_attachment=True)

    active_session = cursor.execute("SELECT id, pt_name, department, session_token FROM sessions WHERE is_active = 1").fetchone()
    candidate_count = 0
    if active_session:
        candidate_count = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (active_session[0],)).fetchone()[0]
        
    conn.close()
    return render_template('admin.html', active_session=active_session, candidate_count=candidate_count)

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
    
    active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE session_token = ? AND is_active = 1", (token,)).fetchone()
    conn.close()
    
    if not active_session:
        return "<h3 style='text-align:center; margin-top:50px; color:red;'>Maaf, sesi rekrutmen ini telah ditutup oleh Admin atau link tidak valid.</h3>"
        
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
        cursor.execute("INSERT INTO candidates (session_id, reg_no, full_name, operator_type, whatsapp, education, major, reg_card, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (active_session[0], reg_no, full_name, operator_type, whatsapp, education, major, filename, submitted_at))
        conn.commit()
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:green;'>Data Anda berhasil dikirim! Terima kasih.</h3>"
        
    return render_template('form.html', session_info=active_session)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
