"""Outils de confidentialité partagés par tous les modules."""
import hashlib


def mask_email(email):
    """j***@gmail.com : assez pour s'y retrouver dans les journaux, sans exposer l'adresse en clair."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def client_scope(email):
    """Identifiant opaque d'un client (16 caractères hexadécimaux) pour nommer ses fichiers : ses combinés générés, l'état de
    sa génération. Le même e-mail (sans tenir compte des majuscules ni des espaces autour) donne toujours le même identifiant ;
    l'adresse elle-même n'apparaît jamais dans un nom de fichier. None pour « personne » (la génération automatique)."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
