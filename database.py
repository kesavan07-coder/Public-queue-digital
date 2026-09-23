import sqlite3
import datetime

DB_NAME = 'queue.db'

def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def init_db():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_number TEXT UNIQUE,
                category TEXT DEFAULT 'OPD', 
                status TEXT DEFAULT 'waiting', 
                created_at TIMESTAMP,
                completed_at TIMESTAMP,
                counter_number INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS counters (
                counter_number INTEGER PRIMARY KEY,
                avg_service_time REAL DEFAULT 120.0,
                total_served INTEGER DEFAULT 0,
                last_call_at TIMESTAMP
            )
        ''')
        # Create token sequences to prevent duplicates
        c.execute('''
            CREATE TABLE IF NOT EXISTS token_sequences (
                category TEXT PRIMARY KEY,
                last_num INTEGER DEFAULT 0
            )
        ''')
        # Pre-populate counters and sequences
        for i in range(1, 4):
            c.execute("INSERT OR IGNORE INTO counters (counter_number) VALUES (?)", (i,))
        for cat in ['OPD', 'Emergency', 'Lab']:
            c.execute("INSERT OR IGNORE INTO token_sequences (category) VALUES (?)", (cat,))
        conn.commit()
    finally:
        conn.close()

def get_average_service_time():
    conn = get_db()
    c = conn.cursor()
    # Simplified query using created_at and completed_at
    c.execute("""
        SELECT created_at, completed_at 
        FROM tokens 
        WHERE status = 'completed' 
        AND created_at IS NOT NULL 
        AND completed_at IS NOT NULL 
        ORDER BY id DESC LIMIT 10
    """)
    rows = c.fetchall()
    conn.close()
    times = []
    for row in rows:
        try:
            created_dt = datetime.datetime.fromisoformat(row['created_at'])
            completed_dt = datetime.datetime.fromisoformat(row['completed_at'])
            diff = (completed_dt - created_dt).total_seconds()
            if diff > 0:
                times.append(diff)
        except Exception:
            continue
    if times:
        return sum(times) / len(times)
    return 120  # fallback: 2 minutes

def generate_token(category='OPD'):
    conn = get_db()
    try:
        c = conn.cursor()
        
        # Atomically increment the sequence for this category
        c.execute("UPDATE token_sequences SET last_num = last_num + 1 WHERE category = ?", (category,))
        c.execute("SELECT last_num FROM token_sequences WHERE category = ?", (category,))
        count = c.fetchone()[0]
        
        prefix = category[0].upper() # O for OPD, E for Emergency, L for Lab
        token_num = f"{prefix}-{count:03d}"
        
        c.execute('''
            INSERT INTO tokens (token_number, category, status, created_at)
            VALUES (?, ?, 'waiting', CURRENT_TIMESTAMP)
        ''', (token_num, category))
        
        conn.commit()
        token_id = c.lastrowid
        # Calculate local expiration time to send back instantly
        c.execute("SELECT datetime(created_at, '+2 hours', 'localtime') FROM tokens WHERE id = ?", (token_id,))
        expires_at = c.fetchone()[0]
        return {'id': token_id, 'token_number': token_num, 'status': 'waiting', 'category': category, 'expires_at': expires_at}
    finally:
        conn.close()

def expire_old_tokens(hours=2):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"UPDATE tokens SET status = 'expired' WHERE status = 'waiting' AND created_at < datetime('now', '-{hours} hours')")
    conn.commit()
    conn.close()

def get_waiting_tokens():
    expire_old_tokens(hours=2) # Clean up old tokens first
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT *, datetime(created_at, '+2 hours', 'localtime') as expires_at FROM tokens WHERE status = 'waiting' ORDER BY id ASC")
        rows = c.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_serving_tokens():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM tokens WHERE status IN ('calling', 'serving') ORDER BY id ASC")
        rows = c.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def call_next(counter_number):
    conn = get_db()
    c = conn.cursor()
    
    # Mark previous as completed and update counter analytics
    c.execute("SELECT created_at FROM tokens WHERE counter_number = ? AND status IN ('calling', 'serving') LIMIT 1", (counter_number,))
    current_token_start = c.fetchone()
    if current_token_start:
        start_time = datetime.datetime.fromisoformat(current_token_start[0])
        now = datetime.datetime.now()
        duration = (now - start_time).total_seconds()
        
        # Update counter's average performance logic (moving average)
        c.execute("SELECT avg_service_time, total_served FROM counters WHERE counter_number = ?", (counter_number,))
        perf = c.fetchone()
        if perf:
            old_avg = perf[0]
            count = perf[1]
            new_count = count + 1
            new_avg = ((old_avg * count) + duration) / new_count
            c.execute("UPDATE counters SET avg_service_time = ?, total_served = ?, last_call_at = ? WHERE counter_number = ?", 
                      (new_avg, new_count, now.isoformat(), counter_number))

    c.execute("UPDATE tokens SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE counter_number = ? AND status IN ('calling', 'serving')", (counter_number,))
    
    # Find next waiting - PRIORITIZE Emergency category
    c.execute("""
        SELECT * FROM tokens 
        WHERE status = 'waiting' 
        ORDER BY 
            CASE WHEN category = 'Emergency' THEN 0 ELSE 1 END,
            id ASC 
        LIMIT 1
    """)
    row = c.fetchone()
    
    if row:
        c.execute("UPDATE tokens SET status = 'calling', counter_number = ? WHERE id = ?", (counter_number, row['id']))
        # Fetch the updated row to return
        c.execute("SELECT * FROM tokens WHERE id = ?", (row['id'],))
        updated_row = c.fetchone()
        conn.commit()
        conn.close()
        return dict(updated_row)
    
    conn.commit()
    conn.close()
    return None

def get_counter_stats():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM counters")
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_recommended_counter():
    conn = get_db()
    c = conn.cursor()
    # Find the counter with the best (lowest) average service time
    c.execute("SELECT counter_number FROM counters ORDER BY avg_service_time ASC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1

def mark_no_show(counter_number):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tokens SET status = 'no_show', completed_at = CURRENT_TIMESTAMP WHERE counter_number = ? AND status IN ('calling', 'serving')", (counter_number,))
    conn.commit()
    conn.close()

def get_analytics_summary():
    conn = get_db()
    c = conn.cursor()
    
    # Total assigned today
    c.execute("SELECT count(*) as total FROM tokens WHERE date(created_at) = date('now', 'localtime')")
    total_today = c.fetchone()['total']
    
    # Category splits
    c.execute("SELECT category, count(*) as count FROM tokens WHERE date(created_at) = date('now', 'localtime') GROUP BY category")
    by_category = {row['category']: row['count'] for row in c.fetchall()}
    
    # Status splits (Waiting vs Completed vs No-Show)
    c.execute("SELECT status, count(*) as count FROM tokens WHERE date(created_at) = date('now', 'localtime') GROUP BY status")
    by_status = {row['status']: row['count'] for row in c.fetchall()}
    
    conn.close()
    return {
        'total_today': total_today,
        'by_category': by_category,
        'by_status': by_status
    }
