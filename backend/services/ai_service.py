import json
import os
import re
import threading
from datetime import datetime

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
    _state.last_meta = None


def get_last_error() -> dict:
    return getattr(_state, "last_error", None) or {}


def _set_last_meta(provider: str, model: str | None):
    _state.last_meta = {"provider": provider, "model": model}


def get_last_meta() -> dict:
    return getattr(_state, "last_meta", None) or {}


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


def _resolve_settings(ai_options: dict | None = None) -> tuple[str, str | None, str]:
    opts = ai_options or {}
    provider = _normalize_provider(opts.get("provider"))
    model = (opts.get("model") or "").strip() or None
    task = (opts.get("task") or "").strip().lower()

    if provider == "openai":
        return provider, model or _runtime_openai_model, task
    if provider == "gemini":
        return provider, model or _runtime_gemini_model, task
    return provider, model, task


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
        "external_fallback": True,
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
    provider, model, task = _resolve_settings(ai_options)

    if provider == "openai":
        result = _call_openai(prompt, timeout, model)
        if result:
            _set_last_meta("openai", model or _runtime_openai_model)
        return result
    if provider == "gemini":
        result = _call_gemini(prompt, timeout, model)
        if result:
            _set_last_meta("gemini", model or _runtime_gemini_model)
        return result

    # auto mode routing by task
    openai_model = model if _is_openai_model(model or "") else _runtime_openai_model
    gemini_model = model if _is_gemini_model(model or "") else _runtime_gemini_model

    prefer_gemini = task in {"summary", "long"}
    prefer_openai = task in {"structured", "json"}

    if prefer_gemini and not prefer_openai:
        result = _call_gemini(prompt, timeout, gemini_model)
        if result:
            _set_last_meta("gemini", gemini_model)
            return result
        result = _call_openai(prompt, timeout, openai_model)
        if result:
            _set_last_meta("openai", openai_model)
        return result

    # default: prefer OpenAI, then fallback to Gemini
    if prefer_openai or not prefer_gemini:
        result = _call_openai(prompt, timeout, openai_model)
        if result:
            _set_last_meta("openai", openai_model)
            return result
        result = _call_gemini(prompt, timeout, gemini_model)
        if result:
            _set_last_meta("gemini", gemini_model)
        return result

    result = _call_openai(prompt, timeout, openai_model)
    if result:
        _set_last_meta("openai", openai_model)
        return result
    result = _call_gemini(prompt, timeout, gemini_model)
    if result:
        _set_last_meta("gemini", gemini_model)
    return result


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


def _extract_citations(question: str, material_text: str, max_snippets: int = 3) -> list[dict]:
    if not material_text or not material_text.strip():
        return []

    stopwords = {
        "the","is","are","was","were","a","an","and","or","to","of","in","on","for","with","as","by","at","from",
        "that","this","these","those","it","its","be","been","being","we","you","your","their","they","he","she","his",
        "her","our","ours","but","if","then","than","so","not","no","do","does","did","can","could","will","would",
    }
    words = re.findall(r"[a-zA-Z0-9]+", (question or "").lower())
    keywords = {w for w in words if w not in stopwords and len(w) > 2}
    if not keywords:
        return []

    # Split into paragraphs and score by keyword overlap
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", material_text) if p.strip()]
    scored = []
    for idx, p in enumerate(paragraphs):
        tokens = set(re.findall(r"[a-zA-Z0-9]+", p.lower()))
        score = len(tokens & keywords)
        if score:
            scored.append((score, idx, p))

    scored.sort(key=lambda x: (-x[0], x[1]))
    citations = []
    for score, idx, p in scored[:max_snippets]:
        snippet = p[:320].strip()
        citations.append({"snippet": snippet, "index": idx, "score": score})
    return citations


def _current_date_str() -> str:
    return datetime.now().strftime("%B %d, %Y")


def _is_date_question(question: str) -> bool:
    q = (question or "").strip().lower()
    return "today" in q and ("date" in q or "day" in q)


def summarize_session(existing_summary: str, recent_messages: list, ai_options: dict | None = None) -> str:
    if not recent_messages:
        return existing_summary or ""

    convo = []
    for msg in recent_messages[-8:]:
        role = "Student" if msg.get("role") == "user" else "StudyBot"
        convo.append(f"{role}: {msg.get('content', '')}")
    convo_text = "\n".join(convo)

    prompt = f"""You are summarizing a tutoring chat.
Update the running summary using the new messages below.
Keep it concise (max 700 characters). Focus on goals, key facts, and decisions.

EXISTING SUMMARY:
{existing_summary or "None"}

NEW MESSAGES:
{convo_text}

Return ONLY the updated summary text."""

    opts = dict(ai_options or {})
    opts["task"] = "summary"
    updated = _call_ai(prompt, ai_options=opts)
    if not updated:
        return existing_summary or ""
    return updated.strip()[:700]


