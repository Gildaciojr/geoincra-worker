from pathlib import Path
import re
import time
from typing import Any

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.db import insert_result, create_document

DOWNLOAD_DIR = Path("/app/data/certidoes")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_DIR = Path("/app/debug")
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    return " ".join(texto.strip().lower().split())


def _converter_data_ptbr_para_iso(data_str: str | None) -> str | None:
    if not data_str:
        return None

    valor = data_str.strip()
    match = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", valor)
    if not match:
        return None

    dia, mes, ano = match.groups()
    return f"{ano}-{mes}-{dia}"


def _debug_page_info(page, etapa: str) -> None:
    try:
        print(f"[DEBUG][{etapa}] URL: {page.url}")
    except Exception as e:
        print(f"[DEBUG][{etapa}] Erro ao obter URL: {e}")

    try:
        print(f"[DEBUG][{etapa}] TITLE: {page.title()}")
    except Exception as e:
        print(f"[DEBUG][{etapa}] Erro ao obter TITLE: {e}")


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


def _extrair_primeiro(texto: str, padrao: str) -> str | None:
    match = re.search(padrao, texto, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    valor = match.group(1).strip()
    return " ".join(valor.split()) if valor else None


def _extrair_bloco(
    texto: str,
    inicio: str,
    fim_opcoes: list[str] | None = None,
) -> str | None:
    if fim_opcoes:
        fim_regex = r"(?=\s*(?:" + "|".join(re.escape(item) for item in fim_opcoes) + r")\s*)"
    else:
        fim_regex = r"$"

    padrao = rf"{re.escape(inicio)}\s*(.*?){fim_regex}"
    return _extrair_primeiro(texto, padrao)


def _aguardar_tabela_principal(page) -> None:
    page.wait_for_selector("#Grid tbody tr", timeout=120000)


def _aguardar_tabela_interna(page) -> None:
    page.wait_for_selector("#Grid tbody tr", timeout=120000)


def _linha_principal_eh_cabecalho(colunas) -> bool:
    try:
        protocolo = colunas.nth(1).inner_text().strip()
        data = colunas.nth(2).inner_text().strip()
        status = colunas.nth(3).inner_text().strip()

        if _normalizar(protocolo) == "protocolo":
            return True
        if _normalizar(data) == "data":
            return True
        if _normalizar(status) == "status *" or _normalizar(status) == "status":
            return True

        return False
    except Exception:
        return False


def _linha_interna_eh_cabecalho(colunas) -> bool:
    try:
        protocolo = colunas.nth(1).inner_text().strip()
        cartorio = colunas.nth(2).inner_text().strip()
        tipo_pesquisa = colunas.nth(3).inner_text().strip()
        status = colunas.nth(4).inner_text().strip()

        if _normalizar(protocolo) == "protocolo":
            return True
        if _normalizar(cartorio) == "cartório":
            return True
        if _normalizar(tipo_pesquisa) == "tipo de pesquisa":
            return True
        if _normalizar(status) == "status":
            return True

        return False
    except Exception:
        return False


def _capturar_numero_pedido(page) -> str | None:
    texto_pagina = page.inner_text("body")
    return _extrair_primeiro(texto_pagina, r"N[ºo]\s*Pedido\s*(P\d+[A-Z])")


def _abrir_pagina_pedido(page, linha, protocolo: str) -> None:
    print(f"➡ Abrindo processo {protocolo}")

    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=120000):
            linha.locator("td").nth(0).locator("a").click()

    except PlaywrightTimeoutError:
        print("⚠ Navegação não detectada via expect_navigation, validando URL manualmente")
        linha.locator("td").nth(0).locator("a").click(force=True)

    page.wait_for_url(
        re.compile(r".*/CertidaoDigital/lstConsultaPedidos\.aspx.*"),
        timeout=120000,
    )

    _aguardar_tabela_interna(page)
    page.wait_for_timeout(1000)

    print(f"✔ Página consulta carregada: {page.url}")


