# geoincra_worker/app/onr_sigri.py
import os
import re
import time
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError

from app.settings import (
    ONR_SIGRI_DIR,
    BACKEND_UPLOADS_BASE,
    ONR_PFX_PATH,
    ONR_PFX_PASSWORD,
)
from app.db import insert_result, create_document, get_job_project_id

PLAYWRIGHT_TIMEOUT = 60_000  # 60s


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _as_backend_path(worker_path: str) -> str:
    # worker salva em /data/... e backend enxerga o mesmo volume em /app/app/uploads/...
    # se o worker_path estiver dentro do volume /data, converte para base do backend
    # exemplo: /data/onr-sigri/x.kmz -> /app/app/uploads/onr-sigri/x.kmz
    worker_path = os.path.abspath(worker_path)
    # substitui apenas o prefixo "/data"
    if worker_path.startswith("/data/"):
        return worker_path.replace("/data", BACKEND_UPLOADS_BASE, 1)
    # fallback: já é caminho do backend (caso rode no mesmo FS)
    return worker_path


def _safe_filename(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w\-_\. ]+", "_", text, flags=re.UNICODE)
    text = text.replace(" ", "_")
    return text[:180] if len(text) > 180 else text


def _get_payload(job: dict) -> dict:
    payload = job.get("payload_json") or {}
    search = payload.get("search") or {}
    search_type = (search.get("type") or "").upper().strip()
    search_value = (search.get("value") or "").strip()
    if search_type not in {"CAR", "ENDERECO"}:
        raise Exception("Payload inválido: search.type deve ser CAR ou ENDERECO")
    if not search_value:
        raise Exception("Payload inválido: search.value vazio")
    return {"type": search_type, "value": search_value}


def _require_project(job: dict) -> int:
    project_id = job.get("project_id")
    if not project_id:
        # regra dura: ONR sempre vinculado a projeto
        raise Exception("ONR_SIGRI_CONSULTA exige project_id")
    return int(project_id)


