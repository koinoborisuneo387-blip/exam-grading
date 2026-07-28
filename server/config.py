# -*- coding: utf-8 -*-
"""路径、版本号、本地配置（AI 接口设置）。

本地配置存 data/config.json，里面有 API key，该文件永不进 git。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 数据目录允许用环境变量挪走（本地跑测试时用），默认就是 repo 下的 data/
DATA_DIR = Path(os.environ.get("PJPG_DATA_DIR") or (ROOT / "data"))

DEFAULT_PORT = int(os.environ.get("PJPG_PORT") or 8899)

_lock = threading.Lock()

# 默认用智谱 GLM-4V（2026-07-28 用户指定）：国内直连、OpenAI 兼容、**能看卷子图**。
# vision=True 表示这个模型能读图片，可以直接对着扫描的答卷批改。
# 纯文本模型（比如 DeepSeek 的 deepseek-chat）看不了图，只能批老师粘贴进去的文字。
DEFAULT_AI = {
    "enabled": False,
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4v-plus",
    "vision": True,
    "timeout": 90,
}

# 设置页的「一键填入」预设。老师不用记这些地址。
# 模型名以各家控制台上实际列出的为准，这里只是常用值。
AI_PRESETS = [
    {
        "key": "glm-4v",
        "label": "智谱 GLM-4V（能看卷子图，当前使用）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-plus",
        "vision": True,
        "apply_url": "https://open.bigmodel.cn",
        "models": ["glm-4v-plus", "glm-4v", "glm-4v-flash"],
        "note": "在智谱开放平台申请 API Key。glm-4v-flash 更便宜，卷面清晰时够用。",
    },
    {
        "key": "qwen-vl",
        "label": "通义千问 qwen-vl-max（能看卷子图，备选）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max",
        "vision": True,
        "apply_url": "https://bailian.console.aliyun.com",
        "models": ["qwen-vl-max", "qwen-vl-plus"],
        "note": "阿里云百炼平台申请。GLM-4V 连不上时可以换这个试试。",
    },
    {
        "key": "deepseek",
        "label": "DeepSeek（便宜，但看不了卷子图）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "vision": False,
        "apply_url": "https://platform.deepseek.com",
        "models": ["deepseek-chat"],
        "note": "纯文字模型。选它的话，每道题要老师把学生写的内容打进输入框，AI 才能批。",
    },
]


def data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def uploads_dir() -> Path:
    p = data_dir() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "批改.db"


def config_path() -> Path:
    return data_dir() / "config.json"


# 老师只需要把智谱的 API Key 粘进这个文件、保存，AI 批改就能用了。
# 放在 data/ 下面是因为更新脚本永远不碰 data/，换版本不会把 key 弄丢。
# 为了好找，程序根目录下的同名文件也认（但更新时要自己留意别被覆盖）。
KEY_FILE_NAME = "API_KEY.txt"

KEY_FILE_TEMPLATE = """# 智谱 GLM-4V 的 API Key 放在这个文件里
#
# 用法：把从 https://open.bigmodel.cn 申请到的 API Key，
#       整行粘到下面这一行的位置，保存，然后重启程序（或者在网页里刷新一下）。
#       这个文件里以 # 开头的行都是说明，程序不看。
#
# 粘完大概长这样（下面那行不要带 # 号）：
# 1a2b3c4d5e6f7g8h9i0j.KLMNOPQRSTUVWXYZ
#
# 填好之后，网页右上角「AI 设置」里会显示「已读到密钥」。
# 不填也没关系，除了 AI 批改，其它功能都能正常用。

"""


def key_file() -> Path:
    """密钥文件的正式位置。"""
    return data_dir() / KEY_FILE_NAME


def _read_key_file(path: Path) -> str:
    """取第一行不是注释的非空内容当密钥。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def key_from_file():
    """返回 (密钥, 来源文件 Path)。两个位置都没有就返回 ("", None)。"""
    for path in (data_dir() / KEY_FILE_NAME, ROOT / KEY_FILE_NAME):
        if path.exists():
            key = _read_key_file(path)
            if key:
                return key, path
    return "", None


def ensure_key_file() -> Path:
    """没有密钥文件就建一个带说明的空模板，让老师知道该往哪儿粘。"""
    path = key_file()
    if not path.exists():
        try:
            path.write_text(KEY_FILE_TEMPLATE, encoding="utf-8")
        except OSError:
            pass
    return path


def version() -> str:
    f = ROOT / "VERSION"
    try:
        return f.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def load_config() -> dict:
    """读本地配置。文件不存在或坏了都返回默认值，不抛异常。

    密钥的来源有两个，**API_KEY.txt 优先** —— 老师只要往那个文件里粘 key 就够了，
    不用再进设置页点开关：读到 key 就自动把 AI 打开。
    """
    cfg = {"ai": dict(DEFAULT_AI)}
    explicit_enabled = None
    p = config_path()
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw.get("ai"), dict):
            merged = dict(DEFAULT_AI)
            merged.update(raw["ai"])
            cfg["ai"] = merged
            if "enabled" in raw["ai"]:
                explicit_enabled = bool(raw["ai"]["enabled"])

    file_key, source = key_from_file()
    cfg["key_source"] = str(source) if source else ""
    if file_key:
        cfg["ai"]["api_key"] = file_key
        # 粘了 key 就默认开着，除非老师在设置页明确关掉过
        if explicit_enabled is None:
            cfg["ai"]["enabled"] = True
        else:
            cfg["ai"]["enabled"] = explicit_enabled
    return cfg


def save_config(cfg: dict) -> dict:
    """写本地配置。只接受已知字段，避免把前端乱传的东西存进去。"""
    with _lock:
        current = load_config()
        file_key, _ = key_from_file()
        incoming = cfg.get("ai") or {}
        ai = current["ai"]
        for key in ("enabled", "base_url", "api_key", "model", "vision", "timeout"):
            if key in incoming:
                ai[key] = incoming[key]
        ai["enabled"] = bool(ai.get("enabled"))
        ai["vision"] = bool(ai.get("vision"))
        ai["base_url"] = str(ai.get("base_url") or "").strip().rstrip("/")
        ai["api_key"] = str(ai.get("api_key") or "").strip()
        ai["model"] = str(ai.get("model") or "").strip()
        try:
            ai["timeout"] = max(5, min(600, int(ai.get("timeout") or 90)))
        except (TypeError, ValueError):
            ai["timeout"] = 90
        # 密钥来自 API_KEY.txt 时，不要再往 config.json 里抄一份 —— 一个密钥只存一个地方
        persist = dict(ai)
        if file_key and persist.get("api_key") == file_key:
            persist["api_key"] = ""
        config_path().write_text(
            json.dumps({"ai": persist}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ai": ai}


def public_config() -> dict:
    """给前端看的配置：API key 只回传"有没有配"，不回传明文。"""
    cfg = load_config()
    ai = cfg["ai"]
    return {
        "ai": {
            "enabled": ai["enabled"],
            "base_url": ai["base_url"],
            "model": ai["model"],
            "vision": ai["vision"],
            "timeout": ai["timeout"],
            "has_key": bool(ai["api_key"]),
        },
        "key_file": str(key_file()),
        "key_source": cfg.get("key_source", ""),
        "presets": AI_PRESETS,
    }