def _capturar_modal_detalhes(page) -> dict[str, str | None]:
    page.wait_for_selector("#popContent", timeout=60000)
    modal = page.locator("#popContent")

    texto_modal = modal.inner_text()

    protocolo_modal = _extrair_bloco(
        texto_modal,
        "Nº Protocolo",
        [
            "Tipo de Certidão",
            "Pedido Por",
            "Cartório / Cidade",
            "Status",
            "Resposta",
            "Dados da Solicitação",
            "Tipo de Finalidade",
        ],
    )

    tipo_certidao = _extrair_bloco(
        texto_modal,
        "Tipo de Certidão",
        [
            "Pedido Por",
            "Cartório / Cidade",
            "Status",
            "Resposta",
            "Dados da Solicitação",
            "Tipo de Finalidade",
        ],
    )

    pedido_por = _extrair_bloco(
        texto_modal,
        "Pedido Por",
        [
            "Cartório / Cidade",
            "Status",
            "Resposta",
            "Dados da Solicitação",
            "Tipo de Finalidade",
        ],
    )

    cartorio_cidade_modal = _extrair_bloco(
        texto_modal,
        "Cartório / Cidade",
        [
            "Status",
            "Resposta",
            "Dados da Solicitação",
            "Tipo de Finalidade",
        ],
    )

    status_modal = _extrair_bloco(
        texto_modal,
        "Status",
        [
            "Resposta",
            "Dados da Solicitação",
            "Tipo de Finalidade",
        ],
    )

    resposta_modal = _extrair_bloco(
        texto_modal,
        "Resposta",
        [
            "Dados da Solicitação",
            "Tipo de Finalidade",
        ],
    )

    dados_solicitacao = _extrair_bloco(
        texto_modal,
        "Dados da Solicitação",
        [
            "Tipo de Finalidade",
        ],
    )

    finalidade = _extrair_bloco(
        texto_modal,
        "Tipo de Finalidade",
        None,
    )

    matricula = None

    if dados_solicitacao:
        matricula = _extrair_primeiro(
            dados_solicitacao,
            r"Matr[íi]cula\s*[:\-]?\s*([^\n\r]+)",
        )

    return {
        "protocolo_modal": protocolo_modal,
        "matricula": matricula,
        "tipo_certidao": tipo_certidao,
        "pedido_por": pedido_por,
        "cartorio_cidade_modal": cartorio_cidade_modal,
        "status_modal": status_modal,
        "resposta_modal": resposta_modal,
        "dados_solicitacao": dados_solicitacao,
        "finalidade": finalidade,
    }


def _abrir_e_capturar_detalhes(page, linha_int) -> dict[str, str | None]:
    print("➡ Abrindo detalhes do pedido")

    col = linha_int.locator("td")

    try:
        link_detalhes = col.nth(0).locator("a")
        link_detalhes.scroll_into_view_if_needed()
        page.wait_for_timeout(300)

        try:
            link_detalhes.click(timeout=30000)
        except PlaywrightTimeoutError:
            link_detalhes.click(force=True, timeout=30000)

        page.wait_for_selector("#popContent", timeout=60000)
        print("✔ Modal carregado")

        detalhes = _capturar_modal_detalhes(page)

        fechar_btn = page.locator("#popContent input[value='Fechar']")
        if fechar_btn.count() > 0:
            try:
                fechar_btn.click(timeout=15000)
            except PlaywrightTimeoutError:
                fechar_btn.click(force=True, timeout=15000)

        page.wait_for_timeout(800)

        return detalhes

    except Exception as e:
        print(f"⚠ Falha ao abrir/capturar modal: {e}")
        _debug_snapshot(page, "erro_modal_detalhes")
        return {
            "protocolo_modal": None,
            "matricula": None,
            "tipo_certidao": None,
            "pedido_por": None,
            "cartorio_cidade_modal": None,
            "status_modal": None,
            "resposta_modal": None,
            "dados_solicitacao": None,
            "finalidade": None,
        }


