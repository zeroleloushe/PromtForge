"""Вызовы инструментов: разбор текстовых <tool_call>, починка JSON, режим «инструменты в промте», сжатие контекста."""
from __future__ import annotations

import json
import re
import uuid

from app.mcp.builtin import ToolDef

TOOL_OPEN, TOOL_CLOSE = "<tool_call>", "</tool_call>"


def new_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:10]


# ----------------------------------------------------------------------------- JSON
def _repair(s: str):
    """Чинит оборванный JSON (модель упёрлась в max_tokens): закрывает строку и скобки, убирает хвостовую запятую."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    fixed = s + ('"' if in_str else "")
    fixed = re.sub(r",\s*$", "", fixed) + "".join(reversed(stack))
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def parse_args(raw) -> tuple[dict, str]:
    """Аргументы вызова (строка JSON или dict) -> (dict, текст ошибки)."""
    if isinstance(raw, dict):
        return raw, ""
    s = (raw or "").strip() if isinstance(raw, str) else ""
    if not s:
        return {}, ""
    try:
        v = json.loads(s)
    except json.JSONDecodeError:
        v = _repair(s)
        if v is None:
            return {}, f"Аргументы не являются корректным JSON: {s[:160]}"
    if isinstance(v, str):                     # двойное кодирование
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            return {}, "Аргументы должны быть JSON-объектом"
    if not isinstance(v, dict):
        return {}, "Аргументы должны быть JSON-объектом"
    return v, ""


# ----------------------------------------------------------------------------- текстовые вызовы
_FUNC_XML = re.compile(r"<function=([^>\s]+)>(.*?)</function>", re.S)
_PARAM_XML = re.compile(r"<parameter=([^>\s]+)>\n?(.*?)\n?</parameter>", re.S)


def parse_text_tool_call(block: str) -> dict | None:
    """Содержимое одного <tool_call>…</tool_call> -> {"name","arguments"(str JSON)} или None.

    Форматы: JSON {"name":…, "arguments":{…}} (Hermes/Qwen) и XML <function=name><parameter=k>v</parameter></function> (Qwen3-Coder).
    """
    block = block.strip()
    m = _FUNC_XML.search(block)
    if m:
        args = {}
        for k, v in _PARAM_XML.findall(m.group(2)):
            try:
                args[k] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                args[k] = v
        return {"name": m.group(1), "arguments": json.dumps(args, ensure_ascii=False)}
    block = re.sub(r"^```(?:json)?\s*|\s*```$", "", block)
    try:
        obj = json.loads(block)
    except json.JSONDecodeError:
        obj = _repair(block)
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if not isinstance(obj, dict):
        return None
    fn = obj["function"] if isinstance(obj.get("function"), dict) else obj
    name = fn.get("name") or obj.get("name")
    if not name:
        return None
    args = fn.get("arguments", fn.get("parameters", fn.get("args", {})))
    return {"name": str(name), "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}


class ToolTagFilter:
    """Вырезает блоки <tool_call>…</tool_call> из потока текста и собирает их содержимое (учитывает разрыв тегов между чанками)."""

    def __init__(self):
        self.inside = False
        self.carry = ""
        self.blocks: list[str] = []
        self._cur: list[str] = []

    @staticmethod
    def _tail(buf: str, tag: str) -> str:
        for n in range(min(len(tag) - 1, len(buf)), 0, -1):
            if tag.startswith(buf[-n:]):
                return buf[-n:]
        return ""

    def feed(self, text: str) -> str:
        buf, self.carry, out = self.carry + text, "", []
        while buf:
            if self.inside:
                i = buf.find(TOOL_CLOSE)
                if i < 0:
                    keep = self._tail(buf, TOOL_CLOSE)
                    self._cur.append(buf[: len(buf) - len(keep)])
                    self.carry = keep
                    return "".join(out)
                self._cur.append(buf[:i])
                self.blocks.append("".join(self._cur))
                self._cur = []
                buf = buf[i + len(TOOL_CLOSE):].lstrip("\n")
                self.inside = False
            else:
                i = buf.find(TOOL_OPEN)
                if i < 0:
                    keep = self._tail(buf, TOOL_OPEN)
                    out.append(buf[: len(buf) - len(keep)])
                    self.carry = keep
                    return "".join(out)
                out.append(buf[:i])
                buf = buf[i + len(TOOL_OPEN):]
                self.inside = True
        return "".join(out)

    def flush(self) -> str:
        if self.inside:                      # блок не закрыт (обрыв по max_tokens) — пробуем починить как есть
            self.blocks.append("".join(self._cur) + self.carry)
            self._cur, self.carry, self.inside = [], "", False
            return ""
        rest, self.carry = self.carry, ""
        return rest


# ----------------------------------------------------------------------------- режим «инструменты в промте»
def render_tools_prompt(defs: list[ToolDef]) -> str:
    tools = "\n".join(json.dumps(d.as_openai()["function"], ensure_ascii=False) for d in defs)
    return (
        "\n\n# Tools\n\nYou may call one or more functions to assist with the user query. "
        "Function signatures are listed inside <tools></tools>:\n<tools>\n" + tools + "\n</tools>\n\n"
        "To call a function, output exactly this and then STOP and wait for the result:\n"
        "<tool_call>\n{\"name\": <function-name>, \"arguments\": <args-json-object>}\n</tool_call>\n"
        "The result will arrive in the next message inside <tool_response></tool_response>. "
        "Do not invent results. When you have enough information, answer normally without a tool_call.")


def to_prompt_messages(messages: list[dict], defs: list[ToolDef]) -> list[dict]:
    """API-сообщения с tool_calls/tool -> обычные сообщения, которые понимает сервер без поддержки tools."""
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            out.append({"role": "system", "content": _text(m["content"]) + (render_tools_prompt(defs) if defs else "")})
        elif role == "assistant" and m.get("tool_calls"):
            blocks = []
            for c in m["tool_calls"]:
                f = c["function"]
                args, _ = parse_args(f.get("arguments"))
                blocks.append(f"{TOOL_OPEN}\n{json.dumps({'name': f['name'], 'arguments': args}, ensure_ascii=False)}\n{TOOL_CLOSE}")
            body = (_text(m.get("content")) + "\n" if m.get("content") else "") + "\n".join(blocks)
            out.append({"role": "assistant", "content": body})
        elif role == "tool":
            block = f"<tool_response>\n{_text(m['content'])}\n</tool_response>"
            if out and out[-1]["role"] == "user" and out[-1].get("_tool"):
                out[-1]["content"] += "\n" + block
            else:
                out.append({"role": "user", "content": block, "_tool": True})
        else:
            out.append({"role": role, "content": m["content"]})
    for m in out:
        m.pop("_tool", None)
    return out


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def msg_len(m: dict) -> int:
    n = len(_text(m.get("content")))
    for c in m.get("tool_calls") or []:
        n += len(c["function"].get("arguments") or "") + len(c["function"]["name"]) + 40
    if isinstance(m.get("content"), list):
        n += 1500 * sum(1 for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url")
    return n


def _shrink(m: dict, keep: int) -> None:
    txt = _text(m["content"])
    if len(txt) > keep + 100:
        m["content"] = txt[: int(keep * 0.75)] + "\n… [результат сокращён, чтобы освободить контекст] …\n" + txt[-int(keep * 0.25):]


def fit_messages(messages: list[dict], budget_chars: int) -> tuple[list[dict], int]:
    """Сжимает историю под бюджет. Сначала сокращает старые результаты инструментов, потом отбрасывает самое старое.

    Последний вопрос пользователя и всё после него (текущий ход агента) сохраняются; структура tool_calls↔tool не нарушается.
    Возвращает (сообщения, сколько сообщений отброшено).
    """
    msgs = [dict(m) for m in messages]
    total = sum(msg_len(m) for m in msgs)
    if total <= budget_chars:
        return msgs, 0
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    rest = [m for m in msgs if m["role"] != "system"]

    def last_user() -> int:
        return max((i for i, m in enumerate(rest) if m["role"] == "user"), default=0)

    for m in rest[: last_user()]:
        if m["role"] == "tool":
            _shrink(m, 600)
    total = sum(msg_len(m) for m in sys_msgs + rest)
    dropped = 0
    while total > budget_chars and last_user() > 0:
        total -= msg_len(rest.pop(0))
        dropped += 1
    if total > budget_chars:                      # текущий ход сам не помещается — ужимаем его результаты
        for m in rest:
            if m["role"] == "tool":
                _shrink(m, 2500)
    return sys_msgs + rest, dropped
