import sqlite3
import datetime
from flask import g

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            'users.db',
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

def init_db():
    db = get_db()
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            user_agent TEXT,
            fingerprint TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    db.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            current_page TEXT DEFAULT 'login',
            email TEXT,
            ip_address TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    db.commit()

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def log_user(email, ip_address, user_agent, fingerprint):
    db = get_db()
    cursor = db.execute(
        'INSERT INTO users (email, ip_address, user_agent, fingerprint) VALUES (?, ?, ?, ?)',
        (email, ip_address, user_agent, fingerprint)
    )
    db.commit()
    return cursor.lastrowid

def create_session(session_id, ip_address):
    db = get_db()
    db.execute(
        'INSERT OR REPLACE INTO sessions (session_id, ip_address, created_at, last_activity) VALUES (?, ?, ?, ?)',
        (session_id, ip_address, datetime.datetime.now(), datetime.datetime.now())
    )
    db.commit()

def update_session_page(session_id, page, email=None):
    db = get_db()
    if email:
        db.execute(
            'UPDATE sessions SET current_page = ?, email = ?, last_activity = ? WHERE session_id = ?',
            (page, email, datetime.datetime.now(), session_id)
        )
    else:
        db.execute(
            'UPDATE sessions SET current_page = ?, last_activity = ? WHERE session_id = ?',
            (page, datetime.datetime.now(), session_id)
        )
    db.commit()

def get_session(session_id):
    db = get_db()
    session = db.execute(
        'SELECT * FROM sessions WHERE session_id = ?',
        (session_id,)
    ).fetchone()
    return session

def cleanup_old_sessions(hours=24):
    db = get_db()
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    db.execute('DELETE FROM sessions WHERE last_activity < ?', (cutoff,))
    db.commit()