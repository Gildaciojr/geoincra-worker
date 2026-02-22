# geoincra_worker/app/ri_digital.py
import os
import re
import time
from datetime import datetime, date

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.settings import RI_DIGITAL_DIR, BACKEND_UPLOADS_BASE
from app.db import insert_result, create_document

PLAYWRIGHT_TIMEOUT = 60_000  # 60s


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

    login = cred.get("login")
    senha = cred.get("password_encrypted")
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

        # =====================================================
        # 1) LOGIN — FORMA REAL CONFIRMADA
        # =====================================================
        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

        # 🔑 CLIQUE REAL NO LINK INTERNO DO CARD "ACESSO COMUM"
        acesso_link = page.locator("a.access-details.acesso-comum-link").first
        acesso_link.wait_for(state="visible", timeout=20_000)
        acesso_link.click(force=True, timeout=20_000)

        # Aguarda inputs reais (NÃO são type=email)
        email_input = page.locator('input[placeholder="E-mail"]')
        senha_input = page.locator('input[placeholder="Senha"]')

        email_input.wait_for(state="visible", timeout=20_000)
        senha_input.wait_for(state="visible", timeout=20_000)

        email_input.fill(login)
        senha_input.fill(senha)

        # Botão Entrar
        page.get_by_role("button", name=re.compile(r"entrar", re.I)).click()

        # Confirma login
        page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)
        print("✅ Login RI Digital realizado")

        # =====================================================
        # 2) VISUALIZAÇÃO DE MATRÍCULA
        # =====================================================
        page.get_by_text("Visualização de matrícula").first.click()
        page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)
        page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)

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
            # 4) ABRIR MATRÍCULA (ÍCONE "ABRIR MAT.")
            # =================================================
            abrir_link = cells.nth(0).locator("a").first
            try:
                abrir_link.click(timeout=20_000)
            except Exception:
                cells.nth(0).click(force=True, timeout=20_000)

            page.wait_for_url("**/PedidoFinalizadoVM.aspx**", timeout=PLAYWRIGHT_TIMEOUT)

            # =================================================
            # 5) EXTRAI NÚMERO DO PEDIDO VMxxxxxx
            # =================================================
            body_text = page.locator("body").inner_text(timeout=20_000)
            m = re.search(r"\b(VM\d{6,})\b", body_text)
            numero_pedido = m.group(1) if m else protocolo

            # =================================================
            # 6) DOWNLOAD DO PDF
            # =================================================
            pdf_link = page.get_by_text(
                re.compile(r"GERAR\s+O\s+PDF|MATR[IÍ]CULA\s+ONLINE", re.I)
            ).first

            with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as dl:
                pdf_link.click(force=True)

            filename = f"{numero_pedido}_{matricula}.pdf".replace("/", "_")
            worker_path = os.path.join(RI_DIGITAL_DIR, filename)
            backend_path = _as_backend_path(worker_path)

            dl.value.save_as(worker_path)

            doc_id = _create_document_compat(
                job.get("project_id"),
                filename,
                backend_path,
            )

            # =================================================
            # 7) REGISTRA RESULTADO
            # =================================================
            insert_result(
                job_id=job["id"],
                data={
                    "protocolo": protocolo,
                    "matricula": matricula,
                    "cartorio": cartorio,
                    "data_pedido": data_pedido,
                    "file_path": backend_path,
                    "metadata_json": {
                        "fonte": "RI_DIGITAL",
                        "numero_pedido_vm": numero_pedido,
                        "document_id": doc_id,
                        "data_consulta": data_pedido.isoformat(),
                    },
                },
            )

            encontrados += 1

            # Volta para listagem
            page.go_back()
            page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
            time.sleep(0.5)

        browser.close()

        if encontrados == 0:
            raise Exception("Nenhuma matrícula encontrada no período informado")

        print("🏁 RI Digital finalizado com sucesso")