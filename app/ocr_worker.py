import os
import json
from pathlib import Path

import psycopg2
import fitz

from psycopg2.extras import RealDictCursor, Json
from google.cloud import vision
from openai import OpenAI

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.settings import DATABASE_URL, BACKEND_UPLOADS_BASE

# pipeline importado do backend montado em /backend_app
from backend.app.services.ocr_pipeline_service import OcrPipelineService


vision_client = vision.ImageAnnotatorClient()

# =========================================================
# SQLAlchemy engine (global)
# =========================================================

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


# =========================================================
# OPENAI CLIENT
# =========================================================

def get_openai_client() -> OpenAI:

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise Exception("OPENAI_API_KEY não configurada no ambiente do worker")

    return OpenAI(api_key=api_key)


# =========================================================
# DB CONNECTION (psycopg2)
# =========================================================

def get_connection():
    return psycopg2.connect(DATABASE_URL)


# =========================================================
# PATH RESOLUTION
# =========================================================

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


# =========================================================
# FILE TYPE HELPERS
# =========================================================

def _is_pdf(file_path: str) -> bool:
    return file_path.lower().endswith(".pdf")


def _is_image(file_path: str) -> bool:
    return file_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))


# =========================================================
# JSON SAFE LOAD
# =========================================================

def _safe_json_loads(content: str):

    try:
        return json.loads(content)

    except Exception:
        return {"resultado": content}


# =========================================================
# DB QUERIES
# =========================================================

def get_document(document_id: int):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute(
                """
                SELECT id, file_path, original_filename, stored_filename, content_type
                FROM documents
                WHERE id = %s
                """,
                (document_id,)
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
                (prompt_id,)
            )

            return cur.fetchone()


# =========================================================
# UPDATE RESULT SUCCESS
# =========================================================

def update_result_success(document_id: int, texto: str, dados_json: dict):

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
                WHERE id = (
                    SELECT id
                    FROM ocr_results
                    WHERE document_id = %s
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (
                    texto,
                    Json(dados_json),
                    document_id,
                ),
            )

            conn.commit()


# =========================================================
# UPDATE RESULT ERROR
# =========================================================

def update_result_error(document_id: int, error_message: str):

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                UPDATE ocr_results
                SET
                    status = 'ERROR',
                    erro = %s,
                    updated_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM ocr_results
                    WHERE document_id = %s
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (error_message, document_id)
            )

            conn.commit()


# =========================================================
# GOOGLE VISION OCR
# =========================================================

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


# =========================================================
# PDF TEXT EXTRACTION
# =========================================================

def extrair_texto_pdf_nativo(file_path: str) -> str:

    partes: list[str] = []

    with fitz.open(file_path) as doc:

        for page in doc:

            texto = page.get_text("text")

            if texto:
                partes.append(texto)

    return "\n".join(partes).strip()


def extrair_texto_pdf_ocr_google(file_path: str) -> str:

    partes: list[str] = []

    with fitz.open(file_path) as doc:

        for page_index, page in enumerate(doc):

            pix = page.get_pixmap(dpi=220, alpha=False)

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


# =========================================================
# DOCUMENT TEXT EXTRACTION
# =========================================================

def extrair_texto_documento(file_path: str) -> str:

    if _is_image(file_path):
        return extrair_texto_imagem_google(file_path)

    if _is_pdf(file_path):

        texto_nativo = extrair_texto_pdf_nativo(file_path)

        if len(texto_nativo.strip()) >= 80:
            return texto_nativo

        return extrair_texto_pdf_ocr_google(file_path)

    raise Exception(
        "Formato não suportado para OCR. Permitidos: PDF, JPG, JPEG, PNG, WEBP."
    )


# =========================================================
# OPENAI INTERPRETATION
# =========================================================

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


# =========================================================
# OCR JOB EXECUTION
# =========================================================

def executar_ocr_job(job: dict):

    payload = job.get("payload_json") or {}

    document_id = payload.get("document_id")
    prompt_id = payload.get("prompt_id")

    if not document_id:
        raise Exception("Payload OCR inválido: document_id ausente")

    if not prompt_id:
        raise Exception("Payload OCR inválido: prompt_id ausente")

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

        dados = interpretar_texto(prompt["prompt"], texto)

        update_result_success(document_id, texto, dados)

        print("✅ OCR concluído")

        # =====================================================
        # PIPELINE TÉCNICO
        # =====================================================

        print("⚙️ Executando pipeline técnico...")

        with SessionLocal() as db:

            OcrPipelineService.executar_pipeline(
                db=db,
                document_id=document_id,
                prompt_categoria=prompt.get("categoria"),
                dados_extraidos=dados,
            )

        print("✅ Pipeline técnico executado")

    except Exception as e:

        update_result_error(document_id, str(e))

        raise