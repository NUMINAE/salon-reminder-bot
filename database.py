import sqlite3
from datetime import datetime, timedelta

DB_NAME = "appointments.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            client_contact TEXT,
            service TEXT,
            appointment_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reminder_sent INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_appointment(client_name, client_contact, service, appointment_time):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO appointments
        (client_name, client_contact, service, appointment_time, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (client_name, client_contact, service,
          appointment_time.isoformat(), datetime.now().isoformat()))
    conn.commit()
    appointment_id = c.lastrowid
    conn.close()
    return appointment_id

def get_appointments_for_day(target_date):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)
    c.execute("""
        SELECT id, client_name, client_contact, service, appointment_time, status
        FROM appointments
        WHERE appointment_time >= ? AND appointment_time < ?
        ORDER BY appointment_time
    """, (start.isoformat(), end.isoformat()))
    rows = c.fetchall()
    conn.close()
    return rows

def get_upcoming(limit=20):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT id, client_name, client_contact, service, appointment_time, status
        FROM appointments
        WHERE appointment_time >= ?
        ORDER BY appointment_time
        LIMIT ?
    """, (datetime.now().isoformat(), limit))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_appointment(appointment_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM appointments WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()

def get_appointments_needing_reminder():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now = datetime.now()
    cutoff = now + timedelta(hours=24)
    c.execute("""
        SELECT id, client_name, client_contact, service, appointment_time
        FROM appointments
        WHERE reminder_sent = 0
          AND status != 'cancelled'
          AND client_contact IS NOT NULL
          AND client_contact != '0'
          AND appointment_time >= ? AND appointment_time <= ?
        ORDER BY appointment_time
    """, (now.isoformat(), cutoff.isoformat()))
    rows = c.fetchall()
    conn.close()
    return rows

def get_appointment(appointment_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT id, client_name, client_contact, service, appointment_time, status
        FROM appointments WHERE id = ?
    """, (appointment_id,))
    row = c.fetchone()
    conn.close()
    return row

def mark_reminder_sent(appointment_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appointment_id,))
    conn.commit()
    conn.close()

def update_status(appointment_id, status):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
    conn.commit()
    conn.close()

def update_appointment_time(appointment_id, appointment_time):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "UPDATE appointments SET appointment_time = ?, reminder_sent = 0 WHERE id = ?",
        (appointment_time.isoformat(), appointment_id)
    )
    conn.commit()
    conn.close()