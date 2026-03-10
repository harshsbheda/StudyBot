import json
import os
import re
import threading

import config

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    import google.generativeai as genai
except Exception:
    genai = None

os.environ.setdefault("GRPC_DNS_RESOLVER", "native")

_openai_client = None
_gemini_models = {}
_state = threading.local()

SUPPORTED_PROVIDERS = {"auto", "gemini", "openai"}
AVAILABLE_MODELS = {
    "gemini": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
    ],
}

_runtime_provider = config.AI_PROVIDER if config.AI_PROVIDER in SUPPORTED_PROVIDERS else "auto"
_runtime_openai_model = config.OPENAI_MODEL
_runtime_gemini_model = config.GEMINI_MODEL


def _set_last_error(code: str, message: str, retry_after: int = 0):
    _state.last_error = {"code": code, "message": message, "retry_after": max(int(retry_after or 0), 0)}


def clear_last_error():
    _state.last_error = None


def get_last_error() -> dict:
    return getattr(_state, "last_error", None) or {}


def _detect_quota_error(exc: Exception) -> tuple[bool, int]:
    msg = str(exc).lower()
    is_quota = any(token in msg for token in ["quota", "rate limit", "insufficient_quota", "429"])
    retry_after = 0
    match = re.search(r"retry(?: in)?\s+(\d+)", msg)
    if match:
        retry_after = int(match.group(1))
    return is_quota, retry_after


def _is_openai_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3")


def _is_gemini_model(model: str) -> bool:
    return (model or "").strip().lower().startswith("gemini-")


def _normalize_provider(provider: str | None) -> str:
    p = (provider or "").strip().lower()
    if p in SUPPORTED_PROVIDERS:
        return p
    return _runtime_provider


def _resolve_settings(ai_options: dict | None = None) -> tuple[str, str | None]:
    opts = ai_options or {}
    provider = _normalize_provider(opts.get("provider"))
    model = (opts.get("model") or "").strip() or None

    if provider == "openai":
        return provider, model or _runtime_openai_model
    if provider == "gemini":
        return provider, model or _runtime_gemini_model
    return provider, model


def get_ai_settings() -> dict:
    current_model = None
    if _runtime_provider == "openai":
        current_model = _runtime_openai_model
    elif _runtime_provider == "gemini":
        current_model = _runtime_gemini_model

    return {
        "provider": _runtime_provider,
        "model": current_model,
        "openai_model": _runtime_openai_model,
        "gemini_model": _runtime_gemini_model,
        "providers": ["auto", "gemini", "openai"],
        "models": AVAILABLE_MODELS,
        "capabilities": {
            "text_chat": True,
            "speech_input_browser": True,
            "speech_output_browser": True,
        },
    }


def set_ai_settings(provider: str | None = None, model: str | None = None) -> dict:
    global _runtime_provider, _runtime_openai_model, _runtime_gemini_model

    if provider:
        p = provider.strip().lower()
        if p not in SUPPORTED_PROVIDERS:
            raise ValueError("Invalid provider. Use: auto, gemini, openai")
        _runtime_provider = p

    if model and model.strip():
        m = model.strip()
        target_provider = _runtime_provider
        if target_provider == "auto":
            if _is_gemini_model(m):
                target_provider = "gemini"
            elif _is_openai_model(m):
                target_provider = "openai"

        if target_provider == "openai":
            _runtime_openai_model = m
        elif target_provider == "gemini":
            _runtime_gemini_model = m

    return get_ai_settings()


def _get_gemini_model(model_name: str):
    if model_name in _gemini_models:
        return _gemini_models[model_name]
    if not genai or not config.GEMINI_API_KEY:
        return None
    try:
        genai.configure(api_key=config.GEMINI_API_KEY, transport="rest")
        model = genai.GenerativeModel(model_name)
        _gemini_models[model_name] = model
        return model
    except Exception as exc:
        print(f"[AI] Gemini init error: {exc}")
        return None


def _get_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if not OpenAI or not config.OPENAI_API_KEY:
        return None
    try:
        _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        return _openai_client
    except Exception as exc:
        print(f"[AI] OpenAI init error: {exc}")
        return None


