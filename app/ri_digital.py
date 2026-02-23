# geoincra_worker/app/ri_digital.py
import os
import re
import time
from datetime import datetime, date
from typing import Optional, Tuple

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from app.settings import RI_DIGITAL_DIR, BACKEND_UPLOADS_BASE
from app.db import insert_result, create_document

PLAYWRIGHT_TIMEOUT = 60_000  # 60s
CLICK_TIMEOUT = 20_000


# =========================================================
# Helpers
# =========================================================
def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _as_backend_path(worker_path: str) -> str:
    abs_path = os.path.abspath(worker_path)
    if abs_path.startswith("/data/"):
        return abs_path.replace("/data", BACKEND_UPLOADS_BASE, 1)
    return abs_path


def _parse_br_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d/%m/%Y").date()


def _within_range(d: date, start_dt: datetime, end_dt: datetime) -> bool:
    return start_dt.date() <= d <= end_dt.date()


def _create_document_compat(project_id, filename, backend_path):
    """
    Compatibilidade com possíveis assinaturas do create_document
    """
    if not project_id:
        return None

    try:
        # assinatura curta
        return create_document(project_id, filename, backend_path)
    except TypeError:
        try:
            # assinatura longa
            return create_document(
                project_id=project_id,
                doc_type="RI_DIGITAL_MATRICULA",
                stored_filename=os.path.basename(backend_path),
                original_filename=filename,
                content_type="application/pdf",
                description="PDF RI Digital - Visualização de Matrícula",
                file_path=backend_path,
            )
        except Exception:
            return None


def _save_debug(page, job_id: str, suffix: str):
    try:
        page.screenshot(
            path=os.path.join(RI_DIGITAL_DIR, f"debug_{job_id}_{suffix}.png"),
            full_page=True,
        )
    except Exception:
        pass


