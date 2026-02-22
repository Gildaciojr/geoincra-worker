from playwright.sync_api import sync_playwright
from datetime import datetime
import os, time

from app.settings import RI_DIGITAL_DIR, BACKEND_UPLOADS_BASE
from app.db import insert_result, create_document


def executar_ri_digital(job, cred):
    os.makedirs(RI_DIGITAL_DIR, exist_ok=True)

    data_inicio = datetime.fromisoformat(job["payload_json"]["data_inicio"])
    data_fim = datetime.fromisoformat(job["payload_json"]["data_fim"])

    print(f"▶️ RI Digital | Job {job['id']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        page = browser.new_page()
        page.set_default_timeout(60_000)

        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")
        page.get_by_text("Acesso comum").click()
        page.fill("input[type=email]", cred["login"])
        page.fill("input[type=password]", cred["password_encrypted"])
        page.click("button[type=submit]")

        page.wait_for_url("**/ServicosOnline.aspx")
        page.get_by_text("Visualização de matrícula").click()
        page.wait_for_selector("table")

        rows = page.query_selector_all("table tbody tr")
        encontrou = False

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 5:
                continue

            data_pedido = datetime.strptime(cells[1].inner_text(), "%d/%m/%Y")
            if not (data_inicio <= data_pedido <= data_fim):
                continue

            protocolo = cells[0].inner_text().strip()
            cartorio = cells[2].inner_text().strip()
            matricula = cells[3].inner_text().strip()

            with page.expect_download() as d:
                cells[0].query_selector("a").click()
                page.get_by_text("PDF").click()

            download = d.value
            filename = f"{protocolo}_{matricula}.pdf".replace("/", "_")
            worker_path = os.path.join(RI_DIGITAL_DIR, filename)
            download.save_as(worker_path)

            backend_path = worker_path.replace("/data", BACKEND_UPLOADS_BASE, 1)
            doc_id = create_document(job["project_id"], filename, backend_path)

            insert_result(
                job["id"],
                {
                    "protocolo": protocolo,
                    "matricula": matricula,
                    "cartorio": cartorio,
                    "data_pedido": data_pedido.date(),
                    "file_path": backend_path,
                    "metadata_json": {
                        "document_id": doc_id,
                        "fonte": "RI_DIGITAL",
                    },
                },
            )

            encontrou = True
            page.go_back()
            time.sleep(1)

        browser.close()

        if not encontrou:
            raise Exception("Nenhuma matrícula encontrada no período")