import psycopg2
from psycopg2.extras import RealDictCursor
from app.settings import DATABASE_URL


# =========================================================
# CONEXÃO COM O BANCO
# =========================================================

def get_connection():
    return psycopg2.connect(DATABASE_URL)


# =========================================================
# BUSCA E BLOQUEIO DO JOB (ATÔMICO)
# - Já marca como PROCESSING
# - Evita race condition
# - Pronto para múltiplos workers
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


# =========================================================
# BUSCA DE CREDENCIAIS DO RI DIGITAL
# =========================================================

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


# =========================================================
# ATUALIZA STATUS DO JOB (FINALIZAÇÃO)
# =========================================================

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
# INSERÇÃO DO RESULTADO DA AUTOMAÇÃO
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
                    file_path
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                job_id,
                data.get("protocolo"),
                data.get("matricula"),
                data.get("cnm"),
                data.get("cartorio"),
                data.get("data_pedido"),
                data.get("file_path"),
            ))
            conn.commit()
