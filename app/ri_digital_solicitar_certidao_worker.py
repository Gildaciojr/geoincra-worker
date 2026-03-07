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

        # timeout global
        page.set_default_timeout(60000)

        try:
            print("➡ Iniciando automação RI Digital Certidão")

            # ------------------------------------------------
            # LOGIN RI DIGITAL
            # ------------------------------------------------
            print("➡ Abrindo página de login")
            page.goto("https://ridigital.org.br/Acesso.aspx")

            page.wait_for_selector("a.acesso-comum-link", timeout=60000)
            page.click("a.acesso-comum-link")

            page.wait_for_selector('input[placeholder="E-mail"]', timeout=60000)

            print("➡ Preenchendo login")
            page.fill('input[placeholder="E-mail"]', login)
            page.fill('input[placeholder="Senha"]', senha)

            page.click("#btnProsseguir")

            page.wait_for_url("**/ServicosOnline.aspx", timeout=60000)
            print("✔ Login realizado com sucesso")

            # ------------------------------------------------
            # SERVIÇOS
            # ------------------------------------------------
            print("➡ Abrindo serviços")
            page.goto("https://ridigital.org.br/ServicosOnline.aspx")

            # ------------------------------------------------
            # PASSO 01 — CERTIDÃO DIGITAL
            # ------------------------------------------------
            print("➡ Abrindo Certidão Digital")
            page.click("#form1 > div.servicos__cards__v2 > div > div:nth-child(2) > div:nth-child(1) > a")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 02 — NOVO PEDIDO (ACESSO DIRETO)
            # ------------------------------------------------
            print("➡ Abrindo página de novo pedido")
            page.goto("https://ridigital.org.br/CertidaoDigital/Default.aspx")

            # ------------------------------------------------
            # PASSO 03 — MAPA (ESCOLHER ESTADO)
            # ------------------------------------------------
            print("➡ Carregando mapa do Brasil")

            page.wait_for_load_state("domcontentloaded")
            page.wait_for_selector("#svg-map-brasil", timeout=60000)

            print("➡ Selecionando estado RO")

            estado = page.locator("#svg-map-brasil text", has_text="RO")
            estado.wait_for(timeout=60000)
            estado.click()

            page.wait_for_timeout(2000)

            print("➡ Prosseguindo após selecionar estado")

            page.wait_for_selector("#Contrato_btnGoNext", timeout=60000)
            page.click("#Contrato_btnGoNext")

            # ------------------------------------------------
            # PASSO 04 — TELA TERMO
            # ------------------------------------------------
            print("➡ Tela de termo carregada")

            page.wait_for_selector("#Contrato_btnGoNext", timeout=60000)

            page.wait_for_function(
                "document.querySelector('#Contrato_btnGoNext') && !document.querySelector('#Contrato_btnGoNext').disabled"
            )

            print("➡ Aceitando termo")

            page.click("#Contrato_btnGoNext")

            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 05 — CIDADE E CARTÓRIO
            # ------------------------------------------------
            print(f"➡ Selecionando cidade: {cidade}")
            page.wait_for_selector("#Cartorio_ddlCidade", timeout=60000)
            page.select_option("#Cartorio_ddlCidade", label=cidade)

            # postback ASP.NET para carregar cartórios
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("#Cartorio_ddlCartorio option:not([value='-1'])", timeout=60000)

            print(f"➡ Selecionando cartório: {cartorio}")
            page.select_option("#Cartorio_ddlCartorio", label=cartorio)
            page.wait_for_timeout(1000)

            print("➡ Prosseguindo para tipo de certidão")
            page.click("#Cartorio_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 06 — TIPO CERTIDÃO
            # FIXO NO WORKER:
            # Tipo Certidão = Matrícula - Inteiro Teor (3)
            # Pedido Por = Nº de Matrícula/CNM (4)
            # ------------------------------------------------
            print("➡ Selecionando tipo de certidão")
            page.wait_for_selector("#TipoCertidao_ddlTipoCertidao", timeout=60000)
            page.select_option("#TipoCertidao_ddlTipoCertidao", value="3")

            # postback ASP.NET
            page.wait_for_load_state("networkidle")

            print("➡ Selecionando pedido por matrícula/CNM")
            page.wait_for_selector("#TipoCertidao_ddlPedidoPor", timeout=60000)
            page.select_option("#TipoCertidao_ddlPedidoPor", value="4")

            page.wait_for_timeout(1000)

            print("➡ Prosseguindo para pesquisa por matrícula")
            page.click("#TipoCertidao_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 07 — MATRÍCULA
            # Campo usa sistema de TAGS JS, precisa confirmar com Enter
            # ------------------------------------------------
            print(f"➡ Informando matrícula: {matricula}")
            page.wait_for_selector("#txtTag", timeout=60000)
            page.fill("#txtTag", matricula)

            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)

            print("➡ Prosseguindo para confirmação")
            page.click("#PorMatriculaComComplemento_btnGoNext")
            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 08 — CAPTURA DADOS DA TABELA DE CONFIRMAÇÃO
            # ------------------------------------------------
            print("➡ Capturando dados da tabela de confirmação")
            resultados = []

            linhas = page.locator("table tbody tr").all()

            for linha in linhas:

                colunas = linha.locator("td").all()

                if len(colunas) < 6:
                    continue

                numero = colunas[1].inner_text().strip()
                cartorio_nome = colunas[2].inner_text().strip()
                tipo_certidao = colunas[3].inner_text().strip()
                tipo_pedido = colunas[4].inner_text().strip()
                prazo = colunas[5].inner_text().strip()

                if not numero or numero.lower() == "total":
                    continue

                resultados.append({
                    "numero": numero,
                    "cartorio": cartorio_nome,
                    "tipo_certidao": tipo_certidao,
                    "tipo_pedido": tipo_pedido,
                    "prazo": prazo,
                })

            # ------------------------------------------------
            # PASSO 09 — FINALIDADE
            # ------------------------------------------------
            print(f"➡ Selecionando finalidade: {finalidade}")
            page.wait_for_selector("#Confirmacao_ddlTipoFinalidade", timeout=60000)
            page.select_option("#Confirmacao_ddlTipoFinalidade", value=finalidade)

            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 10 — PAGAMENTO
            # ------------------------------------------------
            print("➡ Selecionando pagamento por saldo em créditos")
            page.wait_for_selector("#Confirmacao_btnSaldoCreditos", timeout=60000)
            page.click("#Confirmacao_btnSaldoCreditos")

            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # PASSO 11 — CONCLUIR PEDIDO
            # ------------------------------------------------
            print("➡ Concluindo pedido")
            page.wait_for_selector("#Confirmacao_btnConcluirPedido", timeout=60000)
            page.click("#Confirmacao_btnConcluirPedido")

            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(4000)

            # ------------------------------------------------
            # PASSO 12 — DOWNLOAD PDF
            # ------------------------------------------------
            print("➡ Procurando links de download")
            pdf_links = page.query_selector_all("a[href*='Download']")

            arquivos_pdf = []

            for link in pdf_links:

                href = link.get_attribute("href")

                if not href:
                    continue

                try:
                    with page.expect_download() as download_info:
                        link.click()

                    download = download_info.value

                    file_path = DOWNLOAD_DIR / download.suggested_filename
                    download.save_as(file_path)

                    arquivos_pdf.append(str(file_path))
                except Exception:
                    continue

            # ------------------------------------------------
            # PASSO 13 — SALVAR RESULTADOS
            # ------------------------------------------------
            print("➡ Salvando resultados no banco")
            for r in resultados:

                pdf_path = arquivos_pdf[0] if arquivos_pdf else None

                metadata = {
                    "tipo_certidao": r["tipo_certidao"],
                    "tipo_pedido": r["tipo_pedido"],
                    "prazo": r["prazo"],
                    "pdf_status": "OK" if pdf_path else "NAO_DISPONIVEL",
                }

                insert_result(job["id"], {
                    "protocolo": r["numero"],
                    "matricula": matricula,
                    "cartorio": r["cartorio"],
                    "data_pedido": None,
                    "file_path": pdf_path,
                    "metadata_json": metadata,
                })

                if pdf_path and project_id:
                    filename = Path(pdf_path).name
                    create_document(project_id, filename, pdf_path)

            print("✔ Automação RI Digital Certidão finalizada com sucesso")
            browser.close()

            return True

        except Exception as e:
            browser.close()
            raise Exception(f"Erro na automação RI Digital Certidão: {str(e)}")