def _call_openai(prompt: str, timeout: int = 60, model_name: str | None = None) -> str:
    client = _get_openai_client()
    if not client:
        return ""
    try:
        response = client.chat.completions.create(
            model=model_name or _runtime_openai_model,
            messages=[
                {"role": "system", "content": "You are StudyBot, a helpful AI tutor."},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        print(f"[AI] OpenAI error: {exc}")
        is_quota, retry_after = _detect_quota_error(exc)
        if is_quota:
            _set_last_error("quota_exceeded", str(exc), retry_after)
        else:
            _set_last_error("provider_error", str(exc), 0)
        return ""


def _call_gemini(prompt: str, timeout: int = 60, model_name: str | None = None) -> str:
    model = _get_gemini_model(model_name or _runtime_gemini_model)
    if not model:
        return ""
    try:
        response = model.generate_content(prompt, request_options={"timeout": timeout})
        return response.text.strip() if getattr(response, "text", None) else ""
    except Exception as exc:
        print(f"[AI] Gemini error: {exc}")
        is_quota, retry_after = _detect_quota_error(exc)
        if is_quota:
            _set_last_error("quota_exceeded", str(exc), retry_after)
        else:
            _set_last_error("provider_error", str(exc), 0)
        return ""


def _call_ai(prompt: str, timeout: int = 60, ai_options: dict | None = None) -> str:
    clear_last_error()
    provider, model = _resolve_settings(ai_options)

    if provider == "openai":
        return _call_openai(prompt, timeout, model)
    if provider == "gemini":
        return _call_gemini(prompt, timeout, model)

    # auto mode: prefer OpenAI, then fallback to Gemini
    openai_model = model if _is_openai_model(model or "") else _runtime_openai_model
    gemini_model = model if _is_gemini_model(model or "") else _runtime_gemini_model

    result = _call_openai(prompt, timeout, openai_model)
    if result:
        return result
    return _call_gemini(prompt, timeout, gemini_model)


def _parse_json_array(text: str) -> list:
    if not text:
        return []
    try:
        cleaned = re.sub(r"```json|```", "", text).strip()
        if cleaned.startswith("["):
            return json.loads(cleaned)
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return []


def _parse_json_object(text: str) -> dict:
    if not text:
        return {}
    try:
        cleaned = re.sub(r"```json|```", "", text).strip()
        if cleaned.startswith("{"):
            return json.loads(cleaned)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {}


def _truncate(text: str, max_chars: int = 8000) -> str:
    return text[:max_chars] if text else ""


def answer_from_material(question: str, material_text: str, history: list, ai_options: dict | None = None) -> dict:
    history_text = ""
    for msg in (history or [])[-8:]:
        role = "Student" if msg.get("role") == "user" else "StudyBot"
        history_text += f"{role}: {msg.get('content', '')}\n"

    prompt = f"""You are StudyBot, a helpful AI tutor for students.
Answer the student's question using ONLY the study material provided below.

STUDY MATERIAL:
{_truncate(material_text, 8000)}

PREVIOUS CONVERSATION:
{history_text if history_text else 'None'}

STUDENT QUESTION: {question}

STRICT RULES:
1. If the answer exists in the material, answer clearly.
2. If the answer does not exist in the material, reply with exactly: NOT_IN_MATERIAL
3. Never use outside knowledge.

Answer:"""

    result = _call_ai(prompt, ai_options=ai_options)
    if not result:
        return {
            "answer": "AI is currently unavailable. Check OPENAI_API_KEY/GEMINI_API_KEY and try again.",
            "source": "ai_unavailable",
        }

    if "NOT_IN_MATERIAL" in result:
        return {
            "answer": (
                "I could not find this answer in your uploaded material.\n\n"
                "You can check these sources:\n"
                "- Google: https://google.com\n"
                "- Google Scholar: https://scholar.google.com\n"
                "- Khan Academy: https://khanacademy.org\n"
                "- Wikipedia: https://wikipedia.org\n\n"
                "If you upload a material that covers this topic, I can answer directly from it."
            ),
            "source": "not_found",
        }

    return {"answer": result, "source": "material"}


def get_key_topics(material_text: str, ai_options: dict | None = None) -> list:
    if not material_text or not material_text.strip():
        return []

    prompt = f"""Analyze this study material and extract the 10 most important topics.

MATERIAL:
{_truncate(material_text, 6000)}

Return ONLY a valid JSON array of strings.
Topics:"""

    topics = _parse_json_array(_call_ai(prompt, ai_options=ai_options))
    if topics:
        return topics
    return ["Review the uploaded material for key topics"]


def generate_important_questions(material_text: str, count: int = 10, ai_options: dict | None = None) -> list:
    if not material_text or not material_text.strip():
        return []

    prompt = f"""Generate {count} important exam questions from this study material.

MATERIAL:
{_truncate(material_text, 6000)}

Return ONLY a valid JSON array:
[
  {{"question": "What is ...?", "type": "short", "importance": "high"}}
]

Questions:"""

    return _parse_json_array(_call_ai(prompt, ai_options=ai_options))


def generate_mcq_test(material_text: str, count: int = 10, difficulty: str = "medium", ai_options: dict | None = None) -> list:
    prompt = f"""Generate exactly {count} MCQ questions at {difficulty} difficulty.

MATERIAL:
{_truncate(material_text, 6000)}

Return ONLY JSON array:
[
  {{
    "question": "...",
    "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
    "correct": "A",
    "explanation": "..."
  }}
]
"""

    questions = _parse_json_array(_call_ai(prompt, timeout=90, ai_options=ai_options))
    valid = []
    for q in questions:
        if isinstance(q, dict) and {"question", "options", "correct"}.issubset(q.keys()):
            valid.append(q)
    return valid


def generate_short_answer_test(material_text: str, count: int = 5, ai_options: dict | None = None) -> list:
    prompt = f"""Generate exactly {count} short-answer questions.

MATERIAL:
{_truncate(material_text, 6000)}

Return ONLY JSON array:
[
  {{
    "question": "...",
    "model_answer": "...",
    "keywords": ["...", "..."]
  }}
]
"""

    questions = _parse_json_array(_call_ai(prompt, timeout=90, ai_options=ai_options))
    valid = []
    for q in questions:
        if isinstance(q, dict) and {"question", "model_answer"}.issubset(q.keys()):
            valid.append(q)
    return valid


def evaluate_short_answer(question: str, model_answer: str, student_answer: str, ai_options: dict | None = None) -> dict:
    if not student_answer or not student_answer.strip():
        return {"score": 0, "feedback": "No answer provided.", "missed_points": []}

    prompt = f"""Evaluate this student's answer fairly.

Question: {question}
Model Answer: {model_answer}
Student Answer: {student_answer}

Return ONLY JSON:
{{"score": 7, "feedback": "...", "missed_points": ["...", "..."]}}
"""

    parsed = _parse_json_object(_call_ai(prompt, ai_options=ai_options))
    if not parsed or "score" not in parsed:
        return {
            "score": 5,
            "feedback": "Auto-evaluation unavailable. Please review manually.",
            "missed_points": [],
        }
    return parsed

