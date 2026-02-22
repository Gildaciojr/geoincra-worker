import psycopg2
from psycopg2.extras import RealDictCursor, Json
from app.settings import DATABASE_URL


def get_connection():
    return psycopg2.connect(DATABASE_URL)


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


def create_document(project_id, filename, file_path):
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
                    uploaded_at
                )
                VALUES (%s, NULL, 'RI_DIGITAL_PDF', %s, %s,
                        'application/pdf',
                        'Matrícula RI Digital',
                        %s, NOW())
                RETURNING id
            """, (project_id, filename, filename, file_path))
            doc = cur.fetchone()
            conn.commit()
            return doc["id"]


def insert_result(job_id, data):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO automation_results (
                    job_id,
                    protocolo,
                    matricula,
                    cartorio,
                    data_pedido,
                    file_path,
                    metadata_json
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (
                job_id,
                data["protocolo"],
                data["matricula"],
                data["cartorio"],
                data["data_pedido"],
                data["file_path"],
                Json(data.get("metadata_json")),
            ))
            conn.commit()