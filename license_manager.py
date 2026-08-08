import hashlib
import json
import datetime
import uuid

class LicenseManager:
    def __init__(self):
        self.secret_key = "TRIPLE_ELITE_2026_SECRET"
        self.licenses_file = "licenses.json"
    
    def generate_license(self, email, duration_months):
        """Génère une clé de licence unique"""
        unique_id = str(uuid.uuid4())[:8]
        raw = f"{email}{unique_id}{self.secret_key}"
        license_key = hashlib.sha256(raw.encode()).hexdigest()[:16]
        
        expiration = datetime.datetime.now() + datetime.timedelta(days=30*duration_months)
        
        license_data = {
            "email": email,
            "key": license_key,
            "created": str(datetime.datetime.now()),
            "expires": str(expiration),
            "active": True
        }
        
        # Sauvegarder dans le fichier
        try:
            with open(self.licenses_file, 'r') as f:
                licenses = json.load(f)
        except:
            licenses = []
        
        licenses.append(license_data)
        
        with open(self.licenses_file, 'w') as f:
            json.dump(licenses, f, indent=4)
        
        return license_key
    
    def verify_license(self, email, license_key):
        """Vérifie si une licence est valide"""
        try:
            with open(self.licenses_file, 'r') as f:
                licenses = json.load(f)
            
            for lic in licenses:
                if lic["email"] == email and lic["key"] == license_key:
                    if not lic["active"]:
                        return False, "Licence désactivée"
                    
                    expiration = datetime.datetime.strptime(lic["expires"], "%Y-%m-%d %H:%M:%S.%f")
                    if expiration < datetime.datetime.now():
                        return False, "Licence expirée"
                    
                    return True, "Licence valide"
            
            return False, "Licence introuvable"
        except:
            return False, "Erreur de vérification"

# Test
if __name__ == "__main__":
    lm = LicenseManager()
    key = lm.generate_license("test@email.com", 1)
    print(f"✅ Licence générée : {key}")
    print(lm.verify_license("test@email.com", key))