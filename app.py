import os
import sqlite3
import uuid
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import pandas as pd
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage

app = Flask(__name__)

# Mengambil secret key dari Environment Variables Render
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
                
                # Ambil data kandidat beserta nama file foto kartu
                cursor.execute("SELECT reg_no, full_name, operator_type, whatsapp, education, major, reg_card, submitted_at FROM candidates WHERE session_id = ?", (s_id,))
                rows = cursor.fetchall()
                
                file_path = f"Data_Kandidat_{pt}_{dept}.xlsx".replace(" ", "_")
                
                # Membuat Excel workbook menggunakan openpyxl
                wb = Workbook()
                ws = wb.active
                ws.title = "Kandidat"
                
                # Header Kolom Excel
                headers = ['No Registrasi', 'Nama Lengkap', 'Operator', 'No WA Aktif', 'Pendidikan', 'Jurusan', 'Waktu Submit', 'Foto Kartu Registrasi']
                ws.append(headers)
                
                for idx, row in enumerate(rows, start=2):
                    reg_no, full_name, operator_type, whatsapp, education, major, reg_card, submitted_at = row
                    ws.append([reg_no, full_name, operator_type, whatsapp, education, major, submitted_at])
                    
                    if reg_card:
                        img_path = os.path.join(UPLOAD_FOLDER, reg_card)
                        if os.path.exists(img_path):
                            try:
                                img = XLImage(img_path)
                                img.width = 90
                                img.height = 90
                                ws.add_image(img, f'H{idx}')
                                ws.row_dimensions[idx].height = 75
                            except Exception as e:
                                print(f"Gagal menyisipkan gambar ke Excel: {e}")
                        else:
                            ws.cell(row=idx, column=8, value="[File Foto Tidak Ditemukan]")
                    else:
                        ws.cell(row=idx, column=8, value="[Tidak Ada Foto]")
                
                # Penyesuaian lebar kolom teks agar rapi
                for col in ws.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    ws.column_dimensions[col_letter].width = max(max_len + 4, 15)
                
                ws.column_dimensions['H'].width = 22
                wb.save(file_path)
                
                # --- BERSIHKAN DATA DARI SISTEM SETELAH DOWNLOAD ---
                # 1. Hapus file fisik foto dari folder uploads
                for row in rows:
                    reg_card = row[6]
                    if reg_card:
                        img_path = os.path.join(UPLOAD_FOLDER, reg_card)
                        if os.path.exists(img_path):
                            try:
                                os.remove(img_path)
                            except:
                                pass
                
                # 2. Hapus data kandidat dan sesi dari database SQLite
                cursor.execute("DELETE FROM candidates WHERE session_id = ?", (s_id,))
                cursor.execute("DELETE FROM sessions WHERE id = ?", (s_id,))
                conn.commit()
                conn.close()
                
                return send_file(file_path, as_attachment=True)

    active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE is_active = 1").fetchone()
    candidate_count = 0
    if active_session:
        candidate_count = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (active_session[0],)).fetchone()[0]
        
    conn.close()
    current_token = str(uuid.uuid4())[:8]
    
    return render_template('admin.html', active_session=active_session, candidate_count=candidate_count, token=current_token)

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
    active_session = cursor.execute("SELECT id, pt_name, department FROM sessions WHERE is_active = 1").fetchone()
    conn.close()
    
    if not active_session:
        return "<h3 style='text-align:center; margin-top:50px;'>Maaf, sesi rekrutmen ini telah ditutup oleh Admin atau telah berakhir.</h3>"
        
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
