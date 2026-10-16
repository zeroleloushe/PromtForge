"""ContextBuilder: SYSTEM PROMPT + MODEL RULES + ACTIVE STYLES + REFERENCES + USER REQUEST + CURRENT PROMPT + EDIT.

Не знает про UI и сеть: получает данные, возвращает готовый список messages для LLM.
Один стиль никогда не изменяет другой — все выбранные стили просто перечисляются по приоритету.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.model_manager import Model
from app.core.prompt_file import resolve_system_text
from app.core.reference_manager import Reference, ReferenceManager
from app.core.style_manager import Style

GENERIC_RULES = [
    "Return ONLY the final prompt text, with no preface, comments or explanations.",
    "Follow the output format required by the system prompt exactly.",
    "Respect every active style below at the same time; if two instructions conflict, the one with higher priority wins.",
    "Preserve the user's original intent and never add unrelated characters, objects or events.",
    "Never output shell commands or instructions for the user's computer.",
]

CREATIVITY = {
    "precise": "CREATIVITY: conservative. Do not invent details that are not implied by the request or references.",
    "standard": "",
    "bold": "CREATIVITY: bold. You may creatively enrich the idea with fitting details, while keeping the user's intent.",
}


@dataclass
class ContextOptions:
    aspect_ratio: str = ""          # "" = не указывать
    creativity: str = "standard"    # precise | standard | bold
    include_history: bool = True    # учитывать прежние правки
    send_images: bool = True        # использовать vision
    h3_length: int = 0              # MiniMax H3: длина в кадрах (17k+5 @ 24 fps); 0 = не задавать
    picture_labels: bool = False    # MiniMax H3: картинки подписываются как <Picture N>


@dataclass
class BuiltContext:
    messages: list[dict]
    has_images: bool = False
    warnings: list[str] = field(default_factory=list)  # ключи i18n


class ContextBuilder:
    def build(self, model: Model, styles: list[Style], user_request: str,
              references: list[Reference] | None = None, current_prompt: str = "",
              edit_request: str = "", previous_edits: list[str] | None = None,
              options: ContextOptions | None = None) -> BuiltContext:
        opts = options or ContextOptions()
        refs = [r for r in (references or []) if r.exists()]
        warnings: list[str] = []
        if references and len(refs) != len(references):
            warnings.append("warn_ref_missing")

        system, file_warn = self._system(model, styles)
        if file_warn:
            warnings.append(file_warn)

        # --- сообщение пользователя --------------------------------------
        lines: list[str] = []
        images: list[Reference] = []
        if refs:
            can_see = opts.send_images and model.supports_vision
            lines.append(f"REFERENCES ({len(refs)}):")
            n_img = n_vid = n_aud = 0
            described = False
            for r in refs:
                desc = " ".join((r.description or "").split())[:800]
                tail = f" - description: {desc}" if desc else ""
                described = described or bool(desc)
                if r.type == "image":
                    n_img += 1
                    note = "attached below" if can_see else "not attached - filename only"
                    tag = f"<Picture {n_img}>" if opts.picture_labels else f"Image {n_img}"
                    lines.append(f"- {tag}: {r.name} ({note}){tail}")
                    if can_see:
                        images.append(r)
                elif r.type == "audio":
                    n_aud += 1
                    lines.append(f"- Audio {n_aud}: {r.name} (audio cannot be attached - title and description only){tail}")
                else:
                    n_vid += 1
                    lines.append(f"- Video {n_vid}: {r.name} (cannot be attached - filename only){tail}")
            if described or n_aud:
                lines.append("Use each reference's title and description as the source of truth about its content.")
            if not can_see and n_img:
                warnings.append("warn_no_vision")
            if n_vid:
                warnings.append("warn_video_names")
            if n_aud:
                warnings.append("warn_audio_names")
            lines.append("")
        lines.append("USER REQUEST:")
        lines.append(user_request.strip())
        extra = []
        if opts.aspect_ratio:
            extra.append(f"TARGET ASPECT RATIO: {opts.aspect_ratio}")
        if opts.h3_length:
            from app.core.h3 import length_instruction
            extra.append(length_instruction(opts.h3_length))
        if CREATIVITY.get(opts.creativity):
            extra.append(CREATIVITY[opts.creativity])
        if extra:
            lines += ["", *extra]
        if current_prompt:
            if previous_edits and opts.include_history:
                lines += ["", "PREVIOUS EDITS ALREADY APPLIED (keep them unless told otherwise):"]
                lines += [f"- {e}" for e in previous_edits if e]
            lines += ["", "CURRENT PROMPT:", current_prompt.strip()]
        if edit_request:
            lines += ["", "EDIT REQUEST:", edit_request.strip(),
                      "",
                      "Apply the edit request to the current prompt and return the full updated prompt in the same format. "
                      "Change only what the edit requires."]
        text = "\n".join(lines)

        if images:
            content: list[dict] | str = [{"type": "text", "text": text}]
            for r in images:
                content.append({"type": "image_url", "image_url": {"url": ReferenceManager.image_data_url(r)}})
        else:
            content = text
        return BuiltContext(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            has_images=bool(images), warnings=warnings,
        )

    @staticmethod
    def without_images(ctx: BuiltContext) -> BuiltContext:
        """Копия контекста без картинок — для повтора, если API не принимает vision."""
        msgs = []
        for m in ctx.messages:
            c = m["content"]
            if isinstance(c, list):
                c = "\n".join(p["text"] for p in c if p.get("type") == "text")
            msgs.append({"role": m["role"], "content": c})
        return BuiltContext(msgs, False, ctx.warnings + ["warn_no_vision"])

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _system(model: Model, styles: list[Style]) -> tuple[str, str]:
        prompt, warn = resolve_system_text(model.system_prompt, model.system_prompt_file)
        parts = [prompt, "", "=== MODEL RULES ===",
                 *[f"- {r}" for r in (model.rules or GENERIC_RULES)]]
        if styles:
            parts += ["", "=== ACTIVE STYLES ===",
                      "Apply all of these styles together (listed by priority, highest first). "
                      "Style instructions may be written in another language; follow them, "
                      "but the output language is defined by the system prompt above."]
            for s in sorted(styles, key=lambda x: x.priority, reverse=True):
                head = f"[{s.name}] (priority {s.priority})" + (f" - {s.description}" if s.description else "")
                parts.append(head)
                parts += [f"  * {i}" for i in s.instructions]
        return "\n".join(parts), warn
