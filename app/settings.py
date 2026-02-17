# geoincra_worker/app/settings.py
import os

# =========================================================
# BANCO DE DADOS
# =========================================================
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://geoincra_user:domboscojacutinga33@geoincra_postgres:5432/geoincra_db"
)

# =========================================================
# VOLUME COMPARTILHADO
# =========================================================
# Worker monta em: /data
# Backend monta o MESMO volume em: /app/app/uploads
DATA_DIR = os.getenv("DATA_DIR", "/data")
BACKEND_UPLOADS_BASE = os.getenv("BACKEND_UPLOADS_BASE", "/app/app/uploads")

# =========================================================
# SUBPASTAS POR AUTOMAÇÃO (PADRÃO SAAS)
# =========================================================
RI_DIGITAL_DIR = os.path.join(DATA_DIR, "ri-digital")
ONR_SIGRI_DIR = os.path.join(DATA_DIR, "onr-sigri")

# =========================================================
# 🔴 COMPATIBILIDADE COM RI DIGITAL
# =========================================================
# O arquivo ri_digital.py importa DOWNLOAD_DIR diretamente.
# Mantemos essa variável como alias oficial.
DOWNLOAD_DIR = RI_DIGITAL_DIR

# =========================================================
# CERTIFICADO DIGITAL A1 — ONR / SIG-RI
# =========================================================
# ⚠️ O certificado PRECISA existir dentro do container
# Exemplo real: /data/certs/onr_cert.pfx
ONR_PFX_PATH = os.getenv("ONR_PFX_PATH", "")
ONR_PFX_PASSWORD = os.getenv("ONR_PFX_PASSWORD", "")
