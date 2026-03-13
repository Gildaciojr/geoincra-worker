from pathlib import Path
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.db import insert_result, create_document


DOWNLOAD_DIR = Path("/data/ri-digital")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_DIR = Path("/app/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def _wait_enabled(ctx, selector: str, timeout: int = 30000) -> None:
    ctx.wait_for_function(
        """
        (sel) => {
            const el = document.querySelector(sel);
            return !!el && !el.disabled;
        }
        """,
        arg=selector,
        timeout=timeout,
    )


def _debug_page_info(page, etapa: str) -> None:
    try:
        print(f"[DEBUG][{etapa}] URL: {page.url}")
    except Exception as e:
        print(f"[DEBUG][{etapa}] Erro ao obter URL: {e}")

    try:
        print(f"[DEBUG][{etapa}] TITLE: {page.title()}")
    except Exception as e:
        print(f"[DEBUG][{etapa}] Erro ao obter TITLE: {e}")


def _debug_frames(page, etapa: str) -> None:
    print(f"[DEBUG][{etapa}] === FRAMES DA PÁGINA ===")
    try:
        for i, frame in enumerate(page.frames):
            try:
                print(f"[DEBUG][{etapa}] FRAME[{i}] URL: {frame.url}")
            except Exception as e:
                print(f"[DEBUG][{etapa}] FRAME[{i}] erro: {e}")
    except Exception as e:
        print(f"[DEBUG][{etapa}] Erro ao listar frames: {e}")
    print(f"[DEBUG][{etapa}] ========================")


def _debug_snapshot(page, label: str) -> None:
    try:
        ts = int(time.time())
        png_path = DEBUG_DIR / f"{label}_{ts}.png"
        html_path = DEBUG_DIR / f"{label}_{ts}.html"

        page.screenshot(path=str(png_path), full_page=True)

        html = page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"[DEBUG] Screenshot salvo: {png_path}")
        print(f"[DEBUG] HTML salvo: {html_path}")

    except Exception as e:
        print(f"[DEBUG] Falha ao gerar snapshot '{label}': {e}")


def _find_map_context(page):
    """
    Procura o mapa no DOM principal e depois em frames.
    """

    try:
        if page.locator("#svg-map-brasil").count() > 0:
            print("[DEBUG] Mapa encontrado no DOM principal")
            return page
    except Exception:
        pass

    for i, frame in enumerate(page.frames):
        try:
            if frame.locator("#svg-map-brasil").count() > 0:
                print(f"[DEBUG] Mapa encontrado no FRAME[{i}]")
                return frame
        except Exception:
            pass

    return None


