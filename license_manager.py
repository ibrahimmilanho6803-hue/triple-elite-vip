import hashlib
import datetime
import uuid
import os
import secrets
import psycopg2


class LicenseManager:
    def __init__(self):
        # SECURITE : cette cle etait codee en dur ("TRIPLE_ELITE_2026_SECRET")
        # et poussee sur le depot GitHub public. Elle ne sert qu'a saler le
        # hash des NOUVELLES licences generees (les licences deja emises
        # restent valables telles quelles, verifiees par comparaison directe
        # en base, donc la changer ne casse rien pour les clients actuels).
        # Definis LICENSE_SECRET_KEY sur Render pour une valeur stable ;
        # sinon une valeur aleatoire est utilisee (differente a chaque
        # redemarrage, ce qui n'affecte que le "sel" des futures cles, pas
        # leur verification).
        self.secret_key = os.environ.get("LICENSE_SECRET_KEY") or secrets.token_hex(32)
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
            # Commandes en attente de confirmation de paiement PayDunya.
            # Cree AVANT la redirection vers PayDunya (dans /payer) et
            # consultee/validee dans /succes en verifiant aupres de PayDunya
            # que le token a bien ete paye, au lieu de faire confiance aux
            # parametres d'URL envoyes par le navigateur du client.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_orders (
                    token TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    plan TEXT NOT NULL,
                    duree INTEGER NOT NULL,
                    created TEXT,
                    processed BOOLEAN DEFAULT FALSE
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

        expiration = datetime.datetime.now() + datetime.timedelta(days=30 * duration_months)

        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO licenses (email, key, created, expires, active) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (email) DO UPDATE SET key=%s, expires=%s, active=TRUE",
                (email, license_key, str(datetime.datetime.now()), str(expiration), True, license_key, str(expiration))
            )
            conn.commit()
            conn.close()
            print(f"Licence sauvegardee pour {email}")
        except Exception as e:
            print(f"Erreur sauvegarde licence: {e}")

        return license_key

    @staticmethod
    def _parse_expires(expires):
        # str(datetime) omet les microsecondes quand elles sont nulles ;
        # on accepte les deux formats pour ne jamais planter sur une date
        # pourtant valide.
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.datetime.strptime(expires, fmt)
            except ValueError:
                continue
        return datetime.datetime.min

    def verify_license(self, email, license_key):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT key, active, expires FROM licenses WHERE email = %s", (email,))
            result = cursor.fetchone()
            conn.close()

            if not result:
                return False, "Licence introuvable"

            key, active, expires = result

            if not active:
                return False, "Licence desactivee"

            if self._parse_expires(expires) < datetime.datetime.now():
                return False, "Licence expiree"

            if key != license_key:
                return False, "Cle invalide"

            return True, "Licence valide"
        except Exception as e:
            print(f"Erreur verification: {e}")
            return False, "Erreur de verification"

    def is_license_active(self, email):
        """Revalidation legere (sans la cle), utilisee a CHAQUE requete
        protegee du dashboard (pas seulement a la connexion). Sans ca, un
        client reste connecte indefiniment via son cookie de session meme
        apres l'expiration de son abonnement : c'est cette methode qui coupe
        l'acces automatiquement des que la date d'expiration est depassee ou
        que la licence est desactivee, au prochain clic dans le dashboard."""
        if not email:
            return False
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT active, expires FROM licenses WHERE email = %s", (email,))
            result = cursor.fetchone()
            conn.close()
            if not result:
                return False
            active, expires = result
            if not active:
                return False
            if self._parse_expires(expires) < datetime.datetime.now():
                return False
            return True
        except Exception as e:
            print(f"Erreur verification acces: {e}")
            return False

    def deactivate_license(self, email):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("UPDATE licenses SET active = FALSE WHERE email = %s", (email,))
            found = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return found
        except Exception as e:
            print(f"Erreur desactivation: {e}")
            return False

    def list_licenses(self):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT email, key, created, expires, active FROM licenses ORDER BY created DESC")
            rows = cursor.fetchall()
            conn.close()
            return [
                {"email": r[0], "key": r[1], "created": r[2], "expires": r[3], "active": r[4]}
                for r in rows
            ]
        except Exception as e:
            print(f"Erreur liste licences: {e}")
            return []

    # --- Commandes en attente de paiement (utilise par paiement.py) ---

    def create_pending_order(self, token, email, plan, duree):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO pending_orders (token, email, plan, duree, created, processed) "
                "VALUES (%s, %s, %s, %s, %s, FALSE) "
                "ON CONFLICT (token) DO NOTHING",
                (token, email, plan, duree, str(datetime.datetime.now()))
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Erreur creation commande en attente: {e}")
            return False

    def get_pending_order(self, token):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT email, plan, duree, processed FROM pending_orders WHERE token = %s", (token,))
            result = cursor.fetchone()
            conn.close()
            if not result:
                return None
            return {"email": result[0], "plan": result[1], "duree": result[2], "processed": result[3]}
        except Exception as e:
            print(f"Erreur lecture commande en attente: {e}")
            return None

    def mark_order_processed(self, token):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("UPDATE pending_orders SET processed = TRUE WHERE token = %s", (token,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Erreur mise a jour commande: {e}")
