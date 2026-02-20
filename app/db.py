# geoincra_worker/app/db.py
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from app.settings import DATABASE_URL


def get_connection():
    return psycopg2.connect(DATABASE_URL)


# =========================================================
# BUSCA E BLOQUEIO DO JOB (ATÔMICO) - MULTI PROVIDER
# - Pega qualquer PENDING
# - Marca PROCESSING
# - SKIP LOCKED para múltiplos workers
# =========================================================
def fetch_pending_job():
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE automation_jobs
                SET status = 'PROCESSING',
                    started_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM automation_jobs
                    WHERE status = 'PENDING'
                      AND type = 'RI_DIGITAL_MATRICULA'
                    ORDER BY created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
            """)
            job = cur.fetchone()
            conn.commit()
            return job



def fetch_ri_digital_credentials(user_id: int):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT login, password_encrypted
                FROM external_credentials
                WHERE user_id = %s
                  AND provider = 'RI_DIGITAL'
                  AND active = TRUE
            """, (user_id,))
            return cur.fetchone()


def update_job_status(job_id, status, error_message=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE automation_jobs
                SET status = %s,
                    error_message = %s,
                    finished_at = CASE
                        WHEN %s IN ('COMPLETED', 'FAILED') THEN NOW()
                        ELSE finished_at
                    END
                WHERE id = %s
            """, (status, error_message, status, job_id))
            conn.commit()


# =========================================================
# INSERÇÃO DO RESULTADO (GENÉRICO)
# - RI Digital preenche protocolo/matricula/cartorio...
# - ONR usa metadata_json + file_path (KMZ)
# =========================================================
def insert_result(job_id, data: dict):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO automation_results (
                    job_id,
                    protocolo,
                    matricula,
                    cnm,
                    cartorio,
                    data_pedido,
                    file_path,
                    metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                job_id,
                data.get("protocolo"),
                data.get("matricula"),
                data.get("cnm"),
                data.get("cartorio"),
                data.get("data_pedido"),
                data.get("file_path"),
                Json(data.get("metadata_json")) if data.get("metadata_json") is not None else None,
            ))
            result_id = cur.fetchone()[0]
            conn.commit()
            return result_id


# =========================================================
# CRIA UM DOCUMENT VINCULADO AO PROJETO (KMZ/PDF etc.)
# - O backend faz download seguro via /api/files/documents/{id}
# =========================================================
def create_document(
    project_id: int,
    doc_type: str,
    stored_filename: str,
    original_filename: str,
    content_type: str,
    description: str,
    file_path: str,
):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO documents (
                    project_id,
                    matricula_id,
                    doc_type,
                    stored_filename,
                    original_filename,
                    content_type,
                    description,
                    file_path,
                    uploaded_at,
                    observacoes
                )
                VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, NOW(), NULL)
                RETURNING id
            """, (
                project_id,
                doc_type,
                stored_filename,
                original_filename,
                content_type,
                description,
                file_path,
            ))
            doc = cur.fetchone()
            conn.commit()
            return doc["id"]


def get_job_project_id(job_id):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT project_id FROM automation_jobs WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return row["project_id"] if row else None
