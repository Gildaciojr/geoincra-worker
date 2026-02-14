import time
from app.db import (
    fetch_pending_job,
    update_job_status,
    fetch_ri_digital_credentials,
)
from app.ri_digital import executar_ri_digital


def main():
    print("🤖 Worker de automação iniciado")

    while True:
        job = fetch_pending_job()

        if not job:
            time.sleep(5)
            continue

        try:
            cred = fetch_ri_digital_credentials(job["user_id"])
            if not cred:
                raise Exception("Credenciais do RI Digital não encontradas")

            executar_ri_digital(job, cred)

            update_job_status(job["id"], "COMPLETED")

        except Exception as e:
            update_job_status(job["id"], "FAILED", str(e))


if __name__ == "__main__":
    main()