def _extract_vm_number_from_body(text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(VM\d{6,})\b", text)
    return m.group(1) if m else None


def _try_download_pdf(page, timeout_ms: int) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Tenta obter o PDF de 3 formas:
    1) download event (Playwright)
    2) popup/new page (PDF abre em nova aba)
    3) response com content-type application/pdf (inline)
    Retorna: (pdf_bytes, source) onde source = "download" | "popup" | "response"
    """
    # Botão real do PDF (seu print): <input type="image" id="btnPDF">
    btn_pdf = page.locator("#btnPDF")

    # Garantir que estamos na página correta antes de clicar
    btn_pdf.wait_for(state="visible", timeout=timeout_ms)

    # 1) Tenta como download
    try:
        with page.expect_download(timeout=timeout_ms) as dl_info:
            btn_pdf.click(force=True, timeout=CLICK_TIMEOUT)

        download = dl_info.value
        # Se for download mesmo, Playwright salva via download.save_as
        # Aqui retornamos None bytes e marcamos como "download"
        return (None, "download")
    except PlaywrightTimeoutError:
        pass

    # 2) Tenta como popup (nova aba)
    try:
        with page.expect_popup(timeout=10_000) as pop:
            btn_pdf.click(force=True, timeout=CLICK_TIMEOUT)

        popup = pop.value
        popup.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

        # popup pode disparar download ou pode abrir PDF inline
        try:
            with popup.expect_download(timeout=10_000) as dl2:
                # às vezes precisa clicar de novo no popup, mas normalmente não
                pass
            _ = dl2.value
            return (None, "popup")
        except Exception:
            # tenta capturar o conteúdo do PDF via response/URL
            # se a URL do popup já for um PDF, tenta baixar via request
            url = popup.url
            if url:
                try:
                    resp = popup.request.get(url, timeout=timeout_ms)
                    ct = (resp.headers.get("content-type") or "").lower()
                    if "pdf" in ct or url.lower().endswith(".pdf"):
                        return (resp.body(), "popup")
                except Exception:
                    pass
    except PlaywrightTimeoutError:
        pass

    # 3) Tenta via response (inline)
    def _is_pdf_response(resp) -> bool:
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            if "application/pdf" in ct:
                return True
            # fallback por URL
            u = (resp.url or "").lower()
            return ".pdf" in u
        except Exception:
            return False

    try:
        with page.expect_response(_is_pdf_response, timeout=timeout_ms) as resp_info:
            btn_pdf.click(force=True, timeout=CLICK_TIMEOUT)

        resp = resp_info.value
        return (resp.body(), "response")
    except PlaywrightTimeoutError:
        return (None, None)


# =========================================================
# Automação RI Digital
# =========================================================
def executar_ri_digital(job: dict, cred: dict):
    _ensure_dir(RI_DIGITAL_DIR)

    payload = job.get("payload_json") or {}
    if not payload.get("data_inicio") or not payload.get("data_fim"):
        raise Exception("Payload inválido: data_inicio/data_fim ausentes")

    data_inicio = datetime.fromisoformat(payload["data_inicio"])
    data_fim = datetime.fromisoformat(payload["data_fim"])

    login = (cred or {}).get("login")
    senha = (cred or {}).get("password_encrypted")
    if not login or not senha:
        raise Exception("Credenciais RI Digital inválidas")

    print(f"▶️ RI Digital | Job {job.get('id')}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)
        page.set_viewport_size({"width": 1440, "height": 900})

        job_id = str(job.get("id"))

        # =====================================================
        # 1) LOGIN — FORMA REAL CONFIRMADA
        # =====================================================
        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

        # clique no link interno que intercepta o clique do texto/card
        acesso_link = page.locator("a.access-details.acesso-comum-link").first
        acesso_link.wait_for(state="visible", timeout=CLICK_TIMEOUT)
        acesso_link.click(force=True, timeout=CLICK_TIMEOUT)

        email_input = page.locator('input[placeholder="E-mail"]')
        senha_input = page.locator('input[placeholder="Senha"]')

        email_input.wait_for(state="visible", timeout=CLICK_TIMEOUT)
        senha_input.wait_for(state="visible", timeout=CLICK_TIMEOUT)

        email_input.fill(login)
        senha_input.fill(senha)

        page.get_by_role("button", name=re.compile(r"entrar", re.I)).click()

        page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)
        print("✅ Login RI Digital realizado")
        _save_debug(page, job_id, "apos_login")

        # =====================================================
        # 2) IR DIRETO PRA MATRÍCULAS (mais robusto que clicar card)
        # =====================================================
        page.goto(
            "https://ridigital.org.br/VisualizarMatricula/DefaultVM.aspx?from=menu",
            wait_until="domcontentloaded",
        )
        page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)
        page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
        _save_debug(page, job_id, "listagem")

        rows = page.locator("table tbody tr")
        total = rows.count()
        if total == 0:
            raise Exception("Tabela de matrículas vazia")

        encontrados = 0

        # =====================================================
        # 3) VARREDURA DA TABELA
        # =====================================================
        for i in range(total):
            row = rows.nth(i)
            cells = row.locator("td")
            if cells.count() < 6:
                continue

            # colunas reais: AbrirMat | Protocolo | Data | Matricula/CNM | Cartório | Valor | ...
            try:
                data_pedido = _parse_br_date(cells.nth(2).inner_text())
            except Exception:
                continue

            if not _within_range(data_pedido, data_inicio, data_fim):
                continue

            protocolo = cells.nth(1).inner_text().strip()
            matricula = cells.nth(3).inner_text().strip()
            cartorio = cells.nth(4).inner_text().strip()

    # =================================================
    # 4) ABRIR PEDIDO (ÍCONE "Abrir Mat." = <a><img ...pasta.gif>)
    # =================================================
    abrir_link = cells.nth(0).locator("a").first
    abrir_link.wait_for(state="attached", timeout=CLICK_TIMEOUT)

    try:
        abrir_link.click(timeout=CLICK_TIMEOUT)
    except Exception:
        cells.nth(0).click(force=True, timeout=CLICK_TIMEOUT)

    page.wait_for_url("**/PedidoFinalizadoVM.aspx**", timeout=PLAYWRIGHT_TIMEOUT)
    page.wait_for_timeout(400)
    _save_debug(page, job_id, f"pedido_{i}")

    # =================================================
    # 5) EXTRAI NÚMERO DO PEDIDO VMxxxxxx
    # =================================================
    body_text = page.locator("body").inner_text(timeout=CLICK_TIMEOUT)
    numero_pedido = _extract_vm_number_from_body(body_text) or protocolo

    # =================================================
    # 6) TENTAR GERAR PDF (BEST-EFFORT — NÃO QUEBRA)
    # =================================================
    filename = f"{numero_pedido}_{matricula}.pdf".replace("/", "_").replace("\\", "_")
    worker_path = os.path.join(RI_DIGITAL_DIR, filename)
    backend_path = _as_backend_path(worker_path)

    pdf_ok = False
    pdf_motivo = None
    final_file_path = None
    doc_id = None

    try:
        page.locator("#btnPDF").click(force=True, timeout=CLICK_TIMEOUT)

        try:
            download = page.wait_for_event("download", timeout=8_000)
            download.save_as(worker_path)

            pdf_ok = True
            final_file_path = backend_path

            doc_id = _create_document_compat(
                job.get("project_id"),
                filename,
                backend_path,
            )
        except Exception:
            pdf_motivo = "PDF não disponível ou prazo expirado no RI Digital"

    except Exception:
        pdf_motivo = "Erro ao acionar botão de geração do PDF"

    # =================================================
    # 7) REGISTRA RESULTADO (SEMPRE)
    # =================================================
    insert_result(
        job_id=job["id"],
        data={
            "protocolo": protocolo,
            "matricula": matricula,
            "cartorio": cartorio,
            "data_pedido": data_pedido,
            "file_path": final_file_path,
            "metadata_json": {
                "fonte": "RI_DIGITAL",
                "numero_pedido_vm": numero_pedido,
                "pdf_status": "OK" if pdf_ok else "NAO_DISPONIVEL",
                "pdf_motivo": pdf_motivo,
                "document_id": doc_id,
                "data_consulta": data_pedido.isoformat(),
            },
        },
    )

    encontrados += 1

    # =================================================
    # 8) VOLTAR PARA LISTAGEM (NUNCA usar go_back)
    # =================================================
    page.goto(
        "https://ridigital.org.br/VisualizarMatricula/DefaultVM.aspx?from=menu",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
    time.sleep(0.4)

    browser.close()

    if encontrados == 0:
            raise Exception("Nenhuma matrícula encontrada no período informado")

            print("🏁 RI Digital finalizado com sucesso")