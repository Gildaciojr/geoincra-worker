# geoincra_worker/app/main.py
import time

from app.db import (
    fetch_pending_job,
    update_job_status,
    fetch_ri_digital_credentials,
)
from app.ri_digital import executar_ri_digital
from app.onr_sigri import executar_onr_sigri


def main():
    print("🤖 Worker de automação iniciado (multi-provider)")

    while True:
        job = fetch_pending_job()

        if not job:
            time.sleep(5)
            continue

        job_type = (job.get("type") or "").strip()

        try:
            if job_type == "RI_DIGITAL_MATRICULA":
                cred = fetch_ri_digital_credentials(job["user_id"])
                if not cred:
                    raise Exception("Credenciais do RI Digital não encontradas")

                executar_ri_digital(job, cred)
                update_job_status(job["id"], "COMPLETED")

            elif job_type == "ONR_SIGRI_CONSULTA":
                executar_onr_sigri(job)
                update_job_status(job["id"], "COMPLETED")

            else:
                raise Exception(f"Tipo de job não suportado: {job_type}")

        except Exception as e:
            update_job_status(job["id"], "FAILED", str(e))


if __name__ == "__main__":
    main()
