"""Configuration commune des tests : aucun accès réseau, aucun secret, données jetables."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Dossier de données jetable, défini AVANT l'import de config (qui lit DATA_DIR).
_DATA = tempfile.mkdtemp(prefix="tev_tests_")
os.environ["DATA_DIR"] = _DATA
os.environ["TEV_NO_DOTENV"] = "1"       # jamais le .env d'un développeur pendant les tests
for var in ("ODDS_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL", "PAYDUNYA_MASTER_KEY",
            "PAYDUNYA_PRIVATE_KEY", "PAYDUNYA_TOKEN", "PAYDUNYA_MODE", "PAYDUNYA_TEST_MASTER_KEY",
            "PAYDUNYA_TEST_PRIVATE_KEY", "PAYDUNYA_TEST_TOKEN", "PAYDUNYA_TEST_EMAILS", "GMAIL_MDP", "GMAIL_EMAIL",
            "EMAIL_SENDER", "BREVO_API_KEY", "SECRET_KEY", "SMTP_HOST", "SMTP_PORT"):
    os.environ.pop(var, None)
os.environ.setdefault("LOG_LEVEL", "WARNING")
