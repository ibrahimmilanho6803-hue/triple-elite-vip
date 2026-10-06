"""Licences clients (PostgreSQL) et commandes de paiement en attente.

Toutes les dates sont stockées en texte, en UTC « naïf » (format de str(datetime)),
comme les licences déjà émises. Les e-mails sont comparés sans tenir compte des
majuscules (les claviers de téléphone en ajoutent souvent).
"""
import datetime
import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import contextmanager

import psycopg2

import config
from privacy import mask_email

log = logging.getLogger(__name__)

MONTH_DAYS = 30
YEAR_DAYS = 365


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def normalize_email(email):
    return (email or "").strip().lower()


def duration_days(months):
    """Durée d'un abonnement en jours : 1 mois = 30 jours, 12 mois = 365 jours (une vraie année)."""
    months = max(0, int(months))
    return round(months * YEAR_DAYS / 12)


class LicenseManager:
    def __init__(self, db_url=None, connect=None):
        # LICENSE_SECRET_KEY sert de sel pour fabriquer les NOUVELLES clés. Elle ne
        # sert pas à vérifier les clés déjà émises (comparées à la base) : la
        # changer ne casse aucune licence existante.
        self.secret_key = os.environ.get("LICENSE_SECRET_KEY") or secrets.token_hex(32)
        self.db_url = db_url or os.environ.get("DATABASE_URL")
        self._connect = connect          # fabrique de connexions (tests)
        self.init_db()

    def get_conn(self):
        if self._connect is not None:
            return self._connect()
        return psycopg2.connect(self.db_url, connect_timeout=5)

    @contextmanager
    def _db(self):
        conn = self.get_conn()
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self):
        try:
            with self._db() as conn:
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
                # Commandes en attente de confirmation de paiement PayDunya : créées
                # AVANT la redirection vers PayDunya, validées ensuite en
                # interrogeant PayDunya (jamais à partir de paramètres d'URL).
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
                # Colonnes ajoutées après coup (bases déjà en service) : la clé délivrée et
                # le fait qu'il s'agisse d'un renouvellement, pour pouvoir les réafficher au
                # client même si son e-mail tarde ou n'arrive pas.
                for column in ("license_key TEXT", "renewed BOOLEAN"):
                    try:
                        cursor.execute(f"ALTER TABLE pending_orders ADD COLUMN {column}")
                        conn.commit()
                    except Exception:
                        conn.rollback()      # colonne déjà présente
            log.info("Base de données des licences initialisée")
        except Exception as e:
            log.error("Base de données des licences indisponible : %s", e)

    # ------------------------------------------------------------------
    # Licences
    # ------------------------------------------------------------------

    def _new_key(self, email):
        raw = f"{email}:{secrets.token_hex(16)}"
        return hmac.new(self.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]

    @staticmethod
    def _parse_expires(expires):
        # str(datetime) omet les microsecondes quand elles sont nulles : on accepte
        # les deux formats pour ne jamais planter sur une date pourtant valide.
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.datetime.strptime(str(expires), fmt)
            except ValueError:
                continue
        return datetime.datetime.min

    def issue_license(self, email, duration_months):
        """Crée ou renouvelle la licence d'un e-mail.

        - licence encore valide : la durée achetée s'AJOUTE au temps restant et la
          clé reste la même (le client ne perd rien en renouvelant à l'avance) ;
        - licence expirée / désactivée / inexistante : nouvelle clé, durée à partir
          de maintenant.
        Lève une exception si la base est inaccessible : l'appelant ne doit alors
        surtout pas prétendre avoir délivré une licence.
        Renvoie {"key", "expires" (datetime), "renewed" (bool)}.
        """
        email = normalize_email(email)
        now = _utcnow()
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT email, key, expires, active FROM licenses WHERE LOWER(email) = %s", (email,))
            row = cursor.fetchone()
            renewed = False
            base, key = now, None
            if row:
                stored_email, stored_key, expires, active = row
                current_end = self._parse_expires(expires)
                if active and current_end > now:
                    base, key, renewed = current_end, stored_key, True
            if key is None:
                key = self._new_key(email)
            new_expiry = base + datetime.timedelta(days=duration_days(duration_months))
            if row:
                cursor.execute("UPDATE licenses SET key = %s, expires = %s, active = %s WHERE email = %s",
                               (key, str(new_expiry), True, stored_email))
            else:
                cursor.execute("INSERT INTO licenses (email, key, created, expires, active) "
                               "VALUES (%s, %s, %s, %s, %s)", (email, key, str(now), str(new_expiry), True))
            conn.commit()
        log.info("Licence %s pour %s jusqu'au %s", "renouvelée" if renewed else "créée", mask_email(email), new_expiry)
        return {"key": key, "expires": new_expiry, "renewed": renewed}

    def generate_license(self, email, duration_months):
        """Comme issue_license(), en ne renvoyant que la clé (compatibilité)."""
        return self.issue_license(email, duration_months)["key"]

    def _fetch_license(self, email):
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, active, expires FROM licenses WHERE LOWER(email) = %s",
                           (normalize_email(email),))
            return cursor.fetchone()

    def get_status(self, email):
        """{"state": active | expired | inactive | unknown | error, "expires": datetime | None}."""
        if not normalize_email(email):
            return {"state": "unknown", "expires": None}
        try:
            row = self._fetch_license(email)
        except Exception as e:
            log.error("vérification de licence impossible : %s", e)
            return {"state": "error", "expires": None}
        if not row:
            return {"state": "unknown", "expires": None}
        _key, active, expires = row
        expires_at = self._parse_expires(expires)
        if not active:
            return {"state": "inactive", "expires": expires_at}
        if expires_at < _utcnow():
            return {"state": "expired", "expires": expires_at}
        return {"state": "active", "expires": expires_at}

    def check_login(self, email, license_key):
        """Connexion. Renvoie {"ok": bool, "reason": ..., "message": str, "expires": datetime | None}.

        reason : "ok" | "invalid" (e-mail inconnu OU mauvaise clé : on ne distingue pas)
        | "inactive" | "expired" (seulement quand la clé fournie est la bonne, pour ne rien
        révéler sur un compte) | "unavailable" (base de données injoignable).
        """
        try:
            row = self._fetch_license(email)
        except Exception as e:
            log.error("vérification de licence impossible : %s", e)
            return {"ok": False, "reason": "unavailable", "expires": None,
                    "message": "Service momentanément indisponible. Réessaie dans un instant."}
        provided = "".join((license_key or "").split()).lower()      # tolère espaces et majuscules (clé recopiée)
        if not row or not provided or not hmac.compare_digest(str(row[0]).strip().lower(), provided):
            return {"ok": False, "reason": "invalid", "expires": None,
                    "message": "E-mail ou clé de licence incorrect."}
        _key, active, expires = row
        expires_at = self._parse_expires(expires)
        if not active:
            return {"ok": False, "reason": "inactive", "expires": expires_at,
                    "message": f"Licence désactivée. Contacte-nous : {config.SELLER_EMAIL}"}
        if expires_at < _utcnow():
            return {"ok": False, "reason": "expired", "expires": expires_at,
                    "message": f"Ton abonnement a expiré le {expires_at.strftime('%d/%m/%Y')}. "
                               "Renouvelle-le pour retrouver l'accès."}
        return {"ok": True, "reason": "ok", "message": "Licence valide", "expires": expires_at}

    def verify_license(self, email, license_key):
        """Comme check_login(), réduit à (valide, message)."""
        result = self.check_login(email, license_key)
        return result["ok"], result["message"]

    def is_license_active(self, email):
        """Revalidation légère (sans la clé), faite à CHAQUE requête protégée : l'accès
        est coupé dès que la licence expire ou est désactivée, même si le cookie de
        session du navigateur est encore valide."""
        return self.get_status(email)["state"] == "active"

    def deactivate_license(self, email):
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE licenses SET active = %s WHERE LOWER(email) = %s",
                               (False, normalize_email(email)))
                found = cursor.rowcount > 0
                conn.commit()
            return found
        except Exception as e:
            log.error("désactivation impossible : %s", e)
            return False

    def list_licenses(self):
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT email, key, created, expires, active FROM licenses ORDER BY created DESC")
                rows = cursor.fetchall()
            return [{"email": r[0], "key": r[1], "created": r[2], "expires": r[3], "active": r[4]} for r in rows]
        except Exception as e:
            log.error("liste des licences impossible : %s", e)
            return []

    # ------------------------------------------------------------------
    # Commandes en attente de paiement (utilisées par paiement.py)
    # ------------------------------------------------------------------

    def create_pending_order(self, token, email, plan, duree):
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO pending_orders (token, email, plan, duree, created, processed) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (token) DO NOTHING",
                    (token, normalize_email(email), plan, duree, str(_utcnow()), False))
                conn.commit()
            return True
        except Exception as e:
            log.error("création de la commande impossible : %s", e)
            return False

    def get_pending_order(self, token):
        """La commande, ou None si elle n'existe pas.
        Lève une exception si la base est injoignable (à ne pas confondre avec « inconnue »)."""
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT email, plan, duree, processed, license_key, renewed FROM pending_orders "
                           "WHERE token = %s", (token,))
            row = cursor.fetchone()
        if not row:
            return None
        return {"email": row[0], "plan": row[1], "duree": row[2], "processed": bool(row[3]),
                "license_key": row[4], "renewed": None if row[5] is None else bool(row[5])}

    def claim_order(self, token):
        """Réserve la commande de façon ATOMIQUE : un seul des appelants simultanés
        (retour du navigateur, notification PayDunya...) l'obtient et peut émettre la
        licence. Renvoie la commande, ou None si elle est déjà prise / inconnue.
        Lève une exception si la base est injoignable."""
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE pending_orders SET processed = %s WHERE token = %s AND processed = %s "
                "RETURNING email, plan, duree", (True, token, False))
            row = cursor.fetchone()
            conn.commit()
        if not row:
            return None
        return {"email": row[0], "plan": row[1], "duree": row[2]}

    def release_order(self, token):
        """Annule la réservation (la licence n'a pas pu être délivrée : on pourra réessayer)."""
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE pending_orders SET processed = %s WHERE token = %s", (False, token))
                conn.commit()
        except Exception as e:
            log.error("libération de la commande impossible : %s", e)

    def complete_order(self, token, license_key, renewed=False):
        """Enregistre la clé délivrée pour cette commande (et s'il s'agit d'un renouvellement), pour pouvoir la
        réafficher au client. Deux essais : la licence existe déjà à ce stade, mieux vaut ne pas perdre ce lien.
        Renvoie True si l'enregistrement a réussi."""
        for attempt in (1, 2):
            try:
                with self._db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE pending_orders SET processed = %s, license_key = %s, renewed = %s "
                                   "WHERE token = %s", (True, license_key, bool(renewed), token))
                    conn.commit()
                return True
            except Exception as e:
                log.error("enregistrement de la clé de commande impossible (essai %s/2) : %s", attempt, e)
                if attempt == 1:
                    time.sleep(0.3)
        return False

    def mark_order_processed(self, token):
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE pending_orders SET processed = %s WHERE token = %s", (True, token))
                conn.commit()
        except Exception as e:
            log.error("mise à jour de la commande impossible : %s", e)