def _baixar_arquivo_se_disponivel(page, linha_int, status_int: str) -> str | None:
    if _normalizar(status_int) != "respondido":
        print("➡ Item não respondido, sem download")
        return None

    try:
        print("➡ Baixando certidão")

        download_link = linha_int.locator("td").nth(6).locator("a")

        if download_link.count() == 0:
            print("⚠ Coluna de download sem link disponível")
            return None

        download_link.scroll_into_view_if_needed()
        page.wait_for_timeout(300)

        with page.expect_download(timeout=60000) as download_info:
            try:
                download_link.click(timeout=15000)
            except PlaywrightTimeoutError:
                download_link.click(force=True, timeout=15000)

        download = download_info.value
        destino = DOWNLOAD_DIR / download.suggested_filename
        download.save_as(destino)

        print(f"✔ PDF salvo: {destino}")
        return str(destino)

    except Exception as e:
        print(f"⚠ Falha download: {e}")
        return None


def _voltar_para_listagem_principal(page) -> None:
    print("➡ Voltando para listagem principal")

    page.go_back(wait_until="domcontentloaded")

    page.wait_for_url(
        re.compile(r".*/CertidaoDigital/lstPedidos\.aspx.*"),
        timeout=120000,
    )

    _aguardar_tabela_principal(page)
    page.wait_for_timeout(1000)

    print("✔ Retornou para listagem principal")


