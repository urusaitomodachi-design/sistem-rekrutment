import os
import sqlite3
import uuid
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, send_file
import pandas as pd

app = Flask(__name__)

# Mengambil secret key dari Environment Variables Render (aman dari kebocoran kode)
app.secret_key = os.environ.get("SECRET_KEY", "palabapakkau")

DB_NAME = "rekrutmen.db"

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
        
        # Mengambil password admin dari Environment Variables Render
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
                df = pd.read_sql_query(f"SELECT reg_no AS 'No Registrasi', full_name AS 'Nama Lengkap', operator_type AS 'Operator', whatsapp AS 'No WA Aktif', submitted_at AS 'Waktu Submit' FROM candidates WHERE session_id = {s_id}", conn)
                
                file_path = f"Data_Kandidat_{pt}_{dept}.xlsx".replace(" ", "_")
                df.to_excel(file_path, index=False)
                
                cursor.execute("UPDATE sessions SET is_active = 0 WHERE id = ?", (s_id,))
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
        submitted_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO candidates (session_id, reg_no, full_name, operator_type, whatsapp, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (active_session[0], reg_no, full_name, operator_type, whatsapp, submitted_at))
        conn.commit()
        conn.close()
        return "<h3 style='text-align:center; margin-top:50px; color:green;'>Data Anda berhasil dikirim! Terima kasih.</h3>"
        
    return render_template('form.html', session_info=active_session)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
