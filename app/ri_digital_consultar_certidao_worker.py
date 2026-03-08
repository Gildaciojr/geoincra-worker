from pathlib import Path
import re

from playwright.sync_api import sync_playwright

from app.db import insert_result, create_document

DOWNLOAD_DIR = Path("/app/data/certidoes")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _normalizar(texto: str) -> str:
    if not texto:
        return ""
    return texto.strip().lower()


def executar_job_ri_digital_consultar_certidao(job, login, senha):

    payload = job.get("payload_json") or {}

    protocolo_busca = payload.get("protocolo")
    data_busca = payload.get("data")
    status_busca = payload.get("status")

    project_id = job.get("project_id")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.set_default_timeout(60000)

        try:

            # ------------------------------------------------
            # LOGIN
            # ------------------------------------------------

            print("➡ Login RI Digital")

            page.goto("https://ridigital.org.br/Acesso.aspx")

            page.click("a.acesso-comum-link")

            page.fill('input[placeholder="E-mail"]', login)
            page.fill('input[placeholder="Senha"]', senha)

            page.click("#btnProsseguir")

            page.wait_for_url("**/ServicosOnline.aspx")

            print("✔ Login realizado")

            # ------------------------------------------------
            # CERTIDÃO DIGITAL
            # ------------------------------------------------

            page.click("text=Certidão Digital")

            page.wait_for_url("**/CertidaoDigital/lstPedidos.aspx")

            print("➡ Página de pedidos carregada")

            # ------------------------------------------------
            # TABELA PRINCIPAL
            # ------------------------------------------------

            page.wait_for_selector("#Grid")

            linhas = page.locator("#Grid tbody tr")

            total = linhas.count()

            print(f"➡ Processos encontrados: {total}")

            for i in range(total):

                # relocaliza tabela (evita DOM stale)
                linhas = page.locator("#Grid tbody tr")
                linha = linhas.nth(i)

                colunas = linha.locator("td")

                if colunas.count() < 4:
                    continue

                protocolo = colunas.nth(1).inner_text().strip()
                data = colunas.nth(2).inner_text().strip()
                status = colunas.nth(3).inner_text().strip()

                if protocolo_busca and protocolo_busca != protocolo:
                    continue

                if data_busca and data_busca != data:
                    continue
                if status_busca and status_busca.lower() not in status.lower():
                    continue

                print(f"➡ Abrindo processo {protocolo}")

                # abrir pedido
                colunas.nth(0).locator("a").click()

                page.wait_for_url("**/lstConsultaPedidos.aspx")

                page.wait_for_timeout(1500)

                # ------------------------------------------------
                # Nº PEDIDO
                # ------------------------------------------------

                texto_pagina = page.inner_text("body")

                numero_pedido = None

                match = re.search(r"P\d+[A-Z]", texto_pagina)

                if match:
                    numero_pedido = match.group(0)

                print(f"✔ Nº Pedido: {numero_pedido}")

                # ------------------------------------------------
                # TABELA INTERNA
                # ------------------------------------------------

                page.wait_for_selector("#Grid tbody tr")

                linhas_internas = page.locator("#Grid tbody tr")

                total_internas = linhas_internas.count()

                for j in range(total_internas):

                    linhas_internas = page.locator("table tbody tr")

                    linha_int = linhas_internas.nth(j)

                    col = linha_int.locator("td")

                    if col.count() < 5:
                        continue

                    protocolo_int = col.nth(1).inner_text().strip()
                    cartorio = col.nth(2).inner_text().strip()
                    tipo_pesquisa = col.nth(3).inner_text().strip()
                    status_int = col.nth(4).inner_text().strip()

                    print(f"➡ Item interno {protocolo_int}")

                    # ------------------------------------------------
                    # DETALHES
                    # ------------------------------------------------

                    col.nth(0).locator("a").click()

                    page.wait_for_selector("#popContent")

                    modal = page.locator("#popContent")

                    texto_modal = modal.inner_text()

                    # ------------------------------------------------
                    # CAPTURA CAMPOS
                    # ------------------------------------------------

                    protocolo_modal = None
                    matricula = None
                    finalidade = None
                    tipo_certidao = None
                    pedido_por = None

                    m = re.search(r"Nº Protocolo\s*(\S+)", texto_modal)
                    if m:
                        protocolo_modal = m.group(1)

                    m = re.search(r"Matrícula\s*(\S+)", texto_modal)
                    if m:
                        matricula = m.group(1)

                    m = re.search(r"Tipo de Finalidade\s*(.+)", texto_modal)
                    if m:
                        finalidade = m.group(1).strip()

                    m = re.search(r"Tipo de Certidão\s*(.+)", texto_modal)
                    if m:
                        tipo_certidao = m.group(1).strip()

                    m = re.search(r"Pedido Por\s*(.+)", texto_modal)
                    if m:
                        pedido_por = m.group(1).strip()

                    # fechar modal
                    modal.locator("input[value='Fechar']").click()

                    page.wait_for_timeout(800)

                    # ------------------------------------------------
                    # DOWNLOAD
                    # ------------------------------------------------

                    file_path = None

                    if _normalizar(status_int) == "respondido":

                        try:

                            print("➡ Baixando certidão")

                            with page.expect_download(timeout=30000) as download_info:
                                col.nth(6).locator("a").click()

                            download = download_info.value

                            file_path = DOWNLOAD_DIR / download.suggested_filename

                            download.save_as(file_path)

                            print(f"✔ PDF salvo: {file_path}")

                        except Exception as e:
                            print(f"⚠ Falha download: {e}")

                    # ------------------------------------------------
                    # SALVAR RESULTADO
                    # ------------------------------------------------

                    insert_result(
                        job["id"],
                        {
                            "protocolo": protocolo_modal or protocolo_int,
                            "matricula": matricula,
                            "cartorio": cartorio,
                            "data_pedido": data,
                            "file_path": str(file_path) if file_path else None,
                            "metadata_json": {
                                "numero_pedido": numero_pedido,
                                "tipo_certidao": tipo_certidao,
                                "tipo_pedido": pedido_por,
                                "status": status_int,
                                "finalidade": finalidade,
                            },
                        },
                    )

                    if file_path and project_id:

                        create_document(
                            project_id,
                            file_path.name,
                            str(file_path),
                        )

                # ------------------------------------------------
                # VOLTAR PARA LISTA
                # ------------------------------------------------

                page.go_back()

                page.wait_for_url("**/lstPedidos.aspx")

                page.wait_for_timeout(1500)

            print("✔ Consulta finalizada")

        finally:

            browser.close()