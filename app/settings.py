import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://geoincra_user:domboscojacutinga33@geoincra_postgres:5432/geoincra_db"
)

DOWNLOAD_DIR = "/data/ri-digital"
