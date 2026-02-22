# geoincra_worker/app/ri_digital.py
import os
import re
import time
from datetime import datetime, date
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from app.settings import RI_DIGITAL_DIR, BACKEND_UPLOADS_BASE
from app.db import insert_result, create_document

PLAYWRIGHT_TIMEOUT = 60_000  # 60s


# =========================================================
# Helpers
# =========================================================
def _ensure_dir(path: str) -> None:
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
    return datetime.strptime((text or "").strip(), "%d/%m/%Y").date()


def _within_range(d: date, start_dt: datetime, end_dt: datetime) -> bool:
    # compara por DATE (não hora)
    return start_dt.date() <= d <= end_dt.date()


def _create_document_compat(project_id: Optional[int], filename: str, backend_path: str) -> Optional[int]:
    """
    Compatibilidade com assinaturas diferentes do create_document.

    Curta:
      create_document(project_id, original_filename, file_path)

    Longa:
      create_document(project_id=..., doc_type=..., stored_filename=..., original_filename=..., content_type=..., description=..., file_path=...)
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
            description="PDF RI Digital - Visualização de Matrícula",
            file_path=backend_path,
        )
    except Exception:
        return None


def _debug_screenshot(page, out_path: str) -> None:
    try:
        page.screenshot(path=out_path, full_page=True)
    except Exception:
        pass


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
        raise Exception("Credenciais RI Digital inválidas (login/senha ausentes)")

    job_id = str(job.get("id"))
    print(f"▶️ RI Digital | Job {job_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        try:
            # =====================================================
            # 1) LOGIN — FORMA REAL CONFIRMADA
            # =====================================================
            page.goto("https://ridigital.org.br/Acesso.aspx", wait_until="domcontentloaded")

            # Clique real no link interno do card "Acesso comum"
            # (seu print: <a class="access-details acesso-comum-link"...>)
            acesso_link = page.locator("a.access-details.acesso-comum-link").first
            acesso_link.wait_for(state="visible", timeout=20_000)
            acesso_link.click(force=True, timeout=20_000)

            # Inputs reais (placeholders)
            email_input = page.locator('input[placeholder="E-mail"]')
            senha_input = page.locator('input[placeholder="Senha"]')

            email_input.wait_for(state="visible", timeout=20_000)
            senha_input.wait_for(state="visible", timeout=20_000)

            email_input.fill(login)
            senha_input.fill(senha)

            # Botão Entrar (robusto)
            page.get_by_role("button", name=re.compile(r"entrar", re.I)).click()

            page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)
            print("✅ Login RI Digital realizado")

            # =====================================================
            # 2) ABRIR VISUALIZAÇÃO DE MATRÍCULA (SEM DEPENDER DO CARD VISÍVEL)
            # =====================================================
            # Isso evita erro "element is not visible" quando o site troca layout / menu mobile.
            page.goto(
                "https://ridigital.org.br/VisualizarMatricula/DefaultVM.aspx?from=menu",
                wait_until="domcontentloaded",
            )

            # Normalmente redireciona para /VisualizarMatricula/ListagemPedidosVM.aspx...
            page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)

            # Tabela da listagem
            page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)

            # =====================================================
            # 3) VARREDURA DA TABELA
            # =====================================================
            rows = page.locator("table tbody tr")
            total = rows.count()
            if total == 0:
                raise Exception("Tabela de matrículas vazia no RI Digital")

            encontrados = 0

            for i in range(total):
                row = rows.nth(i)
                cells = row.locator("td")
                if cells.count() < 6:
                    continue

                # colunas (conforme seu anexo):
                # 0 Abrir Mat | 1 Protocolo | 2 Data | 3 Matrícula/CNM | 4 Cartório | 5 Valor Total | ...
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
                # 4) ABRIR PEDIDO (ÍCONE "Abrir Mat." = img pasta.gif)
                # =================================================
                abrir_img = cells.nth(0).locator("img[src*='pasta.gif']").first
                abrir_img.wait_for(state="visible", timeout=20_000)
                abrir_img.click(force=True)

                page.wait_for_url("**/PedidoFinalizadoVM.aspx**", timeout=PLAYWRIGHT_TIMEOUT)
                page.wait_for_timeout(400)

                # =================================================
                # 5) EXTRAI NÚMERO DO PEDIDO VMxxxxxx
                # =================================================
                body_text = page.locator("body").inner_text(timeout=20_000)
                m = re.search(r"\b(VM\d{6,})\b", body_text)
                numero_pedido_vm = m.group(1) if m else None

                # =================================================
                # 6) DOWNLOAD DO PDF (seu print: input type=image id="btnnPDF")
                # =================================================
                # Seletores:
                # - id="btnnPDF" (principal)
                # - name="btnnPDF"
                # - title contendo "download em PDF"
                btn_pdf = page.locator(
                    "input#btnnPDF, input[name='btnnPDF'], input[type='image'][title*='PDF' i]"
                ).first

                btn_pdf.wait_for(state="visible", timeout=20_000)

                filename = f"{(numero_pedido_vm or protocolo)}_{matricula}.pdf"
                filename = filename.replace("/", "_").replace("\\", "_")
                worker_path = os.path.join(RI_DIGITAL_DIR, filename)
                backend_path = _as_backend_path(worker_path)

                with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as dl:
                    btn_pdf.click(force=True)

                download = dl.value
                download.save_as(worker_path)

                # cria document (se suportado)
                doc_id = _create_document_compat(job.get("project_id"), filename, backend_path)

                # =================================================
                # 7) REGISTRA RESULTADO
                # =================================================
                insert_result(
                    job_id=job["id"],
                    data={
                        "protocolo": protocolo,
                        "matricula": matricula,
                        "cnm": None,
                        "cartorio": cartorio,
                        "data_pedido": data_pedido,
                        "file_path": backend_path,
                        "metadata_json": {
                            "fonte": "RI_DIGITAL",
                            "numero_pedido_vm": numero_pedido_vm,
                            "document_id": doc_id,
                            "data_consulta": data_pedido.isoformat(),
                            "range": {
                                "data_inicio": data_inicio.date().isoformat(),
                                "data_fim": data_fim.date().isoformat(),
                            },
                        },
                    },
                )

                encontrados += 1

                # voltar para listagem
                page.go_back()
                page.wait_for_url("**/VisualizarMatricula/**", timeout=PLAYWRIGHT_TIMEOUT)
                page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
                time.sleep(0.3)

            if encontrados == 0:
                raise Exception("Nenhuma matrícula encontrada no período informado")

            print(f"🏁 RI Digital finalizado com sucesso | encontrados={encontrados}")

        except Exception as e:
            # screenshot de debug ajuda MUITO em headless
            debug_path = os.path.join(RI_DIGITAL_DIR, f"debug_{job_id}.png")
            _debug_screenshot(page, debug_path)
            raise

        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass