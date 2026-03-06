import os
import json
import openai
import psycopg2

from psycopg2.extras import RealDictCursor
from google.cloud import vision

from app.settings import DATABASE_URL, BACKEND_UPLOADS_BASE


# =========================================================
# GOOGLE VISION
# =========================================================

vision_client = vision.ImageAnnotatorClient()


# =========================================================
# OPENAI
# =========================================================

openai.api_key = os.getenv("OPENAI_API_KEY")


# =========================================================
# BANCO
# =========================================================

def get_connection():
    return psycopg2.connect(DATABASE_URL)


# =========================================================
# BUSCAR DOCUMENTO
# =========================================================

def get_document(document_id):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT id, file_path
                FROM documents
                WHERE id = %s
            """, (document_id,))

            return cur.fetchone()


# =========================================================
# BUSCAR PROMPT
# =========================================================

def get_prompt(prompt_id):

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT prompt
                FROM ocr_prompts
                WHERE id = %s
            """, (prompt_id,))

            return cur.fetchone()


# =========================================================
# SALVAR RESULTADO
# =========================================================

def save_result(document_id, texto, dados_json):

    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO ocr_results (
                    document_id,
                    status,
                    provider,
                    texto_extraido,
                    dados_extraidos_json,
                    created_at,
                    updated_at
                )
                VALUES (%s,'DONE','GOOGLE_VISION_OPENAI',%s,%s,NOW(),NOW())
            """, (
                document_id,
                texto,
                json.dumps(dados_json)
            ))

            conn.commit()


# =========================================================
# OCR GOOGLE VISION
# =========================================================

def extrair_texto_google(file_path):

    with open(file_path, "rb") as image_file:
        content = image_file.read()

    image = vision.Image(content=content)

    response = vision_client.text_detection(image=image)

    texts = response.text_annotations

    if not texts:
        return ""

    return texts[0].description


# =========================================================
# OPENAI INTERPRETAÇÃO
# =========================================================

def interpretar_texto(prompt, texto):

    completion = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": prompt
            },
            {
                "role": "user",
                "content": texto
            }
        ],
        temperature=0
    )

    content = completion.choices[0].message["content"]

    try:
        return json.loads(content)
    except Exception:
        return {"resultado": content}


# =========================================================
# EXECUÇÃO DO JOB
# =========================================================

def executar_ocr_job(job):

    payload = job["payload_json"]

    document_id = payload["document_id"]
    prompt_id = payload["prompt_id"]

    doc = get_document(document_id)

    if not doc:
        raise Exception("Documento não encontrado")

    prompt = get_prompt(prompt_id)

    if not prompt:
        raise Exception("Prompt não encontrado")

    # -----------------------------------------------------
    # RESOLVER CAMINHO ABSOLUTO DO ARQUIVO
    # -----------------------------------------------------

    relative_path = doc["file_path"]

    file_path = os.path.join(BACKEND_UPLOADS_BASE, relative_path)

    if not os.path.exists(file_path):
        raise Exception(f"Arquivo não encontrado no container: {file_path}")

    print("📄 OCR Documento:", file_path)

    # -----------------------------------------------------
    # OCR GOOGLE VISION
    # -----------------------------------------------------

    texto = extrair_texto_google(file_path)

    print("🧠 Interpretando com OpenAI")

    # -----------------------------------------------------
    # INTERPRETAÇÃO COM OPENAI
    # -----------------------------------------------------

    dados = interpretar_texto(prompt["prompt"], texto)

    # -----------------------------------------------------
    # SALVAR RESULTADO
    # -----------------------------------------------------

    save_result(document_id, texto, dados)

    print("✅ OCR concluído")