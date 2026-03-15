import os
import re
from datetime import date, datetime
from typing import Optional

from playwright.sync_api import sync_playwright

from db import create_document, insert_result
from settings import BACKEND_UPLOADS_BASE, RI_DIGITAL_DIR


PLAYWRIGHT_TIMEOUT = 60_000
CLICK_TIMEOUT = 20_000


def _ensure_dir(path: str) -> None:
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


def _save_debug(page, job_id: str, suffix: str) -> None:
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
    match = re.search(r"\b(VM\d{6,})\b", text)
    return match.group(1) if match else None


def _goto_listagem(page, job_id: str) -> None:
    page.goto(
        "https://ridigital.org.br/VisualizarMatricula/DefaultVM.aspx?from=menu",
        wait_until="domcontentloaded",
    )
    page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
    _save_debug(page, job_id, "listagem")
    page.wait_for_timeout(250)


def executar_ri_digital(job: dict, cred: dict) -> None:
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
        page.set_viewport_size({"width": 1440, "height": 900})

        try:
            page.goto(
                "https://ridigital.org.br/Acesso.aspx",
                wait_until="domcontentloaded",
            )

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

            page.wait_for_timeout(3000)
            print("✅ Login RI Digital realizado | URL:", page.url)
            _save_debug(page, job_id, "apos_login")

            _goto_listagem(page, job_id)

            rows = page.locator("table tbody tr")
            total = rows.count()
            if total == 0:
                raise Exception("Tabela de matrículas vazia")

            encontrados = 0

            for i in range(total):
                rows = page.locator("table tbody tr")
                if rows.count() == 0:
                    break

                row = rows.nth(i)
                cells = row.locator("td")
                if cells.count() < 6:
                    continue

                protocolo = None
                matricula = None
                cartorio = None
                data_pedido = None

                try:
                    data_pedido = _parse_br_date(cells.nth(2).inner_text())
                    if not _within_range(data_pedido, data_inicio, data_fim):
                        continue

                    protocolo = cells.nth(1).inner_text().strip()
                    matricula = cells.nth(3).inner_text().strip()
                    cartorio = cells.nth(4).inner_text().strip()

                    abrir_link = cells.nth(0).locator("a").first
                    abrir_link.wait_for(state="attached", timeout=CLICK_TIMEOUT)

                    try:
                        abrir_link.click(timeout=CLICK_TIMEOUT)
                    except Exception:
                        cells.nth(0).click(force=True, timeout=CLICK_TIMEOUT)

                    page.wait_for_url(
                        "**/PedidoFinalizadoVM.aspx**",
                        timeout=PLAYWRIGHT_TIMEOUT,
                    )
                    page.wait_for_timeout(400)
                    _save_debug(page, job_id, f"pedido_{i}")

                    body_text = page.locator("body").inner_text(
                        timeout=CLICK_TIMEOUT
                    )
                    numero_pedido = (
                        _extract_vm_number_from_body(body_text)
                        or protocolo
                        or f"pedido_{i}"
                    )

                    filename = (
                        f"{numero_pedido}_{(matricula or 'matricula')}.pdf"
                        .replace("/", "_")
                        .replace("\\", "_")
                    )
                    worker_path = os.path.join(RI_DIGITAL_DIR, filename)
                    backend_path = _as_backend_path(worker_path)

                    pdf_ok = False
                    pdf_motivo = None
                    final_file_path = None
                    doc_id = None

                    try:
                        page.locator("#btnPDF").wait_for(
                            state="visible",
                            timeout=CLICK_TIMEOUT,
                        )
                        page.locator("#btnPDF").click(
                            force=True,
                            timeout=CLICK_TIMEOUT,
                        )

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
                            pdf_motivo = (
                                "PDF não disponível ou prazo expirado no RI Digital"
                            )
                    except Exception:
                        pdf_motivo = "Erro ao acionar botão de geração do PDF"

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
                                "data_consulta": (
                                    data_pedido.isoformat() if data_pedido else None
                                ),
                            },
                        },
                    )
                    encontrados += 1

                except Exception as e:
                    insert_result(
                        job_id=job["id"],
                        data={
                            "protocolo": protocolo,
                            "matricula": matricula,
                            "cartorio": cartorio,
                            "data_pedido": data_pedido,
                            "file_path": None,
                            "metadata_json": {
                                "fonte": "RI_DIGITAL",
                                "erro_linha": str(e),
                                "linha_index": i,
                            },
                        },
                    )

                _goto_listagem(page, job_id)

            if encontrados == 0:
                raise Exception("Nenhuma matrícula encontrada no período informado")

            print("🏁 RI Digital finalizado com sucesso")

        finally:
            try:
                browser.close()
            except Exception:
                pass