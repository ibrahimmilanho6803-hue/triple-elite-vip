"""Outils de confidentialité partagés par tous les modules."""


def mask_email(email):
    """j***@gmail.com : assez pour s'y retrouver dans les journaux, sans exposer l'adresse en clair."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
