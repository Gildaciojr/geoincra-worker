# geoincra_worker/app/settings.py
import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://geoincra_user:domboscojacutinga33@geoincra_postgres:5432/geoincra_db"
)

# Volume compartilhado:
# - Worker monta em: /data
# - Backend monta o MESMO volume em: /app/app/uploads
DATA_DIR = os.getenv("DATA_DIR", "/data")

# Subpastas por automação (organização SaaS)
RI_DIGITAL_DIR = os.path.join(DATA_DIR, "ri-digital")
ONR_SIGRI_DIR = os.path.join(DATA_DIR, "onr-sigri")

# Caminho equivalente que o BACKEND enxerga (mesmo volume)
# O worker vai salvar no /data/..., mas vai registrar no banco o path do backend:
BACKEND_UPLOADS_BASE = os.getenv("BACKEND_UPLOADS_BASE", "/app/app/uploads")

# Certificado A1 (PFX) para ONR
# IMPORTANTE: no VPS, não confie em “cert instalado no Windows”.
# O worker precisa de um arquivo PFX acessível no container + senha.
ONR_PFX_PATH = os.getenv("ONR_PFX_PATH", "")          # ex.: /data/certs/onr_a1.pfx
ONR_PFX_PASSWORD = os.getenv("ONR_PFX_PASSWORD", "")  # senha do PFX
