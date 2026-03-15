import time

from db import (
    fetch_pending_job,
    update_job_status,
    fetch_ri_digital_credentials,
)
from ocr_worker import executar_ocr_job
from ri_digital import executar_ri_digital
from ri_digital_consultar_certidao_worker import (
    executar_job_ri_digital_consultar_certidao,
)
from ri_digital_solicitar_certidao_worker import (
    executar_job_ri_digital_solicitar_certidao,
)


def main() -> None:
    print("🤖 Worker GEOINCRA iniciado")

    while True:
        job = fetch_pending_job()

        if not job:
            time.sleep(5)
            continue

        try:
            job_type = job["type"]

            if job_type == "RI_DIGITAL_MATRICULA":
                cred = fetch_ri_digital_credentials(job["user_id"])

                if not cred:
                    raise Exception("Credenciais do RI Digital não encontradas")

                executar_ri_digital(job, cred)
                update_job_status(job["id"], "COMPLETED")

            elif job_type == "RI_DIGITAL_SOLICITAR_CERTIDAO":
                cred = fetch_ri_digital_credentials(job["user_id"])

                if not cred:
                    raise Exception("Credenciais do RI Digital não encontradas")

                executar_job_ri_digital_solicitar_certidao(
                    job,
                    cred["login"],
                    cred["password_encrypted"],
                )
                update_job_status(job["id"], "COMPLETED")

            elif job_type == "RI_DIGITAL_CONSULTAR_CERTIDAO":
                cred = fetch_ri_digital_credentials(job["user_id"])

                if not cred:
                    raise Exception("Credenciais do RI Digital não encontradas")

                executar_job_ri_digital_consultar_certidao(
                    job,
                    cred["login"],
                    cred["password_encrypted"],
                )
                update_job_status(job["id"], "COMPLETED")

            elif job_type == "OCR_DOCUMENT":
                executar_ocr_job(job)
                update_job_status(job["id"], "COMPLETED")

            else:
                raise Exception(f"Tipo de automação desconhecido: {job_type}")

        except Exception as e:
            update_job_status(job["id"], "FAILED", str(e))


if __name__ == "__main__":
    main()