def executar_job_ri_digital_solicitar_certidao(job, login, senha):

    payload = job["payload_json"]

    cidade = payload["cidade"]
    cartorio = payload["cartorio"]
    matricula = payload["matricula"]
    finalidade = str(payload["finalidade"])

    project_id = job.get("project_id")

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.set_default_timeout(60000)

        # LOGS
        page.on("console", lambda msg: print(f"[PAGE CONSOLE] {msg.type}: {msg.text}"))
        page.on("pageerror", lambda e: print(f"[PAGE ERROR] {e}"))
        page.on("requestfailed", lambda r: print(f"[REQUEST FAILED] {r.url}"))

        context.tracing.start(
            screenshots=True,
            snapshots=True,
            sources=True,
        )

        try:

            print("➡ Iniciando automação RI Digital Certidão")

            # LOGIN
            print("➡ Abrindo página de login")

            page.goto(
                "https://ridigital.org.br/Acesso.aspx",
                wait_until="domcontentloaded",
            )

            _debug_page_info(page, "login_aberto")

            page.wait_for_selector("a.acesso-comum-link")
            page.click("a.acesso-comum-link")

            page.wait_for_selector('input[placeholder="E-mail"]')

            print("➡ Preenchendo login")

            page.fill('input[placeholder="E-mail"]', login)
            page.fill('input[placeholder="Senha"]', senha)

            page.click("#btnProsseguir")

            page.wait_for_url("**/ServicosOnline.aspx")

            print("✔ Login realizado com sucesso")

            _debug_page_info(page, "login_ok")

            # SERVIÇOS

            print("➡ Abrindo serviços")

            page.goto(
                "https://ridigital.org.br/ServicosOnline.aspx",
                wait_until="domcontentloaded",
            )

            _debug_page_info(page, "servicos")

            # CERTIDÃO DIGITAL

            print("➡ Abrindo Certidão Digital")

            page.wait_for_selector(
                "#form1 > div.servicos__cards__v2 > div > div:nth-child(2) > div:nth-child(1) > a"
            )

            page.click(
                "#form1 > div.servicos__cards__v2 > div > div:nth-child(2) > div:nth-child(1) > a"
            )

            page.wait_for_load_state("networkidle")

            _debug_page_info(page, "certidao_digital")

            # NOVO PEDIDO

            print("➡ Aguardando botão +Novo Pedido")

            page.wait_for_selector("#Ul1 > a.subheader__action-btn")

            print("➡ Clicando em +Novo Pedido")

            page.locator("#Ul1 > a.subheader__action-btn").click()

            page.wait_for_url("**/CertidaoDigital/Default.aspx")

            print("✔ Página de novo pedido carregada")

            page.wait_for_timeout(2000)

            _debug_page_info(page, "novo_pedido")
            _debug_frames(page, "novo_pedido")
            _debug_snapshot(page, "antes_busca_mapa")

            # MAPA

            print("➡ Aguardando mapa do Brasil")

            ctx = _find_map_context(page)

            if not ctx:

                page.wait_for_timeout(3000)

                _debug_snapshot(page, "segunda_tentativa_mapa")

                ctx = _find_map_context(page)

            if not ctx:
                raise Exception("Mapa não encontrado")

            ctx.wait_for_selector("#svg-map-brasil")

            ctx.wait_for_selector("#svg-map-brasil a[name='Rondônia']")

            print("➡ Selecionando estado Rondônia")

            estado = ctx.locator("#svg-map-brasil a[name='Rondônia']").first

            estado.scroll_into_view_if_needed()

            page.wait_for_timeout(500)

            try:
                estado.click()
            except PlaywrightTimeoutError:
                estado.click(force=True)

            print("✔ Estado selecionado")

            _debug_page_info(page, "apos_estado")
            _debug_snapshot(page, "apos_estado")

            # TERMO

            print("➡ Aguardando tela de termo")

            ctx.wait_for_selector("#Contrato_btnGoNext")

            _wait_enabled(ctx, "#Contrato_btnGoNext")

            print("✔ Tela de termo carregada")

            ctx.click("#Contrato_btnGoNext")

            page.wait_for_load_state("networkidle")

                        # ------------------------------------------------
            # PASSO — CIDADE E CARTÓRIO
            # ------------------------------------------------

            print(f"➡ Selecionando cidade: {cidade}")

            ctx.wait_for_selector("#Cartorio_ddlCidade", timeout=60000)

            # aguarda opções carregarem
            ctx.wait_for_function(
                """
                () => {
                    const sel = document.querySelector('#Cartorio_ddlCidade');
                    return sel && sel.options && sel.options.length > 1;
                }
                """
            )

            cidade_normalizada = cidade.strip().lower()

            opcoes_cidade = ctx.locator("#Cartorio_ddlCidade option").all()

            cidade_value = None

            for opt in opcoes_cidade:
                texto = opt.inner_text().strip().lower()

                if cidade_normalizada in texto:
                    cidade_value = opt.get_attribute("value")
                    break

            if not cidade_value:
                raise Exception(f"Cidade '{cidade}' não encontrada")

            ctx.select_option("#Cartorio_ddlCidade", value=cidade_value)

            print("✔ Cidade selecionada")

            # ------------------------------------------------
            # AGUARDAR POSTBACK DO ASP.NET
            # ------------------------------------------------

            ctx.wait_for_load_state("networkidle")

            ctx.wait_for_function(
                """
                () => {
                    const sel = document.querySelector('#Cartorio_ddlCartorio');
                    return sel && sel.options && sel.options.length >= 1;
                }
                """
            )

                       # ------------------------------------------------
            # CARTÓRIO
            # ------------------------------------------------

            print(f"➡ Selecionando cartório: {cartorio}")

            # aguarda cartórios carregarem após postback da cidade
            ctx.wait_for_function(
                """
                () => {
                    const sel = document.querySelector('#Cartorio_ddlCartorio');
                    return sel && sel.options && sel.options.length > 1;
                }
                """,
                timeout=60000
            )

            opcoes_cartorio = ctx.locator("#Cartorio_ddlCartorio option").all()

            # remove "(Selecione)"
            opcoes_validas = []

            for opt in opcoes_cartorio:

                value = opt.get_attribute("value")

                if value and value != "-1":

                    opcoes_validas.append(opt)

            # ------------------------------------------------
            # CASO 1 — SOMENTE UM CARTÓRIO
            # ------------------------------------------------

            if len(opcoes_validas) == 1:

                unico = opcoes_validas[0]

                cartorio_value = unico.get_attribute("value")
                cartorio_label = unico.inner_text().strip()

                ctx.select_option("#Cartorio_ddlCartorio", value=cartorio_value)

                print(f"✔ Cartório único selecionado automaticamente: {cartorio_label}")

            else:

                # ------------------------------------------------
                # CASO 2 — MAIS DE UM CARTÓRIO
                # ------------------------------------------------

                cartorio_input = str(cartorio).strip().lower()

                cartorio_value = None
                cartorio_label = None

                for opt in opcoes_validas:

                    value = opt.get_attribute("value")
                    texto = opt.inner_text().strip().lower()

                    texto_limpo = (
                        texto.replace("º", "")
                        .replace("-", "")
                        .replace("  ", " ")
                        .strip()
                    )

                    # ------------------------------------------------
                    # CASO A — VALUE DIRETO (2663)
                    # ------------------------------------------------

                    if cartorio_input == value:

                        cartorio_value = value
                        cartorio_label = opt.inner_text().strip()
                        break

                    # ------------------------------------------------
                    # CASO B — NÚMERO (1 → 01º)
                    # ------------------------------------------------

                    if texto_limpo.startswith(cartorio_input):

                        cartorio_value = value
                        cartorio_label = opt.inner_text().strip()
                        break

                    # ------------------------------------------------
                    # CASO C — TEXTO COMPLETO
                    # ------------------------------------------------

                    if cartorio_input in texto:

                        cartorio_value = value
                        cartorio_label = opt.inner_text().strip()
                        break

                if not cartorio_value:

                    raise Exception(
                        f"Cartório '{cartorio}' não encontrado nas opções disponíveis"
                    )

                ctx.select_option("#Cartorio_ddlCartorio", value=cartorio_value)

                print(f"✔ Cartório selecionado: {cartorio_label}")

            page.wait_for_timeout(1000)

            # ------------------------------------------------
            # PROSSEGUIR
            # ------------------------------------------------

            print("➡ Prosseguindo")

            _wait_enabled(ctx, "#Cartorio_btnGoNext")

            ctx.click("#Cartorio_btnGoNext")

            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # TIPO CERTIDAO
            # ------------------------------------------------

            print("➡ Selecionando tipo certidão")

            ctx.wait_for_selector("#TipoCertidao_ddlTipoCertidao", timeout=60000)

            ctx.select_option("#TipoCertidao_ddlTipoCertidao", value="3")

            ctx.wait_for_timeout(500)

            ctx.select_option("#TipoCertidao_ddlPedidoPor", value="4")

            page.wait_for_timeout(1000)

            print("➡ Prosseguindo")

            _wait_enabled(ctx, "#TipoCertidao_btnGoNext")

            ctx.click("#TipoCertidao_btnGoNext")

            # aguarda ASP.NET atualizar tela
            ctx.wait_for_selector("#txtTag", timeout=60000)

                       # ------------------------------------------------
            # MATRÍCULA
            # ------------------------------------------------

            print(f"➡ Informando matrícula {matricula}")

            ctx.wait_for_selector("#txtTag", timeout=60000)

            ctx.fill("#txtTag", "")

            ctx.fill("#txtTag", matricula)

            # confirmar matrícula (teclado pertence à page, não ao frame)
            page.keyboard.press("Enter")

            page.wait_for_timeout(1000)

            print("➡ Prosseguindo")

            _wait_enabled(ctx, "#PorMatriculaComComplemento_btnGoNext")

            ctx.click("#PorMatriculaComComplemento_btnGoNext")

            page.wait_for_load_state("networkidle")

            # ------------------------------------------------
            # CAPTURAR TABELA DE CONFIRMAÇÃO
            # ------------------------------------------------

            print("➡ Capturando dados da tabela de confirmação")

            resultados = []

            ctx.wait_for_selector("table tbody tr", timeout=60000)

            linhas = ctx.locator("table tbody tr").all()

            for linha in linhas:

                colunas = linha.locator("td").all()

                # tabela esperada:
                # 0 detalhes
                # 1 número
                # 2 cartório
                # 3 tipo certidão
                # 4 tipo pedido
                # 5 prazo
                # 6 valor
                # 7 excluir
                if len(colunas) < 7:
                    continue

                numero = colunas[1].inner_text().strip()
                cartorio_nome = colunas[2].inner_text().strip()
                tipo_certidao = colunas[3].inner_text().strip()
                tipo_pedido = colunas[4].inner_text().strip()
                prazo = colunas[5].inner_text().strip()
                valor = colunas[6].inner_text().strip()

                # ignora linha total / vazias
                if not numero:
                    continue

                if numero.lower() == "total":
                    continue

                resultados.append(
                    {
                        "numero": numero,
                        "cartorio": cartorio_nome,
                        "tipo_certidao": tipo_certidao,
                        "tipo_pedido": tipo_pedido,
                        "prazo": prazo,
                        "valor": valor,
                    }
                )

            print(f"✔ Itens capturados da tabela: {len(resultados)}")

            # ------------------------------------------------
            # FINALIDADE
            # ------------------------------------------------

            print(f"➡ Selecionando finalidade {finalidade}")

            ctx.wait_for_selector("#Confirmacao_ddlTipoFinalidade", timeout=60000)

            ctx.select_option("#Confirmacao_ddlTipoFinalidade", value=finalidade)

            # ASP.NET faz micro atualização da tela
            page.wait_for_timeout(1500)
            # ------------------------------------------------
            # PAGAMENTO
            # ------------------------------------------------

            print("➡ Pagamento saldo")

            ctx.wait_for_selector("#Confirmacao_btnSaldoCreditos", timeout=60000)

            ctx.click("#Confirmacao_btnSaldoCreditos")

            # micro renderização após escolher forma de pagamento
            page.wait_for_timeout(1500)

            # aguarda botão concluir ficar habilitado
            _wait_enabled(ctx, "#Confirmacao_btnConcluirPedido", timeout=60000)

            # ------------------------------------------------
            # CONCLUIR
            # ------------------------------------------------

            print("➡ Concluindo pedido")

            ctx.click("#Confirmacao_btnConcluirPedido")

            # aguarda a tela reagir
            page.wait_for_timeout(4000)

            # tenta aguardar algum indício de finalização:
            # protocolo ou link de download
            try:
                ctx.wait_for_function(
                    """
                    () => {
                        return !!document.querySelector("a[href*='Download']")
                            || !!document.body.innerText.match(/protocolo/i)
                            || !!document.body.innerText.match(/pedido realizado/i);
                    }
                    """,
                    timeout=60000
                )
            except Exception:
                pass

            # ------------------------------------------------
            # DOWNLOAD
            # ------------------------------------------------

            print("➡ Procurando downloads")

            arquivos_pdf = []

            pdf_links = ctx.locator("a[href*='Download']").all()

            for link in pdf_links:

                try:

                    with page.expect_download(timeout=30000) as download_info:
                        link.click()

                    download = download_info.value

                    file_path = DOWNLOAD_DIR / download.suggested_filename

                    download.save_as(file_path)

                    arquivos_pdf.append(str(file_path))

                    print(f"✔ Download realizado: {file_path.name}")

                except Exception as e:
                    print(f"⚠ Falha ao baixar arquivo: {e}")

            # ------------------------------------------------
            # SALVAR RESULTADOS
            # ------------------------------------------------

            print("➡ Salvando resultados")

            if resultados:

                for i, r in enumerate(resultados):

                    pdf_path = arquivos_pdf[i] if i < len(arquivos_pdf) else (
                        arquivos_pdf[0] if arquivos_pdf else None
                    )

                    relative_path = None

                    if pdf_path:
                        relative_path = f"ri-digital/{Path(pdf_path).name}"

                    metadata = {
                        "tipo_certidao": r["tipo_certidao"],
                        "tipo_pedido": r["tipo_pedido"],
                        "prazo": r["prazo"],
                        "valor": r["valor"],
                        "pdf_status": "OK" if pdf_path else "NAO_DISPONIVEL",
                    }

                    insert_result(
                        job["id"],
                        {
                            "protocolo": r["numero"],
                            "matricula": matricula,
                            "cartorio": r["cartorio"],
                            "data_pedido": None,
                            "file_path": relative_path,
                            "metadata_json": metadata,
                        },
                    )

                    if pdf_path and project_id:

                        filename = Path(pdf_path).name

                        create_document(
                            project_id,
                            filename,
                            relative_path,
                        )

            else:

                # fallback: se não conseguiu capturar tabela, ainda salva PDFs
                for pdf_path in arquivos_pdf:

                    relative_path = f"ri-digital/{Path(pdf_path).name}"

                    metadata = {
                        "pdf_status": "OK"
                    }

                    insert_result(
                        job["id"],
                        {
                            "protocolo": None,
                            "matricula": matricula,
                            "cartorio": cartorio,
                            "data_pedido": None,
                            "file_path": relative_path,
                            "metadata_json": metadata,
                        },
                    )

                    if project_id:

                        filename = Path(pdf_path).name

                        create_document(
                            project_id,
                            filename,
                            relative_path,
                        )

            print("✔ Automação finalizada com sucesso")

            context.tracing.stop(path=str(DEBUG_DIR / "trace.zip"))

            browser.close()

            return True

        except Exception as e:

            print("⚠ ERRO NA AUTOMAÇÃO")

            _debug_page_info(page, "erro")

            _debug_frames(page, "erro")

            _debug_snapshot(page, "erro_automacao")

            try:
                context.tracing.stop(path=str(DEBUG_DIR / "trace.zip"))
            except Exception:
                pass

            try:
                page.screenshot(
                    path="/app/data/ri_digital_solicitar_certidao_erro.png",
                    full_page=True,
                )
            except Exception:
                pass

            browser.close()

            raise Exception(f"Erro na automação RI Digital Certidão: {str(e)}")