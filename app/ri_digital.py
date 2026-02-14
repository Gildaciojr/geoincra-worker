from playwright.sync_api import sync_playwright
from datetime import datetime
import os

from app.settings import DOWNLOAD_DIR
from app.db import insert_result


def executar_ri_digital(job, credenciais):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    data_inicio = datetime.fromisoformat(job["payload_json"]["data_inicio"])
    data_fim = datetime.fromisoformat(job["payload_json"]["data_fim"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        # 1. Login
        page.goto("https://ridigital.org.br/Acesso.aspx")
        page.click("text=Acesso comum")
        page.fill("input[type=email]", credenciais["login"])
        page.fill("input[type=password]", credenciais["password_encrypted"])
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")

        # 2. Visualização de matrícula
        page.goto("https://ridigital.org.br/ServicosOnline.aspx")
        page.click("text=Visualização de matrícula")
        page.wait_for_load_state("networkidle")

        # 3. Tabela de pedidos
        rows = page.query_selector_all("table tr")

        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) < 5:
                continue

            data_text = cells[1].inner_text().strip()
            try:
                data_pedido = datetime.strptime(data_text, "%d/%m/%Y")
            except ValueError:
                continue

            if not (data_inicio <= data_pedido <= data_fim):
                continue

            protocolo = cells[0].inner_text().strip()
            cartorio = cells[2].inner_text().strip()
            matricula = cells[3].inner_text().strip()

            # 4. Abrir matrícula
            abrir_btn = cells[0].query_selector("a")
            if not abrir_btn:
                continue

            with page.expect_navigation():
                abrir_btn.click()

            # 5. Gerar PDF
            with page.expect_download() as download_info:
                page.click("text=CLIQUE AQUI PARA GERAR O PDF")

            download = download_info.value

            filename = f"{protocolo}_{matricula}.pdf".replace("/", "_")
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            download.save_as(file_path)

            insert_result(
                job_id=job["id"],
                data={
                    "protocolo": protocolo,
                    "matricula": matricula,
                    "cnm": None,
                    "cartorio": cartorio,
                    "data_pedido": data_pedido.date(),
                    "file_path": file_path,
                },
            )

            page.go_back()
            page.wait_for_load_state("networkidle")

        browser.close()
