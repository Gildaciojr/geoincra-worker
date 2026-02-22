from playwright.sync_api import sync_playwright, TimeoutError
from datetime import datetime
import os
import time

from app.settings import RI_DIGITAL_DIR, BACKEND_UPLOADS_BASE
from app.db import insert_result, create_document

PLAYWRIGHT_TIMEOUT = 60_000  # 60s


def executar_ri_digital(job, cred):
    # =========================================================
    # PREPARAÇÃO
    # =========================================================
    os.makedirs(RI_DIGITAL_DIR, exist_ok=True)

    data_inicio = datetime.fromisoformat(job["payload_json"]["data_inicio"])
    data_fim = datetime.fromisoformat(job["payload_json"]["data_fim"])

    print(f"▶️ RI Digital | Job {job['id']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        # =========================================================
        # LOGIN (ROBUSTO / PRODUÇÃO)
        # =========================================================
        print("🔐 Acessando RI Digital...")
        page.goto(
            "https://ridigital.org.br/Acesso.aspx",
            wait_until="domcontentloaded",
        )

        # ⚠️ Existem DOIS textos "Acesso comum" no DOM
        acesso_btn = page.get_by_text("Acesso comum").nth(0)
        acesso_btn.wait_for(state="visible", timeout=PLAYWRIGHT_TIMEOUT)
        acesso_btn.click()

        page.wait_for_selector("input[type=email]", timeout=PLAYWRIGHT_TIMEOUT)

        page.fill("input[type=email]", cred["login"])
        page.fill("input[type=password]", cred["password_encrypted"])
        page.click("button[type=submit]")

        # Aguarda navegação real pós-login
        page.wait_for_url("**/ServicosOnline.aspx", timeout=PLAYWRIGHT_TIMEOUT)
        print("✅ Login realizado com sucesso")

        # =========================================================
        # ACESSO À VISUALIZAÇÃO DE MATRÍCULAS
        # =========================================================
        page.get_by_text("Visualização de matrícula").first.click()
        page.wait_for_selector("table", timeout=PLAYWRIGHT_TIMEOUT)
        print("📋 Tabela de matrículas carregada")

        rows = page.query_selector_all("table tbody tr")
        encontrou = False

        # =========================================================
        # PROCESSAMENTO DAS MATRÍCULAS
        # =========================================================
        for row in rows:
            try:
                cells = row.query_selector_all("td")
                if len(cells) < 5:
                    continue

                data_text = cells[1].inner_text().strip()
                data_pedido = datetime.strptime(data_text, "%d/%m/%Y")

                if not (data_inicio <= data_pedido <= data_fim):
                    continue

                protocolo = cells[0].inner_text().strip()
                cartorio = cells[2].inner_text().strip()
                matricula = cells[3].inner_text().strip()

                print(f"📄 Processando matrícula {matricula}")

                abrir_link = cells[0].query_selector("a")
                if not abrir_link:
                    continue

                abrir_link.click()

                # Aguarda tela da matrícula
                page.wait_for_selector("a", timeout=PLAYWRIGHT_TIMEOUT)

                # =========================
                # DOWNLOAD DO PDF
                # =========================
                with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as d:
                    page.get_by_text("PDF").first.click()

                download = d.value

                filename = f"{protocolo}_{matricula}.pdf".replace("/", "_")
                worker_path = os.path.join(RI_DIGITAL_DIR, filename)
                download.save_as(worker_path)

                # Caminho visível para o backend
                backend_path = worker_path.replace("/data", BACKEND_UPLOADS_BASE, 1)

                # =========================
                # DOCUMENT + RESULT
                # =========================
                doc_id = create_document(
                    project_id=job["project_id"],
                    doc_type="RI_DIGITAL_MATRICULA",
                    stored_filename=filename,
                    original_filename=filename,
                    content_type="application/pdf",
                    description="Matrícula obtida via RI Digital",
                    file_path=backend_path,
                )

                insert_result(
                    job["id"],
                    {
                        "protocolo": protocolo,
                        "matricula": matricula,
                        "cnm": None,
                        "cartorio": cartorio,
                        "data_pedido": data_pedido.date(),
                        "file_path": backend_path,
                        "metadata_json": {
                            "document_id": doc_id,
                            "fonte": "RI_DIGITAL",
                        },
                    },
                )

                encontrou = True
                print(f"✅ Matrícula {matricula} salva")

                page.go_back()
                time.sleep(1)

            except TimeoutError:
                print("⚠️ Timeout ao processar matrícula, continuando...")
                page.go_back()
                continue

        browser.close()

        # =========================================================
        # FINALIZAÇÃO
        # =========================================================
        if not encontrou:
            raise Exception("Nenhuma matrícula encontrada no período informado")

        print("🏁 Automação RI Digital finalizada com sucesso")