"""内置提示词：翻译、英文语法检查、中英文一致性检查。

提示词允许用户在“模型与提示词”中编辑并恢复默认；不保证与原外部工具一致。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

PROMPT_NAMES = ("translate", "grammar", "consistency")

PROMPT_LABELS = {
    "translate": "中文→英文翻译",
    "grammar": "英文语法检查",
    "consistency": "中英文一致性检查",
}

DEFAULT_PROMPTS: dict[str, dict[str, str]] = {
    "translate": {
        "base_version": "translate-v1",
        "system": (
            "你是电商图片指令翻译专家。把中文审图指令翻译成自然、简洁、"
            "符合英文电商审图习惯的英文指令。保留数字、编号、专有名词与格式；"
            "不要增加原文没有的要求；不要解释。"
        ),
        "user": (
            "把下面的中文指令翻译成英文。只输出英文译文，不要引号、不要"
            "“Translation:”、不要代码块。\n\n中文指令：\n{zh_text}"
        ),
    },
    "grammar": {
        "base_version": "grammar-v1",
        "system": (
            "你是英文校对员。检查电商图片指令的拼写、语法、标点与表达是否自然。"
            "只指出客观问题，不改变原意。"
        ),
        "user": (
            "检查下面的英文指令。只输出 JSON，不要代码块、不要额外说明：\n"
            '{"passed": true, "issues": [], "suggestion": ""}\n\n'
            "字段说明：passed 表示是否无需修改；issues 是问题列表（中文简述）；"
            "suggestion 是修改后的完整英文，若无问题留空。\n\n英文指令：\n{en_text}"
        ),
    },
    "consistency": {
        "base_version": "consistency-v1",
        "system": (
            "你是中英文对照审校员。判断英文是否完整、准确地表达中文指令，"
            "不评价图片内容。"
        ),
        "user": (
            "对比中文与英文指令，检查是否缺漏、错译或增加了原文没有的要求。"
            "只输出 JSON，不要代码块、不要额外说明：\n"
            '{"passed": true, "issues": [], "suggestion": ""}\n\n'
            "字段说明：passed 表示是否一致；issues 是差异列表（中文简述）；"
            "suggestion 是修正后的完整英文，若无需修改留空。\n\n"
            "中文指令：\n{zh_text}\n\n英文指令：\n{en_text}"
        ),
    },
}


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    system: str
    user: str
    base_version: str = ""
    source: str = "builtin"  # builtin / custom

    @property
    def version(self) -> str:
        return prompt_version(self.base_version or self.name, self.system, self.user)

    @property
    def label(self) -> str:
        return PROMPT_LABELS.get(self.name, self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "system": self.system,
            "user": self.user,
            "base_version": self.base_version,
            "version": self.version,
            "source": self.source,
        }


def prompt_version(base: str, system: str, user: str) -> str:
    digest = hashlib.sha256(f"{base}\n{system}\n{user}".encode("utf-8")).hexdigest()
    return f"{base}-{digest[:10]}"


def default_prompt(name: str) -> PromptTemplate:
    if name not in DEFAULT_PROMPTS:
        raise KeyError(f"未知提示词：{name}")
    data = DEFAULT_PROMPTS[name]
    return PromptTemplate(
        name=name,
        system=data["system"],
        user=data["user"],
        base_version=data["base_version"],
        source="builtin",
    )


def render(template: str, values: dict[str, str]) -> str:
    """安全替换占位符；提示词中的 JSON 花括号不会被当作格式化语法。"""
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def build_messages(
    prompt: PromptTemplate, values: dict[str, str]
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": render(prompt.system, values)},
        {"role": "user", "content": render(prompt.user, values)},
    ]


def all_prompts() -> list[PromptTemplate]:
    return [default_prompt(name) for name in PROMPT_NAMES]
