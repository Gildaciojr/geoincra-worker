import re
from playwright.sync_api import sync_playwright

from app.db import insert_result


def executar_job_ri_digital_consultar_certidao(job, login, senha):

    payload = job.get("payload_json") or {}

    protocolo_busca = payload.get("protocolo")
    data_busca = payload.get("data")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context()
        page = context.new_page()

        try:

            # LOGIN
            page.goto("https://ridigital.org.br/Acesso.aspx")

            page.click("a.acesso-comum-link")

            page.fill('input[placeholder="E-mail"]', login)
            page.fill('input[placeholder="Senha"]', senha)

            page.click("#btnProsseguir")

            page.wait_for_url("**/ServicosOnline.aspx")

            # IR PARA LISTA
            page.goto("https://ridigital.org.br/CertidaoDigital/lstPedidos.aspx")

            page.wait_for_selector("#Grid")

            rows = page.query_selector_all("#Grid tbody tr")

            for row in rows:

                cols = row.query_selector_all("td")

                if len(cols) < 4:
                    continue

                protocolo = cols[1].inner_text().strip()
                data = cols[2].inner_text().strip()

                if protocolo_busca and protocolo_busca != protocolo:
                    continue

                if data_busca and data_busca != data:
                    continue

                # clicar na pasta
                cols[0].query_selector("a img").click()

                page.wait_for_timeout(2000)

                numero_pedido = page.inner_text("#lblNumeroPedido")

                page.click("#Grid tbody tr td a img")

                page.wait_for_url("**/DetalhesSolicitacao.aspx")

                texto = page.inner_text("body")

                protocolo_match = re.search(r"Nº Protocolo\s*(\S+)", texto)
                tipo_match = re.search(r"Tipo de Certidão\s*(.+)", texto)
                cartorio_match = re.search(r"Cartório / Cidade\s*(.+)", texto)
                status_match = re.search(r"Status\s*(.+)", texto)

                insert_result(
                    job["id"],
                    {
                        "protocolo": protocolo_match.group(1) if protocolo_match else protocolo,
                        "matricula": None,
                        "cartorio": cartorio_match.group(1) if cartorio_match else None,
                        "data_pedido": None,
                        "file_path": None,
                        "metadata_json": {
                            "tipo_certidao": tipo_match.group(1) if tipo_match else None,
                            "status": status_match.group(1) if status_match else None,
                            "numero_pedido": numero_pedido
                        }
                    }
                )

                page.click("input[value='Fechar']")

        finally:

            browser.close()