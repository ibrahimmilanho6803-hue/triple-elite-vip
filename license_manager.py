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
import re
import secrets
import time
import unicodedata
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


# Une clé fabriquée ici ne contient que des chiffres et les lettres a à f. Recopiée à la main, elle se fait souvent
# lire de travers : un zéro pris pour la lettre O, un un pour un I ou un L. Ces lettres ne peuvent pas faire partie
# d'une vraie clé : on les lit comme les chiffres qu'elles imitent. Sans conséquence sur la sécurité : une clé garde
# plus de 60 bits d'imprévisibilité et les essais sont comptés par e-mail.
_HEX_KEY = re.compile(r"[0-9a-f]+")
_LOOKALIKES = str.maketrans({"o": "0", "l": "1", "i": "1"})


def clean_key(value):
    """Clé telle que saisie, sans ce qu'une copie ou un clavier de téléphone y glisse : espaces (y compris
    insécables), caractères invisibles (espace de largeur nulle, marques de sens d'écriture...) et majuscules."""
    return "".join(c for c in str(value or "") if not c.isspace() and unicodedata.category(c) != "Cf").lower()


def keys_match(stored, provided):
    """La clé saisie est-elle celle de la licence ? Comparaison à temps constant, qui ne plante jamais
    (hmac.compare_digest refuse les textes accentués : on compare des octets)."""
    stored, provided = clean_key(stored), clean_key(provided)
    if _HEX_KEY.fullmatch(stored):
        provided = provided.translate(_LOOKALIKES)
    return hmac.compare_digest(stored.encode("utf-8", "replace"), provided.encode("utf-8", "replace"))


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
            log.info("Base de données des licences initialisée (%s)", self.describe_db())
            extra = self.duplicate_count()
            if extra:
                log.warning("%d ligne(s) de licence en double (même e-mail, majuscules différentes) : la connexion "
                            "choisit la bonne ligne, mais mieux vaut supprimer les lignes en trop "
                            "(python generate_keys.py, option 4).", extra)
        except Exception as e:
            log.error("Base de données des licences indisponible : %s", e)

    def _license_counts(self):
        """(nombre de lignes, nombre d'e-mails distincts à la casse près), ou None si la base est illisible."""
        try:
            with self._db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), COUNT(DISTINCT LOWER(email)) FROM licenses")
                rows, people = cursor.fetchone()
            return int(rows), int(people)
        except Exception:
            return None

    def duplicate_count(self):
        """Nombre de lignes en trop : plusieurs lignes pour un même e-mail (à la casse près) ne devraient pas exister
        (héritage d'anciennes versions, qui gardaient l'e-mail tel que saisi). 0 si tout est en ordre ou illisible."""
        counts = self._license_counts()
        return counts[0] - counts[1] if counts else 0

    def describe_db(self):
        """Repère NON secret de la base utilisée : son nom et le nombre de licences (jamais l'adresse ni le mot de
        passe). Sert à vérifier d'un coup d'œil que le site client et le service de paiement lisent la même base :
        s'ils en lisaient deux, un client aurait payé sans pouvoir se connecter. Signale aussi les doublons."""
        try:
            name = psycopg2.extensions.parse_dsn(self.db_url or "").get("dbname") or "?"
        except Exception:
            name = "?"
        counts = self._license_counts()
        if counts is None:
            return f"base {name}, nombre de licences illisible"
        rows, people = counts
        text = f"base {name}, {rows} licence(s)"
        if people < rows:
            text += f" pour {people} e-mail(s) : DOUBLONS à supprimer"
        return text

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

    @staticmethod
    def _usable(row, now):
        return bool(row["active"]) and row["expires"] >= now

    def _rows_for(self, cursor, email):
        """Les lignes d'un e-mail (à la casse près), la plus utile d'abord : licence utilisable, puis active mais
        expirée, puis désactivée ; à égalité, celle qui court le plus longtemps.

        Il ne devrait y en avoir qu'une. Des anciennes versions gardaient l'e-mail tel que saisi, d'où des lignes
        « Ibrahim@… » et « ibrahim@… » côte à côte : lire « la » ligne au hasard donnait parfois la périmée, et la
        bonne clé était refusée. Toute lecture passe donc par ce tri, jamais par un simple fetchone()."""
        cursor.execute("SELECT email, key, expires, active FROM licenses WHERE LOWER(email) = %s",
                       (normalize_email(email),))
        now = _utcnow()
        rows = [{"email": r[0], "key": r[1], "expires": self._parse_expires(r[2]), "active": bool(r[3])}
                for r in cursor.fetchall()]
        rows.sort(key=lambda r: (self._usable(r, now), r["active"], r["expires"], str(r["key"])), reverse=True)
        return rows

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
            rows = self._rows_for(cursor, email)
            best = rows[0] if rows else None                # la plus utile : c'est elle qu'on renouvelle
            renewed = False
            base, key = now, None
            if best and self._usable(best, now):
                base, key, renewed = best["expires"], best["key"], True
            if key is None:
                key = self._new_key(email)
            new_expiry = base + datetime.timedelta(days=duration_days(duration_months))
            if best:
                cursor.execute("UPDATE licenses SET key = %s, expires = %s, active = %s WHERE email = %s",
                               (key, str(new_expiry), True, best["email"]))
            else:
                cursor.execute("INSERT INTO licenses (email, key, created, expires, active) "
                               "VALUES (%s, %s, %s, %s, %s)", (email, key, str(now), str(new_expiry), True))
            conn.commit()
        log.info("Licence %s pour %s jusqu'au %s", "renouvelée" if renewed else "créée", mask_email(email), new_expiry)
        if len(rows) > 1:
            log.warning("%d lignes de licence pour %s : seule la plus utile a été mise à jour, supprime les autres "
                        "(python generate_keys.py, option 4)", len(rows), mask_email(email))
        return {"key": key, "expires": new_expiry, "renewed": renewed}

    def generate_license(self, email, duration_months):
        """Comme issue_license(), en ne renvoyant que la clé (compatibilité)."""
        return self.issue_license(email, duration_months)["key"]

    def _fetch_licenses(self, email):
        with self._db() as conn:
            return self._rows_for(conn.cursor(), email)

    def get_status(self, email):
        """{"state": active | expired | inactive | unknown | error, "expires": datetime | None}."""
        if not normalize_email(email):
            return {"state": "unknown", "expires": None}
        try:
            rows = self._fetch_licenses(email)
        except Exception as e:
            log.error("vérification de licence impossible : %s", e)
            return {"state": "error", "expires": None}
        if not rows:
            return {"state": "unknown", "expires": None}
        row = rows[0]
        if not row["active"]:
            return {"state": "inactive", "expires": row["expires"]}
        if row["expires"] < _utcnow():
            return {"state": "expired", "expires": row["expires"]}
        return {"state": "active", "expires": row["expires"]}

    @staticmethod
    def _invalid(detail):
        return {"ok": False, "reason": "invalid", "expires": None, "detail": detail,
                "message": "E-mail ou clé de licence incorrect."}

    def check_login(self, email, license_key):
        """Connexion. Renvoie {"ok": bool, "reason": ..., "message": str, "expires": datetime | None}.

        reason : "ok" | "invalid" (e-mail inconnu OU mauvaise clé : le visiteur ne le sait pas)
        | "inactive" | "expired" (seulement quand la clé fournie est la bonne, pour ne rien
        révéler sur un compte) | "unavailable" (base de données injoignable).

        Pour "invalid", "detail" donne le motif exact (e-mail inconnu, clé vide, clé différente) : il est
        réservé aux journaux du serveur, jamais à afficher au visiteur (le message, lui, est toujours le même).
        La clé tolère espaces, majuscules, caractères invisibles et lettres ressemblant à 0 ou 1 (voir keys_match).

        Si plusieurs lignes existent pour cet e-mail (doublons hérités), c'est la ligne dont la clé correspond qui
        décide : une ligne périmée ou désactivée ne bloque jamais la bonne, et l'ancienne clé reste refusée.
        """
        try:
            rows = self._fetch_licenses(email)
        except Exception as e:
            log.error("vérification de licence impossible : %s", e)
            return {"ok": False, "reason": "unavailable", "expires": None,
                    "message": "Service momentanément indisponible. Réessaie dans un instant."}
        if not rows:
            return self._invalid("e-mail inconnu")
        provided = clean_key(license_key)
        if not provided:
            return self._invalid("clé vide")
        matching = [row for row in rows if keys_match(row["key"], provided)]      # toutes comparées, sans sortie anticipée
        if not matching:
            detail = f"clé différente ({len(provided)} caractères saisis, {len(clean_key(rows[0]['key']))} attendus)"
            if len(rows) > 1:
                detail += f", {len(rows)} lignes pour cet e-mail"
            return self._invalid(detail)
        row = matching[0]                                    # déjà triée : la plus utile parmi celles qui correspondent
        expires_at = row["expires"]
        if not row["active"]:
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

    def delete_license(self, email):
        """Supprime DÉFINITIVEMENT les licences d'un e-mail : toutes les lignes, majuscules ou non. Renvoie le nombre
        de lignes supprimées (0 : aucune trouvée). Les commandes de paiement, elles, sont conservées.
        Lève une exception si la base est injoignable : l'appelant ne doit pas faire croire que c'est fait."""
        email = normalize_email(email)
        if not email:
            return 0
        with self._db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM licenses WHERE LOWER(email) = %s", (email,))
            deleted = cursor.rowcount
            conn.commit()
        log.info("Licence supprimée pour %s (%d ligne(s))", mask_email(email), deleted)
        return deleted

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
