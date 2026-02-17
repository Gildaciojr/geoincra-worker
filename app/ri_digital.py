from playwright.sync_api import sync_playwright, TimeoutError
from datetime import datetime
import os
import time

from app.settings import DOWNLOAD_DIR
from app.db import insert_result


PLAYWRIGHT_TIMEOUT = 60_000  # 60s


def executar_ri_digital(job, credenciais):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    data_inicio = datetime.fromisoformat(job["payload_json"]["data_inicio"])
    data_fim = datetime.fromisoformat(job["payload_json"]["data_fim"])

    print(f"▶️ Iniciando RI Digital | Job {job['id']}")

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
        print("🔐 Acessando RI Digital...")
        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

        page.locator("text=Acesso comum").first.click()
        page.fill("input[type=email]", credenciais["login"])
        page.fill("input[type=password]", credenciais["password_encrypted"])
        page.click("button[type=submit]")

        page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)
        print("✅ Login realizado")

        # =========================
        # VISUALIZAÇÃO DE MATRÍCULAS
        # =========================
        page.goto("https://ridigital.org.br/ServicosOnline.aspx", wait_until="domcontentloaded")

        page.locator("text=Visualização de matrícula").first.wait_for(timeout=PLAYWRIGHT_TIMEOUT)
        page.locator("text=Visualização de matrícula").first.click()

        page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
        print("📋 Tabela de matrículas carregada")

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

                print(f"📄 Processando matrícula {matricula}")

                abrir_btn = cells[0].query_selector("a")
                if not abrir_btn:
                    continue

                abrir_btn.click()

                page.wait_for_selector("a", timeout=PLAYWRIGHT_TIMEOUT)

                with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as download_info:
                    page.locator("a", has_text="PDF").first.click()

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

                print(f"✅ Matrícula {matricula} salva")
                page.go_back()
                time.sleep(2)

            except TimeoutError:
                print("⚠️ Timeout em uma matrícula, continuando...")
                page.go_back()
                continue

        browser.close()
        print("🏁 Automação RI Digital finalizada")