def executar_job_ri_digital_consultar_certidao(job: dict[str, Any], login: str, senha: str):
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
        page.set_default_timeout(120000)

        try:
            # ------------------------------------------------
            # LOGIN
            # ------------------------------------------------
            print("➡ Login RI Digital")

            page.goto(
                "https://ridigital.org.br/Acesso.aspx",
                wait_until="domcontentloaded",
            )

            page.wait_for_selector("a.acesso-comum-link", timeout=60000)
            page.click("a.acesso-comum-link")

            page.wait_for_selector('input[placeholder="E-mail"]', timeout=60000)
            page.fill('input[placeholder="E-mail"]', login)
            page.fill('input[placeholder="Senha"]', senha)

            page.click("#btnProsseguir")
            page.wait_for_url("**/ServicosOnline.aspx", timeout=120000)
            page.wait_for_load_state("networkidle")

            print("✔ Login realizado")

            # ------------------------------------------------
            # CERTIDÃO DIGITAL
            # ------------------------------------------------
            print("➡ Acessando página Certidão Digital")

            page.goto(
                "https://ridigital.org.br/CertidaoDigital/lstPedidos.aspx",
                wait_until="domcontentloaded",
            )

            _aguardar_tabela_principal(page)

            print("✔ Página de pedidos carregada")
            _debug_page_info(page, "lst_pedidos")

            # ------------------------------------------------
            # TABELA PRINCIPAL
            # ------------------------------------------------
            linhas = page.locator("#Grid tbody tr")
            total = linhas.count()

            print(f"➡ Processos encontrados: {total}")

            for i in range(total):
                linhas = page.locator("#Grid tbody tr")
                linha = linhas.nth(i)

                colunas = linha.locator("td")
                qtd_colunas = colunas.count()

                if qtd_colunas < 4:
                    continue

                if _linha_principal_eh_cabecalho(colunas):
                    print(f"⚠ Ignorando cabeçalho da tabela principal na linha {i + 1}")
                    continue

                protocolo = colunas.nth(1).inner_text().strip()
                data = colunas.nth(2).inner_text().strip()
                data_iso = _converter_data_ptbr_para_iso(data)
                status = colunas.nth(3).inner_text().strip()

                if not protocolo:
                    continue

                if protocolo_busca and protocolo_busca != protocolo:
                    continue

                if data_busca and data_busca != data:
                    continue

                if status_busca and status_busca.lower() not in status.lower():
                    continue

                print(f"➡ Linha {i + 1}/{total}")
                print(f"   Protocolo: {protocolo}")
                print(f"   Data: {data}")
                print(f"   Status: {status}")

                _abrir_pagina_pedido(page, linha, protocolo)

                # ------------------------------------------------
                # Nº PEDIDO
                # ------------------------------------------------
                numero_pedido = _capturar_numero_pedido(page)
                print(f"✔ Nº Pedido: {numero_pedido}")

                # ------------------------------------------------
                # TABELA INTERNA
                # ------------------------------------------------
                _aguardar_tabela_interna(page)

                linhas_internas = page.locator("#Grid tbody tr")
                total_internas = linhas_internas.count()

                print(f"➡ Itens internos encontrados: {total_internas}")

                for j in range(total_internas):
                    linhas_internas = page.locator("#Grid tbody tr")
                    linha_int = linhas_internas.nth(j)

                    col = linha_int.locator("td")
                    qtd_col_int = col.count()

                    if qtd_col_int < 7:
                        continue

                    if _linha_interna_eh_cabecalho(col):
                        print(f"⚠ Ignorando cabeçalho da tabela interna na linha {j + 1}")
                        continue

                    protocolo_int = col.nth(1).inner_text().strip()
                    cartorio = col.nth(2).inner_text().strip()
                    tipo_pesquisa = col.nth(3).inner_text().strip()
                    status_int = col.nth(4).inner_text().strip()

                    if not protocolo_int:
                        print(f"⚠ Ignorando linha interna vazia na linha {j + 1}")
                        continue

                    print(f"   ➜ Item {j + 1}/{total_internas}")
                    print(f"      Protocolo interno: {protocolo_int}")
                    print(f"      Cartório: {cartorio}")
                    print(f"      Tipo pesquisa: {tipo_pesquisa}")
                    print(f"      Status: {status_int}")

                    # ------------------------------------------------
                    # DETALHES
                    # ------------------------------------------------
                    detalhes = _abrir_e_capturar_detalhes(page, linha_int)

                    # ------------------------------------------------
                    # DOWNLOAD
                    # ------------------------------------------------
                    file_path = _baixar_arquivo_se_disponivel(page, linha_int, status_int)

                    # ------------------------------------------------
                    # SALVAR RESULTADO
                    # ------------------------------------------------
                    metadata = {
                        "numero_pedido": numero_pedido,
                        "tipo_certidao": detalhes.get("tipo_certidao"),
                        "tipo_pedido": detalhes.get("pedido_por"),
                        "tipo_pesquisa": tipo_pesquisa,
                        "status": status_int,
                        "status_modal": detalhes.get("status_modal"),
                        "resposta": detalhes.get("resposta_modal"),
                        "finalidade": detalhes.get("finalidade"),
                        "cartorio_cidade_modal": detalhes.get("cartorio_cidade_modal"),
                        "dados_solicitacao": detalhes.get("dados_solicitacao"),
                        "pdf_status": "OK" if file_path else "NAO_DISPONIVEL",
                    }

                    insert_result(
                        job["id"],
                        {
                            "protocolo": detalhes.get("protocolo_modal") or protocolo_int,
                            "matricula": detalhes.get("matricula"),
                            "cartorio": cartorio,
                            "data_pedido": data_iso,
                            "file_path": file_path,
                            "metadata_json": metadata,
                        },
                    )

                    if file_path and project_id:
                        create_document(
                            project_id,
                            Path(file_path).name,
                            file_path,
                        )

                # ------------------------------------------------
                # VOLTAR PARA LISTA
                # ------------------------------------------------
                _voltar_para_listagem_principal(page)

            print("✔ Consulta finalizada")
            return True

        except Exception as e:
            print("⚠ ERRO NA AUTOMAÇÃO CONSULTAR CERTIDÃO")
            _debug_page_info(page, "erro_consultar_certidao")
            _debug_snapshot(page, "erro_consultar_certidao")
            raise Exception(f"Erro na automação RI Digital Consultar Certidão: {str(e)}")

        finally:
            browser.close()