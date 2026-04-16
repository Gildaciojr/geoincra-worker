import json
import os

import fitz
import psycopg2
import requests
from google.cloud import vision
from openai import OpenAI
from psycopg2.extras import Json, RealDictCursor

from settings import BACKEND_UPLOADS_BASE, DATABASE_URL


vision_client = vision.ImageAnnotatorClient()


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise Exception("OPENAI_API_KEY não configurada no ambiente do worker")

    return OpenAI(api_key=api_key)


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def _resolve_file_path(relative_path: str) -> str:
    primary = os.path.join(BACKEND_UPLOADS_BASE, relative_path)

    if os.path.exists(primary):
        return primary

    fallback = os.path.join("/data", relative_path)

    if os.path.exists(fallback):
        return fallback

    raise Exception(
        f"Arquivo não encontrado. Tentativas: '{primary}' e '{fallback}'"
    )


def _is_pdf(file_path: str) -> bool:
    return file_path.lower().endswith(".pdf")


def _is_image(file_path: str) -> bool:
    return file_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))


def _safe_json_loads(content: str):
    content = content.strip()
    content = content.replace("```json", "")
    content = content.replace("```", "")

    try:
        return json.loads(content)
    except Exception:
        # 🔴 NÃO QUEBRA PIPELINE, MAS PRESERVA DADO
        return {"_raw_output": content}


def get_document(document_id: int):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, file_path, original_filename, stored_filename, content_type
                FROM documents
                WHERE id = %s
                """,
                (document_id,),
            )
            return cur.fetchone()


def get_prompt(prompt_id: int):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, nome, prompt, categoria
                FROM ocr_prompts
                WHERE id = %s
                  AND ativo = TRUE
                """,
                (prompt_id,),
            )
            return cur.fetchone()


def update_result_success(ocr_result_id: int, texto: str, dados_json: dict):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ocr_results
                SET
                    status = 'DONE',
                    provider = 'GOOGLE_VISION_OPENAI',
                    texto_extraido = %s,
                    dados_extraidos_json = %s,
                    erro = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    texto,
                    Json(dados_json),
                    ocr_result_id,
                ),
            )
            conn.commit()


def update_result_error(ocr_result_id: int, error_message: str):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ocr_results
                SET
                    status = 'ERROR',
                    erro = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error_message, ocr_result_id),
            )
            conn.commit()


def merge_job_payload(job_id, patch: dict):
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT payload_json
                FROM automation_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            )
            row = cur.fetchone()

            current = {}
            if row and isinstance(row.get("payload_json"), dict):
                current = row["payload_json"]

            current.update(patch)

            cur.execute(
                """
                UPDATE automation_jobs
                SET payload_json = %s
                WHERE id = %s
                """,
                (Json(current), job_id),
            )
            conn.commit()


def extrair_texto_imagem_google(file_path: str) -> str:
    with open(file_path, "rb") as f:
        content = f.read()

    image = vision.Image(content=content)
    response = vision_client.document_text_detection(image=image)

    if response.error.message:
        raise Exception(f"Google Vision erro: {response.error.message}")

    if response.full_text_annotation and response.full_text_annotation.text:
        return response.full_text_annotation.text

    texts = response.text_annotations
    if texts:
        return texts[0].description

    return ""


def extrair_texto_pdf_nativo(file_path: str) -> str:
    partes = []

    with fitz.open(file_path) as doc:
        for page in doc:
            texto = page.get_text("text")
            if texto:
                partes.append(texto)

    return "\n".join(partes).strip()


def extrair_texto_pdf_ocr_google(file_path: str) -> str:
    partes = []

    with fitz.open(file_path) as doc:
        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(dpi=300, alpha=False)
            png_bytes = pix.tobytes("png")

            image = vision.Image(content=png_bytes)
            response = vision_client.document_text_detection(image=image)

            if response.error.message:
                raise Exception(
                    f"Google Vision erro na página {page_index + 1}: {response.error.message}"
                )

            page_text = ""

            if response.full_text_annotation and response.full_text_annotation.text:
                page_text = response.full_text_annotation.text
            elif response.text_annotations:
                page_text = response.text_annotations[0].description

            if page_text:
                partes.append(page_text)

    return "\n\n".join(partes).strip()


def extrair_texto_documento(file_path: str) -> str:
    if _is_image(file_path):
        return extrair_texto_imagem_google(file_path)

    if _is_pdf(file_path):
        texto_nativo = extrair_texto_pdf_nativo(file_path)
        texto_ocr = extrair_texto_pdf_ocr_google(file_path)

        if len(texto_ocr) > len(texto_nativo):
            return texto_ocr

        return texto_nativo

    raise Exception(
        "Formato não suportado para OCR. Permitidos: PDF, JPG, JPEG, PNG, WEBP."
    )


