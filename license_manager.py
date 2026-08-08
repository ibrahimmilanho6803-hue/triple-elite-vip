import hashlib
import datetime
import uuid
import os
import psycopg2

class LicenseManager:
    def __init__(self):
        self.secret_key = "TRIPLE_ELITE_2026_SECRET"
        self.db_url = os.environ.get("DATABASE_URL")
        self.init_db()
    
    def get_conn(self):
        return psycopg2.connect(self.db_url)
    
    def init_db(self):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS licenses (
                    email TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    created TEXT,
                    expires TEXT,
                    active BOOLEAN DEFAULT TRUE
                )
            ''')
            conn.commit()
            conn.close()
            print("Base de donnees initialisee")
        except Exception as e:
            print(f"Erreur DB: {e}")
    
    def generate_license(self, email, duration_months):
        unique_id = str(uuid.uuid4())[:8]
        raw = f"{email}{unique_id}{self.secret_key}"
        license_key = hashlib.sha256(raw.encode()).hexdigest()[:16]
        
        expiration = datetime.datetime.now() + datetime.timedelta(days=30*duration_months)
        
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO licenses (email, key, created, expires, active) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO UPDATE SET key=%s, expires=%s, active=TRUE",
                (email, license_key, str(datetime.datetime.now()), str(expiration), True, license_key, str(expiration))
            )
            conn.commit()
            conn.close()
            print(f"Licence sauvegardee pour {email}")
        except Exception as e:
            print(f"Erreur sauvegarde licence: {e}")
        
        return license_key
    
    def verify_license(self, email, license_key):
    try:
        print(f"Verification pour: {email}, cle: {license_key}")
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM licenses WHERE email = %s", (email,))
        result = cursor.fetchone()
        print(f"Resultat DB: {result}")
        conn.close()
        
        if not result:
            return False, "Licence introuvable"
        
        # Suite du code...
    except Exception as e:
        print(f"Erreur verification: {e}")
        return False, "Erreur de verification"