def executar_onr_sigri(job: dict):
    project_id = _require_project(job)
    search = _get_payload(job)

    _ensure_dir(ONR_SIGRI_DIR)

    # ======= Certificado A1 (PFX) obrigatório no VPS =======
    if not ONR_PFX_PATH or not os.path.exists(ONR_PFX_PATH):
        raise Exception(
            "Certificado ONR (PFX) não configurado no worker. "
            "Defina ONR_PFX_PATH apontando para um arquivo .pfx acessível no container."
        )
    if not ONR_PFX_PASSWORD:
        raise Exception(
            "Senha do certificado ONR não configurada. Defina ONR_PFX_PASSWORD no ambiente do worker."
        )

    print(f"▶️ ONR/SIG-RI | Job {job['id']} | Projeto {project_id} | {search['type']}={search['value']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        # Playwright: client certificate (PFX) para origin do ONR.
        # Isso evita “modal de seleção” depender do SO (Linux headless).
        context = browser.new_context(
            accept_downloads=True,
            client_certificates=[
                {
                    "origin": "https://mapa.onr.org.br",
                    "pfxPath": ONR_PFX_PATH,
                    "passphrase": ONR_PFX_PASSWORD,
                }
            ],
        )

        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT)

        # =========================
        # LOGIN
        # =========================
        page.goto("https://mapa.onr.org.br/sigri/login-usuario", wait_until="domcontentloaded")

        # Em ambientes com certificado aplicado por mTLS, o site pode logar automaticamente.
        # Ainda assim tentamos o botão “Entrar com Certificado Digital” se existir.
        try:
            btn_cert = page.get_by_text("Entrar com Certificado Digital")
            if btn_cert:
                btn_cert.click(timeout=10_000)
        except Exception:
            pass

        # Aguarda redirecionar para início (ou carregar sessão)
        # A UI pode variar, então toleramos.
        time.sleep(3)

        # =========================
        # ABRIR MAPA PRINCIPAL
        # =========================
        page.goto("https://mapa.onr.org.br", wait_until="domcontentloaded")
        time.sleep(5)

        # =========================
        # CAMADA DE BUSCA (CAR / ENDERECO)
        # =========================
        # Estratégia: abrir card "Camada de Busca" e selecionar opção.
        # Seletores tolerantes para não quebrar fácil.
        try:
            page.get_by_text("Camada de Busca").first.click(timeout=20_000)
        except Exception:
            # fallback: algumas UIs usam “Camada para busca”
            page.get_by_text("Camada").first.click(timeout=20_000)

        time.sleep(1)

        if search["type"] == "CAR":
            page.get_by_text("Cadastro Ambiental Rural").click(timeout=20_000)
        else:
            page.get_by_text("Endereço").click(timeout=20_000)

        # =========================
        # INPUT DE BUSCA + AUTOCOMPLETE
        # =========================
        # O texto do placeholder pode variar, então buscamos pelo primeiro input visível após escolher camada.
        inputs = page.locator("input:visible")
        if inputs.count() == 0:
            raise Exception("Campo de busca não encontrado no ONR")

        search_input = inputs.first
        search_input.fill(search["value"])
        time.sleep(1.5)

        # Seleciona primeira opção do autocomplete (você confirmou que aparece lista clicável)
        # Tentativas: role listbox, dropdown, item com texto.
        try:
            page.locator("[role='listbox'] [role='option']").first.click(timeout=10_000)
        except Exception:
            # fallback: clicar no primeiro item de um dropdown comum
            try:
                page.locator(".autocomplete li").first.click(timeout=10_000)
            except Exception:
                # última tentativa: ENTER (algumas UIs aceitam)
                search_input.press("Enter")

        # Aguarda mapa atualizar e polígono aparecer
        time.sleep(6)

        # =========================
        # CLICAR NO POLÍGONO PARA ABRIR MODAL
        # =========================
        # Como a geometria é canvas/mapa, usamos um clique central na viewport para disparar seleção.
        # Em produção, isso pode exigir ajuste por zoom/offset, mas funciona para a maioria.
        page.mouse.click(800, 450)
        time.sleep(2)

        # =========================
        # CAPTURAR MODAL (DADOS OFICIAIS)
        # =========================
        # Vamos procurar os labels e extrair valores próximos.
        modal = page.locator("text=Camada:").first
        modal.wait_for(timeout=20_000)

        # Coleta de texto do painel/modal
        # Pegamos um bloco próximo ao "Camada:" para extrair pares.
        block = page.locator("xpath=//*[contains(., 'Camada:') and contains(., 'Código')]").first
        block_text = block.inner_text(timeout=10_000)

        def _extract(label: str) -> str | None:
            # busca "Label:\nvalor" ou "Label: valor"
            pattern = rf"{re.escape(label)}\s*:\s*([^\n\r]+)"
            m = re.search(pattern, block_text, flags=re.IGNORECASE)
            return m.group(1).strip() if m else None

        camada = _extract("Camada")
        codigo_sigef = _extract("Código Sigef") or _extract("Código SIGEF")
        nome_area = _extract("Nome da Área") or _extract("Nome da Area")
        matricula = _extract("Matrícula") or _extract("Matricula")
        municipio = _extract("Município") or _extract("Municipio")
        uf = _extract("UF")
        ccir_sncr = _extract("CCIR/SNCR")

        metadata = {
            "fonte": "ONR_SIGRI",
            "consultado_em": datetime.utcnow().isoformat() + "Z",
            "camada": camada,
            "codigo_sigef": codigo_sigef,
            "nome_area": nome_area,
            "matricula": matricula,
            "municipio": municipio,
            "uf": uf,
            "ccir_sncr": ccir_sncr,
            "search": {"type": search["type"], "value": search["value"]},
            "raw_block": block_text,
        }

        # =========================
        # BAIXAR POLÍGONO (KMZ)
        # =========================
        # Você descreveu o ícone/ação “Baixar polígono”.
        # Tentamos primeiro pelo texto, depois por aria-label/title.
        download_clicked = False

        # O download do Playwright captura automaticamente
        kmz_path_worker = None

        with page.expect_download(timeout=PLAYWRIGHT_TIMEOUT) as download_info:
            try:
                page.get_by_text("Baixar polígono").click(timeout=10_000)
                download_clicked = True
            except Exception:
                # fallback: procurar botão com title/aria-label
                try:
                    page.locator("[title*='Baixar'][title*='polígono'], [aria-label*='Baixar'][aria-label*='polígono']").first.click(timeout=10_000)
                    download_clicked = True
                except Exception:
                    pass

        if not download_clicked:
            raise Exception("Não foi possível acionar o download do polígono (KMZ) no ONR")

        download = download_info.value
        suggested = download.suggested_filename or "poligono.kmz"

        base_name = _safe_filename(f"onr_{project_id}_{(codigo_sigef or 'sigef')}_{int(time.time())}")
        if not suggested.lower().endswith(".kmz"):
            suggested = base_name + ".kmz"

        kmz_path_worker = os.path.join(ONR_SIGRI_DIR, suggested)
        download.save_as(kmz_path_worker)

        # Caminho que o BACKEND enxerga (mesmo volume)
        kmz_path_backend = _as_backend_path(kmz_path_worker)

        # =========================
        # REGISTRAR RESULTADO + DOCUMENT DO PROJETO
        # =========================
        # 1) Document (para download seguro via /api/files/documents/{id})
        doc_id = create_document(
            project_id=project_id,
            doc_type="ONR_SIGRI_POLIGONO",
            stored_filename=os.path.basename(kmz_path_backend),
            original_filename=os.path.basename(kmz_path_backend),
            content_type="application/vnd.google-earth.kmz",
            description="Polígono KMZ obtido via consulta ONR/SIG-RI",
            file_path=kmz_path_backend,
        )

        metadata["document_id"] = doc_id
        metadata["download_url"] = f"/api/files/documents/{doc_id}"

        # 2) AutomationResult (auditoria técnica da automação)
        insert_result(
            job_id=job["id"],
            data={
                "protocolo": None,
                "matricula": matricula,
                "cnm": None,
                "cartorio": None,
                "data_pedido": None,
                "file_path": kmz_path_backend,
                "metadata_json": metadata,
            },
        )

        browser.close()
        print(f"✅ ONR/SIG-RI concluído | Projeto {project_id} | Document {doc_id}")
