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
    abs_path = os.path.abspath(worker_path)
    if abs_path.startswith("/data/"):
        return abs_path.replace("/data", BACKEND_UPLOADS_BASE, 1)
    return abs_path


def _parse_br_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%d/%m/%Y").date()


def _within_range(d: date, start_dt: datetime, end_dt: datetime) -> bool:
    return start_dt.date() <= d <= end_dt.date()


def _create_document_compat(project_id, filename, backend_path):
    if not project_id:
        return None
    try:
        return create_document(project_id, filename, backend_path)
    except TypeError:
        try:
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


def executar_ri_digital(job: dict, cred: dict):
    _ensure_dir(RI_DIGITAL_DIR)

    payload = job.get("payload_json") or {}
    data_inicio = datetime.fromisoformat(payload["data_inicio"])
    data_fim = datetime.fromisoformat(payload["data_fim"])

    login = cred.get("login")
    senha = cred.get("password_encrypted")

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
        # LOGIN – RI DIGITAL (REAL)
        # =========================
        page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

        acesso = page.get_by_text("Acesso comum").first
        acesso.wait_for(state="visible", timeout=20_000)
        acesso.click(force=True, timeout=20_000)

        email_input = page.locator('input[placeholder="E-mail"]')
        senha_input = page.locator('input[placeholder="Senha"]')

        email_input.wait_for(state="visible", timeout=20_000)
        senha_input.wait_for(state="visible", timeout=20_000)

        email_input.fill(login)
        senha_input.fill(senha)

        page.get_by_role("button", name=re.compile("entrar", re.I)).click()
        page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)

        # =========================
        # VISUALIZAÇÃO DE MATRÍCULA
        # =========================
        page.get_by_text("Visualização de matrícula").first.click()
        page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)
        page.wait_for_selector("table")

        rows = page.locator("table tbody tr")
        encontrados = 0

        for i in range(rows.count()):
            cells = rows.nth(i).locator("td")
            if cells.count() < 5:
                continue

            data_pedido = _parse_br_date(cells.nth(2).inner_text())
            if not _within_range(data_pedido, data_inicio, data_fim):
                continue

            protocolo = cells.nth(1).inner_text().strip()
            matricula = cells.nth(3).inner_text().strip()
            cartorio = cells.nth(4).inner_text().strip()

            # Abrir matrícula
            cells.nth(0).click(force=True)
            page.wait_for_url("**/PedidoFinalizadoVM.aspx**", timeout=PLAYWRIGHT_TIMEOUT)

            body = page.locator("body").inner_text()
            m = re.search(r"\b(VM\d+)\b", body)
            numero_pedido = m.group(1) if m else protocolo

            pdf_link = page.get_by_text(re.compile("GERAR O PDF", re.I)).first
            with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as dl:
                pdf_link.click(force=True)

            filename = f"{numero_pedido}_{matricula}.pdf".replace("/", "_")
            worker_path = os.path.join(RI_DIGITAL_DIR, filename)
            backend_path = _as_backend_path(worker_path)

            dl.value.save_as(worker_path)
            doc_id = _create_document_compat(job.get("project_id"), filename, backend_path)

            insert_result(
                job_id=job["id"],
                data={
                    "protocolo": protocolo,
                    "matricula": matricula,
                    "cartorio": cartorio,
                    "data_pedido": data_pedido,
                    "file_path": backend_path,
                    "metadata_json": {
                        "numero_pedido_vm": numero_pedido,
                        "document_id": doc_id,
                        "fonte": "RI_DIGITAL",
                    },
                },
            )

            encontrados += 1
            page.go_back()
            page.wait_for_selector("table")
            time.sleep(0.5)

        browser.close()

        if encontrados == 0:
            raise Exception("Nenhuma matrícula encontrada no período")