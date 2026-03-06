import time

from app.db import (
    fetch_pending_job,
    update_job_status,
    fetch_ri_digital_credentials,
)

from app.ri_digital import executar_ri_digital
from app.ri_digital_solicitar_certidao_worker import executar_job_ri_digital_solicitar_certidao
from app.ocr_worker import executar_ocr_job


def main():
    print("🤖 Worker GEOINCRA iniciado")

    while True:

        job = fetch_pending_job()

        if not job:
            time.sleep(5)
            continue

        try:

            job_type = job["type"]

            # ------------------------------------------------
            # RI DIGITAL — CONSULTA MATRÍCULAS (AUTOMAÇÃO 1)
            # ------------------------------------------------
            if job_type == "RI_DIGITAL_MATRICULA":

                cred = fetch_ri_digital_credentials(job["user_id"])

                if not cred:
                    raise Exception("Credenciais do RI Digital não encontradas")

                executar_ri_digital(job, cred)

                update_job_status(job["id"], "COMPLETED")

            # ------------------------------------------------
            # RI DIGITAL — SOLICITAR CERTIDÃO (AUTOMAÇÃO 2)
            # ------------------------------------------------
            elif job_type == "RI_DIGITAL_SOLICITAR_CERTIDAO":

                cred = fetch_ri_digital_credentials(job["user_id"])

                if not cred:
                    raise Exception("Credenciais do RI Digital não encontradas")

                executar_job_ri_digital_solicitar_certidao(
                    job,
                    cred["login"],
                    cred["password_encrypted"]
                )

                update_job_status(job["id"], "COMPLETED")

            # ------------------------------------------------
            # OCR DOCUMENTOS (AUTOMAÇÃO 3)
            # ------------------------------------------------
            elif job_type == "OCR_DOCUMENT":

                executar_ocr_job(job)

                update_job_status(job["id"], "COMPLETED")

            # ------------------------------------------------
            # JOB DESCONHECIDO
            # ------------------------------------------------
            else:
                raise Exception(f"Tipo de automação desconhecido: {job_type}")

        except Exception as e:

            update_job_status(job["id"], "FAILED", str(e))


if __name__ == "__main__":
    main()