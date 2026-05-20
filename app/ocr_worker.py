import json
import os

import fitz
import psycopg2
import requests
from google.cloud import vision
from openai import OpenAI
from datetime import datetime
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
                SELECT
                    id,
                    nome,
                    slug,
                    categoria,
                    pipeline,
                    engine,
                    prompt,
                    versao,
                    prioridade,
                    temperatura,
                    modelo_llm,
                    idioma,
                    parser_service,
                    normalizer_service,
                    post_processor_service,
                    output_schema,
                    pipeline_executor,
                    usar_google_vision,
                    usar_openai,
                    usar_pipeline_hibrido,
                    exige_geometria,
                    exige_memorial,
                    exige_confrontantes,
                    exige_historico_registral,
                    exige_proprietarios,
                    exige_documentos_pessoais,
                    gera_documento_tecnico,
                    gera_geojson,
                    gera_croqui,
                    gera_memorial,
                    gera_pdf,
                    gera_docx,
                    gera_dxf,
                    gera_shp,
                    gera_sigef,
                    gera_txt,
                    gera_csv,
                    persistir_banco,
                    habilitar_validacao_semantica,
                    habilitar_pos_processamento,
                    habilitar_pipeline_registral,
                    habilitar_pipeline_geometrico,
                    habilitar_pipeline_confrontantes,
                    configuracao_json,
                    schema_json,
                    output_mapping_json,
                    metadados_json,
                    timeout_execucao_segundos,
                    max_tokens_llm
                FROM ocr_prompts
                WHERE id = %s
                  AND ativo = TRUE
                """,
                (prompt_id,),
            )
            return cur.fetchone()


def update_result_success(
    ocr_result_id: int,
    texto: str,
    dados_json: dict,
    prompt: dict,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            qualidade = (
                dados_json.get("qualidade")
                if isinstance(dados_json, dict)
                else None
            )

            score_confianca = None

            if isinstance(qualidade, dict):

                try:
                    score_confianca = int(
                        qualidade.get("score", 0) or 0
                    )

                except Exception:
                    score_confianca = 0

            possui_geojson = bool(
                (
                    isinstance(dados_json, dict)
                    and (
                        dados_json.get("geojson")
                        or (
                            isinstance(
                                dados_json.get("geometria"),
                                dict,
                            )
                            and dados_json["geometria"].get(
                                "geojson"
                            )
                        )
                    )
                )
            )

            possui_memorial = bool(
                (
                    isinstance(dados_json, dict)
                    and (
                        dados_json.get("memorial")
                        or dados_json.get("memorial_texto")
                        or (
                            isinstance(
                                dados_json.get("geometria"),
                                dict,
                            )
                            and dados_json["geometria"].get(
                                "memorial_texto"
                            )
                        )
                    )
                )
            )

            possui_confrontantes = bool(
                (
                    isinstance(dados_json, dict)
                    and dados_json.get("confrontantes")
                )
            )

            possui_historico = bool(
                (
                    isinstance(dados_json, dict)
                    and dados_json.get(
                        "historico_registral"
                    )
                )
            )

            cur.execute(
                """
                UPDATE ocr_results
                SET
                    status = 'DONE',

                    provider = %s,

                    prompt_nome = %s,

                    categoria = %s,

                    modelo_llm = %s,

                    parser_utilizado = %s,

                    normalizador_utilizado = %s,

                    pipeline_versao = %s,

                    score_confianca = %s,

                    possui_geojson = %s,

                    possui_memorial = %s,

                    possui_confrontantes = %s,

                    possui_historico = %s,

                    texto_extraido = %s,

                    dados_extraidos_json = %s,

                    erro = NULL,

                    processado_em = NOW(),

                    updated_at = NOW()

                WHERE id = %s
                """,
                (
                    prompt.get("engine")
                    or "GOOGLE_VISION_OPENAI",

                    prompt.get("nome"),

                    prompt.get("categoria"),

                    prompt.get("modelo_llm"),

                    prompt.get("parser_service"),

                    prompt.get("normalizer_service"),

                    (
                        prompt.get("versao")
                        or "OCR_PIPELINE_V2"
                    ),

                    score_confianca,

                    possui_geojson,

                    possui_memorial,

                    possui_confrontantes,

                    possui_historico,

                    texto,

                    Json(dados_json),

                    ocr_result_id,
                ),
            )

            conn.commit()

def update_result_error(
    ocr_result_id: int,
    error_message: str,
    prompt: dict | None = None,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            provider = "GOOGLE_VISION_OPENAI"

            categoria = None
            prompt_nome = None
            modelo_llm = None
            parser_utilizado = None
            normalizador_utilizado = None
            pipeline_versao = "OCR_PIPELINE_V2"

            if isinstance(prompt, dict):

                provider = (
                    prompt.get("engine")
                    or provider
                )

                categoria = prompt.get("categoria")

                prompt_nome = prompt.get("nome")

                modelo_llm = prompt.get("modelo_llm")

                parser_utilizado = (
                    prompt.get("parser_service")
                )

                normalizador_utilizado = (
                    prompt.get("normalizer_service")
                )

                pipeline_versao = (
                    prompt.get("versao")
                    or pipeline_versao
                )

            cur.execute(
                """
                UPDATE ocr_results
                SET
                    status = 'ERROR',

                    provider = %s,

                    categoria = %s,

                    prompt_nome = %s,

                    modelo_llm = %s,

                    parser_utilizado = %s,

                    normalizador_utilizado = %s,

                    pipeline_versao = %s,

                    erro = %s,

                    updated_at = NOW()

                WHERE id = %s
                """,
                (
                    provider,

                    categoria,

                    prompt_nome,

                    modelo_llm,

                    parser_utilizado,

                    normalizador_utilizado,

                    pipeline_versao,

                    error_message,

                    ocr_result_id,
                ),
            )

            conn.commit()


def merge_job_payload(
    job_id: int,
    patch: dict,
):
    with get_connection() as conn:

        try:

            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:

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

                current: dict = {}

                if (
                    row
                    and isinstance(
                        row.get("payload_json"),
                        dict,
                    )
                ):
                    current = row["payload_json"]

                if not isinstance(patch, dict):
                    patch = {}

                # =====================================================
                # MERGE PROFUNDO
                # =====================================================
                def deep_merge(
                    base: dict,
                    incoming: dict,
                ) -> dict:

                    result = dict(base)

                    for key, value in incoming.items():

                        if (
                            key in result
                            and isinstance(
                                result[key],
                                dict,
                            )
                            and isinstance(
                                value,
                                dict,
                            )
                        ):
                            result[key] = deep_merge(
                                result[key],
                                value,
                            )

                        else:
                            result[key] = value

                    return result

                merged_payload = deep_merge(
                    current,
                    patch,
                )

                # =====================================================
                # METADADOS TÉCNICOS
                # =====================================================
                merged_payload[
                    "_updated_at"
                ] = datetime.utcnow().isoformat()

                cur.execute(
                    """
                    UPDATE automation_jobs
                    SET
                        payload_json = %s
                    WHERE id = %s
                    """,
                    (
                        Json(merged_payload),
                        job_id,
                    ),
                )

                conn.commit()

        except Exception:

            conn.rollback()
            raise


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


def extrair_texto_documento(
    file_path: str,
) -> str:

    # =========================================================
    # IMAGEM
    # =========================================================
    if _is_image(file_path):

        return extrair_texto_imagem_google(
            file_path
        )

    # =========================================================
    # PDF
    # =========================================================
    if _is_pdf(file_path):

        texto_nativo = (
            extrair_texto_pdf_nativo(
                file_path
            )
        )

        texto_nativo_limpo = (
            texto_nativo.strip()
        )

        # =====================================================
        # PDF COM TEXTO NATIVO VÁLIDO
        # =====================================================
        if len(texto_nativo_limpo) >= 500:

            print(
                "✅ PDF possui texto nativo válido"
            )

            return texto_nativo_limpo

        # =====================================================
        # FALLBACK OCR GOOGLE VISION
        # =====================================================
        print(
            "⚠️ PDF sem texto suficiente. "
            "Executando OCR Google Vision..."
        )

        texto_ocr = (
            extrair_texto_pdf_ocr_google(
                file_path
            )
        )

        texto_ocr_limpo = (
            texto_ocr.strip()
        )

        if texto_ocr_limpo:

            return texto_ocr_limpo

        # =====================================================
        # ÚLTIMO FALLBACK
        # =====================================================
        if texto_nativo_limpo:

            print(
                "⚠️ OCR não retornou conteúdo. "
                "Usando texto nativo parcial."
            )

            return texto_nativo_limpo

        raise Exception(
            "Nenhum texto pôde ser extraído do PDF."
        )

    # =========================================================
    # FORMATO INVÁLIDO
    # =========================================================
    raise Exception(
        "Formato não suportado para OCR. "
        "Permitidos: PDF, JPG, JPEG, PNG, WEBP."
    )


def interpretar_texto(
    prompt_config: dict,
    texto: str,
):
    openai_client = get_openai_client()

    modelo_llm = (
        prompt_config.get("modelo_llm")
        or "gpt-4o-mini"
    )

    temperatura = 0

    try:
        temperatura = float(
            prompt_config.get(
                "temperatura",
                0,
            )
        )
    except Exception:
        temperatura = 0

    completion_params = {
        "model": modelo_llm,

        "temperature": temperatura,

        "messages": [
            {
                "role": "system",
                "content": (
                    f"{prompt_config['prompt']}\n\n"
                    "Retorne JSON válido sempre que possível. "
                    "Não use markdown. "
                    "Não use bloco ```json. "
                    "Quando houver listas, "
                    "retorne arrays JSON. "
                    "Quando não encontrar algum campo, "
                    "use null ou array vazio."
                ),
            },
            {
                "role": "user",
                "content": texto,
            },
        ],
    }

    max_tokens = prompt_config.get(
        "max_tokens_llm"
    )

    if max_tokens:

        try:
            completion_params["max_tokens"] = int(
                max_tokens
            )
        except Exception:
            pass

    completion = (
        openai_client.chat.completions.create(
            **completion_params
        )
    )

    content = (
        completion.choices[0]
        .message
        .content
        or ""
    )

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

        dados_raw = interpretar_texto(
            prompt,
            texto,
        )

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

        update_result_success(
            ocr_result_id,
            texto,
            dados,
            prompt,
        )

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
        update_result_error(
            ocr_result_id,
            str(e),
            prompt,
        )

        if job_id:
            merge_job_payload(
                job_id,
                {
                    "pipeline_success": False,
                    "ocr_error": str(e),
                },
            )

        raise