def interpretar_texto(prompt: str, texto: str):
    openai_client = get_openai_client()

    completion = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    f"{prompt}\n\n"
                    "Retorne JSON válido sempre que possível. "
                    "Não use markdown. Não use bloco ```json. "
                    "Quando houver listas, retorne arrays JSON. "
                    "Quando não encontrar algum campo, use null ou array vazio."
                ),
            },
            {
                "role": "user",
                "content": texto,
            },
        ],
    )

    content = completion.choices[0].message.content or ""
    return _safe_json_loads(content)


def chamar_pipeline_backend(
    document_id: int,
    ocr_result_id: int,
    categoria: str,
    dados: dict,
):
    backend_url = os.getenv("BACKEND_INTERNAL_URL", "http://geoincra_backend:8000")
    url = f"{backend_url}/internal/ocr/pipeline"

    payload = {
        "document_id": document_id,
        "ocr_result_id": ocr_result_id,
        "categoria": categoria,
        "dados": dados,
    }

    response = requests.post(url, json=payload, timeout=120)

    data = {}
    try:
        data = response.json()
    except Exception:
        data = {}

    if response.status_code != 200:
        raise Exception(
            f"Erro ao chamar pipeline backend: {response.status_code} {response.text}"
        )

    if not bool(data.get("success")):
        detalhes = data.get("pipeline_details") or {}
        errors = detalhes.get("errors") or []
        if errors:
            raise Exception("Pipeline técnico falhou: " + " | ".join(errors))
        raise Exception("Pipeline técnico retornou falha sem detalhes.")

    return data


def executar_ocr_job(job: dict):
    payload = job.get("payload_json") or {}

    job_id = job.get("id")
    document_id = payload.get("document_id")
    prompt_id = payload.get("prompt_id")
    ocr_result_id = payload.get("ocr_result_id")

    if not document_id:
        raise Exception("Payload OCR inválido: document_id ausente")

    if not prompt_id:
        raise Exception("Payload OCR inválido: prompt_id ausente")

    if not ocr_result_id:
        raise Exception("Payload OCR inválido: ocr_result_id ausente")

    doc = get_document(document_id)
    if not doc:
        raise Exception("Documento não encontrado")

    prompt = get_prompt(prompt_id)
    if not prompt:
        raise Exception("Prompt não encontrado")

    try:
        relative_path = doc.get("file_path")
        if not relative_path:
            raise Exception("Documento sem file_path")

        file_path = _resolve_file_path(relative_path)

        print(f"📄 OCR Documento: {file_path}")

        texto = extrair_texto_documento(file_path)

        if not texto or not texto.strip():
            raise Exception("Nenhum texto foi extraído do documento")

        print("🧠 Interpretando com OpenAI")

        dados_raw = interpretar_texto(prompt["prompt"], texto)

        # =========================================================
        # 🔥 VALIDAÇÃO LEVE DO PAYLOAD OCR (WORKER)
        # =========================================================
        print("🧩 Validação leve do payload OCR (worker)")

        try:
            if not isinstance(dados_raw, dict):
                raise ValueError("Resposta da IA não é um JSON válido (dict)")

            # 🔒 sanity check mínimo (sem impor schema do backend)
            if not dados_raw:
                print("⚠️ OCR retornou JSON vazio")

            dados = dados_raw

        except Exception as norm_error:
            print("⚠️ Falha na validação leve do OCR no worker")
            print("Erro:", str(norm_error))
            print("📦 Payload recebido da IA:")
            print(json.dumps(dados_raw, ensure_ascii=False, indent=2))

            # 🔒 fallback seguro (não quebra pipeline)
            dados = {
                "_raw_output": dados_raw
            }

        # =========================================================

        update_result_success(ocr_result_id, texto, dados)

        if job_id:
            merge_job_payload(
                job_id,
                {
                    "ocr_stage": "OCR_DONE",
                    "ocr_result_id": ocr_result_id,
                    "document_id": document_id,
                    "prompt_id": prompt_id,
                },
            )

        print("✅ OCR concluído (com normalização)")
        print("⚙️ Chamando pipeline técnico no backend...")

        pipeline_data = chamar_pipeline_backend(
            document_id=document_id,
            ocr_result_id=ocr_result_id,
            categoria=prompt.get("categoria"),
            dados=dados,
        )

        if job_id:
            merge_job_payload(
                job_id,
                {
                    "pipeline_success": bool(pipeline_data.get("success")),
                    "pipeline_details": pipeline_data.get("pipeline_details") or {},
                    "pipeline_warning": None,
                    "ocr_error": None,
                },
            )

        print(
            "✅ Pipeline executado via backend",
            json.dumps(
                pipeline_data.get("pipeline_details", {}),
                ensure_ascii=False,
            ),
        )

    except Exception as e:
        update_result_error(ocr_result_id, str(e))

        if job_id:
            merge_job_payload(
                job_id,
                {
                    "pipeline_success": False,
                    "ocr_error": str(e),
                },
            )

        raise