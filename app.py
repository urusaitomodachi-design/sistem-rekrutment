import os
import sqlite3
import uuid
import datetime
import zipfile
import io
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import pandas as pd
from werkzeug.utils import secure_filename
from PIL import Image

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
            nik TEXT,
            full_name TEXT,
            whatsapp TEXT,
            education TEXT,
            major TEXT,
            birth_info TEXT,
            reg_card TEXT
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
    error_message = None
    
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
            
        elif action == 'download_all':
            active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE is_active = 1").fetchone()
            
            if not active_session:
                conn.close()
                error_message = "Tidak ada sesi rekrutmen aktif saat ini, download dibatalkan."
                return render_template('admin.html', active_session=None, candidate_count=0, error_message=error_message)
            
            s_id, pt, dept = active_session
            
            candidate_check = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (s_id,)).fetchone()[0]
            if candidate_check == 0:
                conn.close()
                error_message = "Belum ada data kandidat yang masuk pada sesi ini."
                return render_template('admin.html', active_session=active_session, candidate_count=0, error_message=error_message)
            
            # Kolom Excel dengan No WA Aktif dipindah ke posisi paling akhir
            df = pd.read_sql_query(f"""
                SELECT 
                    reg_no AS 'No Registrasi', 
                    nik AS 'NIK KTP', 
                    full_name AS 'Nama Lengkap', 
                    education AS 'Pendidikan', 
                    major AS 'Jurusan', 
                    birth_info AS 'Tempat, Tanggal Lahir', 
                    whatsapp AS 'No WA Aktif' 
                FROM candidates WHERE session_id = {s_id}
            """, conn)
            
            excel_filename = f"Data_Kandidat_{pt}_{dept}.xlsx".replace(" ", "_")
            with pd.ExcelWriter(excel_filename, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Kandidat')
                worksheet = writer.sheets['Kandidat']
                for col in worksheet.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = max(max_len + 5, 18)
            
            cursor.execute("SELECT reg_card FROM candidates WHERE session_id = ?", (s_id,))
            rows = cursor.fetchall()
            
            zip_filename = f"Rekrutmen_{pt}_{dept}.zip".replace(" ", "_")
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(excel_filename, excel_filename)
                for row in rows:
                    if row[0]:
                        img_path = os.path.join(UPLOAD_FOLDER, row[0])
                        if os.path.exists(img_path):
                            zf.write(img_path, os.path.join("Foto_Kandidat", row[0]))
            
            memory_file.seek(0)
            
            if os.path.exists(excel_filename):
                try: os.remove(excel_filename)
                except: pass
            
            for row in rows:
                if row[0]:
                    img_path = os.path.join(UPLOAD_FOLDER, row[0])
                    if os.path.exists(img_path):
                        try: os.remove(img_path)
                        except: pass
            
            cursor.execute("DELETE FROM candidates WHERE session_id = ?", (s_id,))
            cursor.execute("DELETE FROM sessions WHERE id = ?", (s_id,))
            conn.commit()
            conn.close()
            
            return send_file(memory_file, download_name=zip_filename, as_attachment=True)

    active_session = cursor.execute("SELECT id, pt_name, department, session_token FROM sessions WHERE is_active = 1").fetchone()
    candidate_count = 0
    if active_session:
        candidate_count = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (active_session[0],)).fetchone()[0]
        
    conn.close()
    return render_template('admin.html', active_session=active_session, candidate_count=candidate_count, error_message=error_message)

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

@app.route('/apply/<token>', methods=['GET', 'POST'])
def apply(token):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE session_token = ? AND is_active = 1", (token,)).fetchone()
    conn.close()
    
    if not active_session:
        return "<h3 style='text-align:center; margin-top:50px; color:red;'>Maaf, sesi rekrutmen ini telah ditutup atau link sudah tidak berlaku (telah di-reset oleh Admin).</h3>"
        
    if request.method == 'POST':
        reg_no = request.form.get('reg_no', '').strip().upper()
        nik = request.form.get('nik', '').strip().upper()
        full_name = request.form.get('full_name', '').strip().upper()
        whatsapp = request.form.get('whatsapp', '').strip().upper()
        education = request.form.get('education', '').strip().upper()
        major = request.form.get('major', '').strip().upper()
        
        birth_place = request.form.get('birth_place', '').strip().upper()
        birth_day = request.form.get('birth_day', '').strip()
        birth_month = request.form.get('birth_month', '').strip().capitalize()
        birth_year = request.form.get('birth_year', '').strip()
        
        birth_info = f"{birth_place}, {birth_day} {birth_month} {birth_year}".strip()
        
        file = request.files.get('reg_card')
        filename = ""
        if file and file.filename != '':
            safe_name = secure_filename(file.filename)
            filename = f"{reg_no}_{int(datetime.datetime.now().timestamp())}_{safe_name}"
            save_path = os.path.join(UPLOAD_FOLDER, filename)
            
            try:
                img = Image.open(file)
                img = img.convert('RGB')
                img.thumbnail((1200, 1200))
                img.save(save_path, 'JPEG', quality=80)
            except Exception:
                file.save(save_path)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Cek apakah No Registrasi ATAU NIK KTP sudah ada pada sesi aktif
        existing = cursor.execute('''
            SELECT id FROM candidates 
            WHERE session_id = ? AND (reg_no = ? OR nik = ?)
        ''', (active_session[0], reg_no, nik)).fetchone()
        
        if existing:
            # Jika ada yang sama, timpa/update data lama
            cursor.execute('''
                UPDATE candidates SET
                    reg_no = ?,
                    nik = ?,
                    full_name = ?,
                    whatsapp = ?,
                    education = ?,
                    major = ?,
                    birth_info = ?,
                    reg_card = COALESCE(NULLIF(?, ''), reg_card)
                WHERE id = ?
            ''', (reg_no, nik, full_name, whatsapp, education, major, birth_info, filename, existing[0]))
        else:
            # Jika belum ada, masukkan sebagai data baru
            cursor.execute('''
                INSERT INTO candidates (session_id, reg_no, nik, full_name, whatsapp, education, major, birth_info, reg_card)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (active_session[0], reg_no, nik, full_name, whatsapp, education, major, birth_info, filename))
        
        conn.commit()
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:green;'>Data Anda berhasil dikirim! Terima kasih.</h3>"
        
    return render_template('form.html', session_info=active_session)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
