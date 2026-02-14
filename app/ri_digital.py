from playwright.sync_api import sync_playwright, TimeoutError
from datetime import datetime
import os
import time

from app.settings import DOWNLOAD_DIR
from app.db import insert_result


PLAYWRIGHT_TIMEOUT = 60_000  # 60s (RI Digital é lento)


def executar_ri_digital(job, credenciais):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    data_inicio = datetime.fromisoformat(job["payload_json"]["data_inicio"])
    data_fim = datetime.fromisoformat(job["payload_json"]["data_fim"])

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage"]
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        # =========================
        # LOGIN
        # =========================
        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

        page.locator("text=Acesso comum").first.click()
        page.fill("input[type=email]", credenciais["login"])
        page.fill("input[type=password]", credenciais["password_encrypted"])
        page.click("button[type=submit]")

        # aguarda painel carregar
        page.wait_for_selector("text=Serviços Online", timeout=PLAYWRIGHT_TIMEOUT)

        # =========================
        # ACESSO À MATRÍCULA
        # =========================
        page.goto("https://ridigital.org.br/ServicosOnline.aspx", wait_until="domcontentloaded")

        page.locator("text=Visualização de matrícula").first.click()

        page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)

        rows = page.query_selector_all("table tbody tr")

        for row in rows:
            try:
                cells = row.query_selector_all("td")
                if len(cells) < 5:
                    continue

                data_text = cells[1].inner_text().strip()
                data_pedido = datetime.strptime(data_text, "%d/%m/%Y")

                if not (data_inicio <= data_pedido <= data_fim):
                    continue

                protocolo = cells[0].inner_text().strip()
                cartorio = cells[2].inner_text().strip()
                matricula = cells[3].inner_text().strip()

                abrir_btn = cells[0].query_selector("a")
                if not abrir_btn:
                    continue

                abrir_btn.click()
                page.wait_for_selector("text=GERAR O PDF", timeout=PLAYWRIGHT_TIMEOUT)

                with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as download_info:
                    page.locator("text=GERAR O PDF").click()

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
                time.sleep(2)

            except TimeoutError:
                # ignora matrícula que falhou e segue
                page.go_back()
                continue

        browser.close()