def answer_from_material(
    question: str,
    material_text: str,
    history: list,
    ai_options: dict | None = None,
    session_summary: str = "",
) -> dict:
    history_text = ""
    for msg in (history or [])[-8:]:
        role = "Student" if msg.get("role") == "user" else "StudyBot"
        history_text += f"{role}: {msg.get('content', '')}\n"

    prompt = f"""You are StudyBot, a helpful AI tutor for students.
Answer the student's question using ONLY the study material provided below.

STUDY MATERIAL:
{_truncate(material_text, 8000)}

SESSION SUMMARY:
{session_summary if session_summary else 'None'}

PREVIOUS CONVERSATION:
{history_text if history_text else 'None'}

STUDENT QUESTION: {question}

STRICT RULES:
1. If the answer exists in the material, answer clearly.
2. If the answer does not exist in the material, reply with exactly: NOT_IN_MATERIAL
3. Never use outside knowledge.

Answer:"""

    opts = dict(ai_options or {})
    opts["task"] = "qa"
    result = _call_ai(prompt, ai_options=opts)
    answer_model = get_last_meta()
    if not result:
        return {
            "answer": "AI is currently unavailable. Check OPENAI_API_KEY/GEMINI_API_KEY and try again.",
            "source": "ai_unavailable",
            "confidence": "low",
        }

    if "NOT_IN_MATERIAL" in result:
        fallback_prompt = f"""You are StudyBot, a helpful tutor.
The user's question was NOT answered by their uploaded material.
Provide a concise, accurate answer from general knowledge.
Be clear and helpful, avoid fabricating specifics.

Current date: {_current_date_str()}

Question: {question}

Answer:"""
        fallback_opts = dict(ai_options or {})
        fallback_opts["task"] = "qa"
        fallback = _call_ai(fallback_prompt, ai_options=fallback_opts)
        fallback_model = get_last_meta()
        if not fallback:
            return {
                "answer": (
                    "I could not find this answer in your uploaded material, and the AI is currently unavailable.\n\n"
                    "Please try again, or upload a material that covers this topic."
                ),
                "source": "not_found",
                "confidence": "low",
                "model_info": fallback_model,
            }

        if _is_date_question(question):
            fallback = f"Today's date is {_current_date_str()}."

        fallback = re.sub(r"\[current date[^\]]*\]", _current_date_str(), fallback, flags=re.IGNORECASE)

        return {
            "answer": (
                "I could not find this answer in your uploaded material.\n"
                "The response below is from general knowledge (not from your material):\n\n"
                f"{fallback}"
            ),
            "source": "external",
            "confidence": "medium",
            "model_info": fallback_model,
        }

    citations = _extract_citations(question, material_text)
    confidence = "high"
    note = ""
    if not citations:
        confidence = "medium"

    answer_text = result
    if note:
        answer_text = f"{result}\n\nNote: {note}"

    return {
        "answer": answer_text,
        "source": "material",
        "confidence": confidence,
        "citations": citations,
        "model_info": answer_model,
    }


def get_key_topics(material_text: str, ai_options: dict | None = None) -> list:
    if not material_text or not material_text.strip():
        return []

    prompt = f"""Analyze this study material and extract the 10 most important topics.

MATERIAL:
{_truncate(material_text, 6000)}

Return ONLY a valid JSON array of strings.
Topics:"""

    opts = dict(ai_options or {})
    opts["task"] = "structured"
    topics = _parse_json_array(_call_ai(prompt, ai_options=opts))
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

    opts = dict(ai_options or {})
    opts["task"] = "structured"
    return _parse_json_array(_call_ai(prompt, ai_options=opts))


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

    opts = dict(ai_options or {})
    opts["task"] = "structured"
    questions = _parse_json_array(_call_ai(prompt, timeout=90, ai_options=opts))
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

    opts = dict(ai_options or {})
    opts["task"] = "structured"
    questions = _parse_json_array(_call_ai(prompt, timeout=90, ai_options=opts))
    valid = []
    for q in questions:
        if isinstance(q, dict) and {"question", "model_answer"}.issubset(q.keys()):
            valid.append(q)
    return valid


def generate_flashcards(material_text: str, count: int = 12, ai_options: dict | None = None) -> list:
    if not material_text or not material_text.strip():
        return []

    prompt = f"""Create {count} concise study flashcards from the material.

MATERIAL:
{_truncate(material_text, 6000)}

Return ONLY valid JSON array:
[
  {{"question": "...", "answer": "..."}}
]
"""

    opts = dict(ai_options or {})
    opts["task"] = "structured"
    cards = _parse_json_array(_call_ai(prompt, timeout=90, ai_options=opts))
    valid = []
    for c in cards:
        if isinstance(c, dict) and c.get("question") and c.get("answer"):
            valid.append({"question": str(c.get("question")).strip(), "answer": str(c.get("answer")).strip()})
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

    opts = dict(ai_options or {})
    opts["task"] = "structured"
    parsed = _parse_json_object(_call_ai(prompt, ai_options=opts))
    if not parsed or "score" not in parsed:
        return {
            "score": 5,
            "feedback": "Auto-evaluation unavailable. Please review manually.",
            "missed_points": [],
        }
    return parsed
