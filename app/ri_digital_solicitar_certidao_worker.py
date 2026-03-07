import time
from pathlib import Path
from playwright.sync_api import sync_playwright

from app.db import insert_result, create_document


DOWNLOAD_DIR = Path("/app/data/certidoes")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def executar_job_ri_digital_solicitar_certidao(job, login, senha):

    payload = job["payload_json"]

    cidade = payload["cidade"]
    cartorio = payload["cartorio"]
    matricula = payload["matricula"]
    finalidade = str(payload["finalidade"])

    project_id = job.get("project_id")

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        # aumenta timeout padrão para evitar falhas
        page.set_default_timeout(60000)

        try:

            # ------------------------------------------------
            # LOGIN RI DIGITAL
            # ------------------------------------------------
            page.goto("https://ridigital.org.br/Acesso.aspx")

            page.wait_for_selector("a.acesso-comum-link")
            page.click("a.acesso-comum-link")

            page.wait_for_selector('input[placeholder="E-mail"]')

            page.fill('input[placeholder="E-mail"]', login)
            page.fill('input[placeholder="Senha"]', senha)

            page.click("#btnProsseguir")

            page.wait_for_url("**/ServicosOnline.aspx")

            # ------------------------------------------------
            # SERVIÇOS
            # ------------------------------------------------
            page.goto("https://ridigital.org.br/ServicosOnline.aspx")

            # ------------------------------------------------
            # PASSO 01 — CERTIDÃO DIGITAL
            # ------------------------------------------------
            page.click("#form1 > div.servicos__cards__v2 > div > div:nth-child(2) > div:nth-child(1) > a")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 02 — NOVO PEDIDO
            # ------------------------------------------------
            page.click("#Ul1 > a.subheader__action-btn")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 03 — SELECIONAR ESTADO NO MAPA
            # ------------------------------------------------
            page.wait_for_load_state("domcontentloaded")

            page.wait_for_selector("#svg-map-brasil", timeout=60000)

            estado = page.locator("#svg-map-brasil text", has_text="RO")

            estado.wait_for(timeout=60000)

            estado.click()

            page.wait_for_timeout(2000)

            # ------------------------------------------------
            # PASSO 04 — PROSSEGUIR
            # ------------------------------------------------
            page.click("#Contrato_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 05 — CIDADE E CARTÓRIO
            # ------------------------------------------------
            page.select_option("#Cartorio_ddlCidade", label=cidade)
            page.wait_for_timeout(2000)

            page.select_option("#Cartorio_ddlCartorio", label=cartorio)
            page.wait_for_timeout(1000)

            page.click("#Cartorio_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 06 — TIPO CERTIDÃO
            # ------------------------------------------------
            page.select_option("#TipoCertidao_ddlTipoCertidao", value="3")
            page.select_option("#TipoCertidao_ddlPedidoPor", value="4")

            page.click("#TipoCertidao_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 07 — MATRÍCULA
            # ------------------------------------------------
            page.fill("#txtTag", matricula)

            page.click("#PorMatriculaComComplemento_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 08 — FINALIDADE
            # ------------------------------------------------
            page.select_option("#Confirmacao_ddlTipoFinalidade", value=finalidade)

            page.wait_for_timeout(2000)

            # ------------------------------------------------
            # CAPTURA DADOS DA TABELA
            # ------------------------------------------------
            resultados = []

            linhas = page.query_selector_all("table tbody tr")

            for linha in linhas:

                colunas = linha.query_selector_all("td")

                if len(colunas) < 6:
                    continue

                numero = colunas[1].inner_text().strip()
                cartorio_nome = colunas[2].inner_text().strip()
                tipo_certidao = colunas[3].inner_text().strip()
                tipo_pedido = colunas[4].inner_text().strip()
                prazo = colunas[5].inner_text().strip()

                resultados.append({
                    "numero": numero,
                    "cartorio": cartorio_nome,
                    "tipo_certidao": tipo_certidao,
                    "tipo_pedido": tipo_pedido,
                    "prazo": prazo
                })

            # ------------------------------------------------
            # PAGAMENTO
            # ------------------------------------------------
            page.click("#Confirmacao_btnSaldoCreditos")

            page.wait_for_timeout(2000)

            page.click("#Confirmacao_btnConcluirPedido")

            page.wait_for_timeout(4000)

            # ------------------------------------------------
            # DOWNLOAD PDF
            # ------------------------------------------------
            pdf_links = page.query_selector_all("a[href*='.pdf']")

            arquivos_pdf = []

            for link in pdf_links:

                href = link.get_attribute("href")

                if not href:
                    continue

                with page.expect_download() as download_info:
                    link.click()

                download = download_info.value

                file_path = DOWNLOAD_DIR / download.suggested_filename

                download.save_as(file_path)

                arquivos_pdf.append(str(file_path))

            # ------------------------------------------------
            # SALVAR RESULTADOS
            # ------------------------------------------------
            for r in resultados:

                pdf_path = arquivos_pdf[0] if arquivos_pdf else None

                metadata = {
                    "tipo_certidao": r["tipo_certidao"],
                    "tipo_pedido": r["tipo_pedido"],
                    "prazo": r["prazo"],
                    "pdf_status": "OK" if pdf_path else "NAO_DISPONIVEL"
                }

                insert_result(job["id"], {
                    "protocolo": r["numero"],
                    "matricula": matricula,
                    "cartorio": r["cartorio"],
                    "data_pedido": None,
                    "file_path": pdf_path,
                    "metadata_json": metadata
                })

                if pdf_path and project_id:

                    filename = Path(pdf_path).name

                    create_document(project_id, filename, pdf_path)

            browser.close()

            return True

        except Exception as e:

            browser.close()

            raise Exception(f"Erro na automação RI Digital Certidão: {str(e)}")