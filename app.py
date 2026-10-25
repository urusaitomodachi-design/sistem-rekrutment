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

# Rute pencegah sleep (untuk Cron-job.org / UptimeRobot)
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
                active_session = None
                candidate_count = 0
                error_message = "Tidak ada sesi rekrutmen aktif saat ini, download dibatalkan."
                return render_template('admin.html', active_session=active_session, candidate_count=candidate_count, error_message=error_message)
            
            s_id, pt, dept = active_session
            
            candidate_check = cursor.execute("SELECT COUNT(*) FROM candidates WHERE session_id = ?", (s_id,)).fetchone()[0]
            if candidate_check == 0:
                conn.close()
                candidate_count = 0
                error_message = "Belum ada data kandidat yang masuk pada sesi ini, sehingga file Excel dan foto belum dapat diunduh."
                return render_template('admin.html', active_session=active_session, candidate_count=candidate_count, error_message=error_message)
            
            # 1. Buat file Excel di server sementara
            df = pd.read_sql_query(f"SELECT reg_no AS 'No Registrasi', full_name AS 'Nama Lengkap', operator_type AS 'Operator', whatsapp AS 'No WA Aktif', education AS 'Pendidikan', major AS 'Jurusan', submitted_at AS 'Waktu Submit' FROM candidates WHERE session_id = {s_id}", conn)
            
            excel_filename = f"Data_Kandidat_{pt}_{dept}.xlsx".replace(" ", "_")
            with pd.ExcelWriter(excel_filename, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Kandidat')
                worksheet = writer.sheets['Kandidat']
                for col in worksheet.columns:
                    max_len = max(len(str(cell.value or '')) for cell in col)
                    col_letter = col[0].column_letter
                    worksheet.column_dimensions[col_letter].width = max(max_len + 5, 18)
            
            # 2. Ambil daftar nama file foto kandidat
            cursor.execute("SELECT reg_card FROM candidates WHERE session_id = ?", (s_id,))
            rows = cursor.fetchall()
            
            # 3. Bungkus Excel & Foto ke dalam memori ZIP
            zip_filename = f"Rekrutmen_{pt}_{dept}.zip".replace(" ", "_")
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Masukkan file Excel ke root zip
                zf.write(excel_filename, excel_filename)
                
                # Masukkan foto-foto ke dalam folder khusus di dalam zip
                for row in rows:
                    if row[0]:
                        img_path = os.path.join(UPLOAD_FOLDER, row[0])
                        if os.path.exists(img_path):
                            zf.write(img_path, os.path.join("Foto_Kandidat", row[0]))
            
            memory_file.seek(0)
            
            # 4. Hapus file Excel sementara di server
            if os.path.exists(excel_filename):
                try: os.remove(excel_filename)
                except: pass
            
            # 5. Hapus file foto fisik dari folder uploads
            for row in rows:
                if row[0]:
                    img_path = os.path.join(UPLOAD_FOLDER, row[0])
                    if os.path.exists(img_path):
                        try: os.remove(img_path)
                        except: pass
            
            # 6. Hapus data dari database agar bersih untuk sesi berikutnya
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
