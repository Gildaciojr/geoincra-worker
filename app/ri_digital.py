# geoincra_worker/app/ri_digital.py
import os
import re
import time
from datetime import datetime, date

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.settings import RI_DIGITAL_DIR, BACKEND_UPLOADS_BASE
from app.db import insert_result, create_document


PLAYWRIGHT_TIMEOUT = 60_000  # 60s


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _as_backend_path(worker_path: str) -> str:
    """
    Worker grava no volume em /data/...
    Backend enxerga o MESMO volume em /app/app/uploads/...
    """
    abs_path = os.path.abspath(worker_path)
    if abs_path.startswith("/data/"):
        return abs_path.replace("/data", BACKEND_UPLOADS_BASE, 1)
    return abs_path


def _parse_br_date(text: str) -> date:
    # dd/mm/yyyy
    text = (text or "").strip()
    return datetime.strptime(text, "%d/%m/%Y").date()


def _within_range(d: date, start_dt: datetime, end_dt: datetime) -> bool:
    # compara apenas por DATE (não hora)
    return start_dt.date() <= d <= end_dt.date()


def _create_document_compat(project_id: int | None, filename: str, backend_path: str) -> int | None:
    """
    Compatibilidade: seu create_document pode ter assinatura "curta" ou "longa".
    - Curta (que você usou): create_document(project_id, original_filename, file_path)
    - Longa (profissional): create_document(project_id, doc_type, stored_filename, original_filename, content_type, description, file_path)
    """
    if not project_id:
        return None

    # tentativa 1: assinatura curta
    try:
        return create_document(project_id, filename, backend_path)
    except TypeError:
        pass

    # tentativa 2: assinatura longa
    try:
        return create_document(
            project_id=project_id,
            doc_type="RI_DIGITAL_MATRICULA",
            stored_filename=os.path.basename(backend_path),
            original_filename=filename,
            content_type="application/pdf",
            description="PDF obtido via automação RI Digital (Visualização de Matrícula)",
            file_path=backend_path,
        )
    except TypeError:
        # se o backend/modelo não suportar document aqui, não quebra a automação
        return None


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
        raise Exception("Credenciais RI Digital inválidas (login/senha ausentes)")

    print(f"▶️ RI Digital | Job {job.get('id')}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        # =========================
        # 1) LOGIN (Acesso comum)
        # =========================
        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

        # O erro que você mostrou: um <a class="access-details ...> intercepta clique no texto.
        # Então:
        # - escolhemos o PRIMEIRO "Acesso comum"
        # - clicamos com force=True para ignorar interceptação
        # - confirmamos que o form abriu (inputs de email/senha visíveis)
        acesso_comum = page.get_by_text("Acesso comum").first
        try:
            acesso_comum.wait_for(state="visible", timeout=20_000)
            acesso_comum.click(force=True, timeout=20_000)
        except PlaywrightTimeoutError:
            # fallback: clicar no container do card
            card = page.locator("div:has-text('Acesso comum')").first
            card.click(force=True, timeout=20_000)

        # aguarda abrir o form
        page.locator("input[type=email]").wait_for(state="visible", timeout=20_000)
        page.locator("input[type=password]").wait_for(state="visible", timeout=20_000)

        page.fill("input[type=email]", login)
        page.fill("input[type=password]", senha)

        # botão "Entrar" (mais robusto que button[type=submit])
        btn_entrar = page.get_by_role("button", name=re.compile(r"entrar", re.I))
        if btn_entrar.count() > 0:
            btn_entrar.first.click(timeout=20_000)
        else:
            page.click("button[type=submit]")

        # Após login, deve ir para ServicosOnline.aspx
        page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)

        # =========================
        # 2) Abrir "Visualização de matrícula"
        # =========================
        # Na home (ServicosOnline.aspx) existe o card "Visualização de matrícula".
        # Clique com fallback por role/link.
        try:
            page.get_by_text("Visualização de matrícula").first.click(timeout=20_000)
        except Exception:
            page.get_by_role("link", name=re.compile("Visualização de matrícula", re.I)).first.click(timeout=20_000)

        # A tela de listagem
        page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)
        page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)

        # =========================
        # 3) Ler tabela e filtrar por data
        # =========================
        rows = page.locator("table tbody tr")
        total = rows.count()
        if total == 0:
            raise Exception("Tabela de matrículas vazia no RI Digital")

        encontrados = 0

        for i in range(total):
            row = rows.nth(i)
            cells = row.locator("td")
            if cells.count() < 5:
                continue

            # Colunas (conforme seu anexo):
            # 0 Abrir Mat. | 1 Protocolo | 2 Data | 3 Matrícula/CNM | 4 Cartório | 5 Valor Total | ...
            protocolo = cells.nth(1).inner_text().strip()
            data_txt = cells.nth(2).inner_text().strip()
            matricula_cnm = cells.nth(3).inner_text().strip()
            cartorio = cells.nth(4).inner_text().strip()

            try:
                data_pedido = _parse_br_date(data_txt)
            except Exception:
                continue

            if not _within_range(data_pedido, data_inicio, data_fim):
                continue

            encontrados += 1

            # =========================
            # 4) Abrir pedido (ícone Abrir Mat.)
            # =========================
            # O ícone normalmente é um <a> na primeira coluna.
            abrir_link = cells.nth(0).locator("a").first
            try:
                abrir_link.click(timeout=20_000)
            except Exception:
                # fallback: clicar no td inteiro (às vezes o click é delegado)
                cells.nth(0).click(timeout=20_000, force=True)

            page.wait_for_url("**/PedidoFinalizadoVM.aspx**", timeout=PLAYWRIGHT_TIMEOUT)
            page.wait_for_timeout(700)

            # =========================
            # 5) Extrair número do pedido "VMxxxxxxxxx"
            # =========================
            body_text = page.locator("body").inner_text(timeout=20_000)
            m = re.search(r"\b(VM\d{6,})\b", body_text)
            numero_pedido_vm = m.group(1) if m else None

            # =========================
            # 6) Baixar PDF (MATRÍCULA ONLINE)
            # =========================
            pdf_filename = f"{protocolo}_{matricula_cnm}.pdf".replace("/", "_").replace("\\", "_")
            worker_path = os.path.join(RI_DIGITAL_DIR, pdf_filename)
            backend_path = _as_backend_path(worker_path)

            # Link pode ser "CLIQUE AQUI PARA GERAR O PDF"
            pdf_link = page.get_by_text(re.compile(r"CLIQUE\s+AQUI\s+PARA\s+GERAR\s+O\s+PDF", re.I)).first
            if pdf_link.count() == 0:
                # fallback: link contendo "PDF"
                pdf_link = page.get_by_text(re.compile(r"\bPDF\b", re.I)).first

            if pdf_link.count() == 0:
                raise Exception("Link para gerar PDF não encontrado no pedido (PedidoFinalizadoVM)")

            with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as dl_info:
                pdf_link.click(timeout=20_000, force=True)

            download = dl_info.value
            download.save_as(worker_path)

            # (opcional) criar document no banco, se houver suporte/assinatura
            doc_id = _create_document_compat(job.get("project_id"), pdf_filename, backend_path)

            # =========================
            # 7) Registrar resultado no banco
            # =========================
            insert_result(
                job_id=job["id"],
                data={
                    "protocolo": protocolo,
                    "matricula": matricula_cnm,
                    "cnm": None,
                    "cartorio": cartorio,
                    "data_pedido": data_pedido,
                    "file_path": backend_path,
                    "metadata_json": {
                        "fonte": "RI_DIGITAL",
                        "numero_pedido_vm": numero_pedido_vm,
                        "document_id": doc_id,
                        "range": {
                            "data_inicio": data_inicio.date().isoformat(),
                            "data_fim": data_fim.date().isoformat(),
                        },
                    },
                },
            )

            # voltar para listagem
            page.go_back()
            page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)
            page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
            time.sleep(0.5)

        browser.close()

        if encontrados == 0:
            raise Exception("Nenhuma matrícula encontrada no período informado")