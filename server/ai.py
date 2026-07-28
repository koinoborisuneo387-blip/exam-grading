# -*- coding: utf-8 -*-
"""AI 辅助批改：调 OpenAI 兼容的 /chat/completions 接口。

用标准库 urllib（requests 装不了）。换 DeepSeek / 通义 / 智谱 / Kimi 只改设置页的
base_url + model，不用改代码。

**铁律：这里返回的分永远只是建议。老师不点确认，一个字都不许写进 score.score。**
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request

from . import config
from .grading import AI_STYLE, round_score, to_float


class AIError(Exception):
    """message 是直接给老师看的大白话。"""


SYSTEM_PROMPT = (
    "你是一位经验丰富的中学阅卷老师，正在协助批改主观题。"
    "你只提供建议分和评语草稿，最终分数由老师决定。\n"
    "最重要的判分原则：**学生作答不需要和参考答案字面一致，只要意思相近、"
    "要点表达到位，就应该给分。**"
    "换了说法、调了语序、用了同义词、举了等价的例子、写得更简略但意思对，"
    "这些都要正常给分，不许因为「和参考答案不一样」而扣分。"
    "只有当要点确实缺失、说错、或者答非所问时才扣分。\n"
    "同样不要因为字迹、篇幅长短、错别字等无关因素加减分。"
    "只输出一个 JSON 对象，不要输出任何其它文字、不要用代码块包裹。"
)

USER_TEMPLATE = """请批改下面这道{style}。

【满分】{max_score} 分

【题目】
{stem}

【参考答案】
{answer_key}

【评分要点】
{rubric}

【学生作答】
{answer}

判分要求：逐条比对「参考答案／评分要点」里的每个要点，看学生有没有把这个意思表达出来。
**意思相近即得分，不要求用词一致。** 请在 reasons 里写清楚哪些要点拿到了、哪些没答到。

请返回 JSON，字段如下：
{{
  "score": 建议分数（0 到 {max_score} 之间的数字，可以是小数）,
  "comment": "给学生看的评语，30-80 字，先肯定答对的部分再指出缺的要点，语气平和",
  "reasons": ["要点1：答到了/没答到，简要说明", "要点2：……"]
}}"""


MAX_IMAGES = 12
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 单张图 base64 后的上限

_DATA_URL_RE = re.compile(r"^data:image/(png|jpe?g|webp);base64,[A-Za-z0-9+/=\s]+$")


def is_ready() -> bool:
    ai = config.load_config()["ai"]
    return bool(ai.get("enabled") and ai.get("base_url") and ai.get("api_key")
                and ai.get("model"))


def is_vision() -> bool:
    return bool(config.load_config()["ai"].get("vision"))


def _clean_images(images) -> list:
    """只收前端 Canvas 压好的 dataURL，挡掉乱七八糟的输入。"""
    out = []
    for item in (images or []):
        s = str(item or "").strip()
        if not _DATA_URL_RE.match(s):
            continue
        if len(s) > MAX_IMAGE_BYTES:
            continue
        out.append(s)
        if len(out) >= MAX_IMAGES:
            break
    return out


def _user_content(text: str, images) -> object:
    """没有图就发纯文本（老模型只认这个），有图才发 content 数组。"""
    images = _clean_images(images)
    if not images:
        return text
    parts = [{"type": "text", "text": text}]
    for url in images:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _extract_json(text: str) -> dict:
    """模型经常把 JSON 包在 ```json 里，或者前后带一句废话，都得容错。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    # 退而求其次：抓第一个成对的大括号
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise AIError("AI 返回的内容看不懂，没法自动填分。原文：%s" % text[:200])


def _post(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", "Bearer " + api_key)
    req.add_header("Accept", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code == 401:
            raise AIError("API 密钥不对，请到「设置」页检查密钥是不是填错或过期了。")
        if exc.code == 402:
            raise AIError("AI 服务提示余额不足，请去服务商那边充值。")
        if exc.code == 404:
            raise AIError("接口地址不对（404）。请检查「设置」页的接口地址，"
                          "大多数服务要以 /v1 结尾。")
        if exc.code == 429:
            raise AIError("调用太频繁了，等几秒再点一次。")
        raise AIError("AI 服务返回错误 %s。%s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise AIError("连不上 AI 服务（%s）。可能是没联网，或者这台电脑的网络"
                      "不让访问外部网站。先手动打分就行，不影响其它功能。"
                      % getattr(exc, "reason", exc))
    except (OSError, ssl.SSLError) as exc:
        raise AIError("网络出错：%s。先手动打分就行。" % exc)

    try:
        return json.loads(raw)
    except ValueError:
        raise AIError("AI 服务返回的不是 JSON：%s" % raw[:200])


def _require_config() -> dict:
    ai = config.load_config()["ai"]
    if not ai.get("enabled"):
        raise AIError("AI 辅助批改还没打开。到「设置」页打开开关并填好接口信息。")
    if not (ai.get("base_url") and ai.get("api_key") and ai.get("model")):
        raise AIError("AI 接口还没配置完整。到「设置」页填接口地址、密钥、模型名。")
    return ai


def suggest(question: dict, student_answer: str, images=None, key_images=None) -> dict:
    """让 AI 出一个建议分 + 评语草稿。

    images / key_images 是前端 Canvas 压缩好的 dataURL（学生答卷页、标准答案页）。
    只有配置里勾了「模型能看图」才会真的发出去。

    返回 {"score": float|None, "comment": str, "reasons": [str], "model": str}
    出错一律抛 AIError，message 可以直接显示给老师。
    """
    ai = _require_config()
    vision = bool(ai.get("vision"))
    pics = _clean_images(images) if vision else []
    key_pics = _clean_images(key_images) if vision else []

    answer = (student_answer or "").strip()
    if not answer and not pics:
        if vision:
            raise AIError("这道题既没有答卷图片，也没有录入文字作答，AI 没有可批的内容。")
        raise AIError("当前用的模型看不了卷子图片。请把学生写的内容打进（或粘贴进）"
                      "输入框，AI 才能批。\n"
                      "想让 AI 直接看着卷子批，到「设置」页换成通义千问 qwen-vl-max "
                      "或智谱 GLM-5V 这类能看图的模型。")

    max_score = to_float(question.get("max_score"), 0)
    stem = (question.get("stem") or "").strip() or "（老师没有录入题干）"
    answer_key = (question.get("answer_key") or "").strip() or "（老师没有录入参考答案）"
    rubric = (question.get("rubric") or "").strip() or "（老师没有录入评分要点，请按参考答案酌情给分）"
    style = AI_STYLE.get(question.get("qtype"), "主观题")

    text = USER_TEMPLATE.format(
        style=style, max_score=("%g" % max_score), stem=stem,
        answer_key=answer_key, rubric=rubric,
        answer=answer or "（见后面的答卷图片，请自己从图里读学生的作答）")
    if key_pics:
        text += ("\n\n随附图片里，前 %d 张是【标准答案卷】，其余是【学生答卷】。"
                 % len(key_pics))
    elif pics:
        text += "\n\n随附图片是【学生答卷】。"
    if pics:
        text += ("\n请只批「%s」这一题，卷子上的其它题不用管。"
                 % (question.get("no_label") or "本"))

    payload = {
        "model": ai["model"],
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(text, key_pics + pics)},
        ],
    }
    url = ai["base_url"].rstrip("/") + "/chat/completions"
    data = _post(url, payload, ai["api_key"], int(ai.get("timeout") or 90))

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("AI 服务的返回格式不认识，可能这个接口地址不是 OpenAI 兼容的。")

    obj = _extract_json(content)
    score = obj.get("score")
    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None
    if score is not None:
        if score < 0:
            score = 0.0
        if max_score and score > max_score:
            score = max_score
        score = round_score(score)

    reasons = obj.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(x) for x in reasons if str(x).strip()][:8]

    return {
        "score": score,
        "comment": str(obj.get("comment") or "").strip(),
        "reasons": reasons,
        "model": ai["model"],
    }


WHOLE_TEMPLATE = """请一次批改这份卷子上的所有主观题。

随附图片：{picture_note}

各题信息如下（JSON）：
{questions}

判分要求：
1. 从【学生答卷】图片里找到每道题的作答，逐条对照该题的参考答案／评分要点。
2. **意思相近即得分**，换说法、调语序、用同义词、写得更简略但意思对，都要正常给分。
   只有要点确实缺失、说错、答非所问才扣分。
3. 学生整题空着没写，就给 0 分，并在 reasons 里注明「未作答」。
4. 卷面看不清、找不到这道题的作答，score 填 null，并在 reasons 里说明看不清。**不要瞎猜分数。**

请返回 JSON：
{{
  "items": [
    {{"question_id": 题目的 id（用上面给的数字，不要改）,
      "score": 建议分数或 null,
      "comment": "给学生看的评语，30-80 字",
      "reasons": ["要点1：答到了/没答到，简要说明"]}}
  ]
}}"""


def grade_paper(questions, student_images, key_images=None) -> dict:
    """整卷预批：一次调用批完这份卷子上所有主观题。

    只有能看图的模型才支持（纯文本模型没法从图里读作答）。
    返回 {"items": [{question_id, score, comment, reasons}], "model": str}
    """
    ai = _require_config()
    if not ai.get("vision"):
        raise AIError("当前用的模型看不了卷子图片，没法整卷预批。\n"
                      "到「设置」页换成智谱 GLM-5V 或通义千问 qwen-vl-max 这类能看图的模型，"
                      "或者逐题手动录入学生作答后再用 AI。")
    pics = _clean_images(student_images)
    if not pics:
        raise AIError("这个学生还没有导入答卷图片，没法整卷预批。")
    key_pics = _clean_images(key_images)

    brief = []
    for q in questions:
        brief.append({
            "question_id": q.get("id"),
            "题号": q.get("no_label") or "",
            "题型": AI_STYLE.get(q.get("qtype"), "主观题"),
            "满分": to_float(q.get("max_score"), 0),
            "题干": (q.get("stem") or "").strip(),
            "参考答案": (q.get("answer_key") or "").strip(),
            "评分要点": (q.get("rubric") or "").strip(),
        })
    if not brief:
        raise AIError("这场考试还没有登记题目，先去「题卡」页把要批的主观题加上。")

    if key_pics:
        picture_note = ("前 %d 张是【标准答案卷】，后 %d 张是【学生答卷】。"
                        % (len(key_pics), len(pics)))
    else:
        picture_note = "共 %d 张，全部是【学生答卷】。" % len(pics)

    text = WHOLE_TEMPLATE.format(
        picture_note=picture_note,
        questions=json.dumps(brief, ensure_ascii=False, indent=1),
    )
    payload = {
        "model": ai["model"],
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(text, key_pics + pics)},
        ],
    }
    url = ai["base_url"].rstrip("/") + "/chat/completions"
    data = _post(url, payload, ai["api_key"], int(ai.get("timeout") or 90))
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("AI 服务的返回格式不认识，可能这个接口地址不是 OpenAI 兼容的。")

    obj = _extract_json(content)
    raw_items = obj.get("items")
    if not isinstance(raw_items, list):
        raise AIError("AI 没有按要求返回逐题结果，请重试一次。")

    by_id = {q.get("id"): q for q in questions}
    items = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        qid = it.get("question_id")
        try:
            qid = int(qid)
        except (TypeError, ValueError):
            continue
        q = by_id.get(qid)
        if not q:
            continue
        score = it.get("score")
        if score is not None:
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = None
        if score is not None:
            full = to_float(q.get("max_score"), 0)
            if score < 0:
                score = 0.0
            if full and score > full:
                score = full
            score = round_score(score)
        reasons = it.get("reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        items.append({
            "question_id": qid,
            "score": score,
            "comment": str(it.get("comment") or "").strip(),
            "reasons": [str(x) for x in reasons if str(x).strip()][:8],
        })
    if not items:
        raise AIError("AI 返回的结果里没有对得上的题目，请重试一次。")
    return {"items": items, "model": ai["model"]}


IDENTITY_SYSTEM = (
    "你是一个只做文字抄录的助手，正在处理学生答卷的扫描件。"
    "你的唯一任务是把卷面表头上写着的学生姓名和学号**原样抄下来**。\n"
    "铁律：**只抄卷子上确实写着的字，绝对不许猜测、不许编造、不许补全。**"
    "字迹潦草看不清、或者这一页压根没有姓名栏，就老老实实返回 null —— "
    "写错名字会导致分数记到别的同学头上，比返回 null 严重得多。\n"
    "只输出一个 JSON 对象，不要输出任何其它文字、不要用代码块包裹。"
)

IDENTITY_TEMPLATE = """下面按顺序给你 {count} 页学生答卷的扫描图，编号 1 到 {count}。

请逐页读出表头上的**学生姓名**和**学号**。

注意：
1. 只抄卷面上写着的内容。看不清、没写、或这一页没有姓名栏，对应字段填 null。
2. 「姓名」「学号」这类标签不要抄进去，只要后面的内容。
3. has_header 表示这一页**有没有填写姓名的表头**——
   一份答卷的第一页通常有，第二页往后通常没有。用它来判断哪几页属于同一个学生。
4. 不确定就填 null。**宁可空着让老师补，也不要写一个可能错的名字。**

返回 JSON：
{{
  "pages": [
    {{"index": 1, "name": "读到的姓名或 null", "student_no": "读到的学号或 null",
      "has_header": true}}
  ]
}}"""


def read_identity(images) -> dict:
    """从答卷图上读出每一页的学生姓名和学号。

    **返回的只是草稿**，必须经老师在「确认姓名」界面点头才能落库 ——
    认错名字会把分数记到别人头上，见 SPEC 5.4c。

    返回 {"pages": [{"index", "name", "student_no", "has_header"}], "model": str}
    """
    ai = _require_config()
    if not ai.get("vision"):
        raise AIError("当前用的模型看不了卷子图，没法从卷面读姓名。\n"
                      "到「设置」页换成智谱 GLM-5V 这类能看图的模型，"
                      "或者手动填写学生名单。")
    pics = _clean_images(images)
    if not pics:
        raise AIError("没有可以识别的答卷图片。")

    text = IDENTITY_TEMPLATE.format(count=len(pics))
    payload = {
        "model": ai["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": IDENTITY_SYSTEM},
            {"role": "user", "content": _user_content(text, pics)},
        ],
    }
    url = ai["base_url"].rstrip("/") + "/chat/completions"
    data = _post(url, payload, ai["api_key"], int(ai.get("timeout") or 90))
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError("AI 服务的返回格式不认识，可能这个接口地址不是 OpenAI 兼容的。")

    obj = _extract_json(content)
    raw = obj.get("pages")
    if not isinstance(raw, list):
        raise AIError("AI 没有按要求返回逐页结果，请重试一次。")

    by_index = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= len(pics)):
            continue
        by_index[idx] = {
            "index": idx,
            "name": _clean_field(item.get("name")),
            "student_no": _clean_field(item.get("student_no")),
            "has_header": bool(item.get("has_header", True)),
        }
    # 模型漏了哪页就补一个空的，绝不让页面凭空消失
    pages = [by_index.get(i, {"index": i, "name": "", "student_no": "",
                              "has_header": i == 1})
             for i in range(1, len(pics) + 1)]
    return {"pages": pages, "model": ai["model"]}


def _clean_field(value) -> str:
    """模型可能返回 None、字符串 "null"、或者带标签的文字，统一成干净字符串。"""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("null", "none", "n/a", "na", "-", "—", "无", "未知", "看不清"):
        return ""
    return s[:40]


def test_connection() -> dict:
    """设置页的「测试连接」按钮。发一个很短的请求，验证地址/密钥/模型三件事。

    **不要在这里设 max_tokens。** glm-5v-turbo 这类思考型模型会先花一百多个 token 推理，
    额度设小了 content 直接是空字符串，界面上就变成「连上了但没回话」，让人以为坏了。
    """
    ai = config.load_config()["ai"]
    if not (ai.get("base_url") and ai.get("api_key") and ai.get("model")):
        raise AIError("请先把接口地址、密钥、模型名都填上。")
    payload = {
        "model": ai["model"],
        "temperature": 0,
        "messages": [{"role": "user", "content": "回复两个字：正常"}],
    }
    url = ai["base_url"].rstrip("/") + "/chat/completions"
    data = _post(url, payload, ai["api_key"], min(120, int(ai.get("timeout") or 90)))
    try:
        message = data["choices"][0]["message"]
        reply = message.get("content") or ""
        if not str(reply).strip():
            # 思考型模型偶尔把话全说在推理里，拿它兜个底，至少证明接口是通的
            reply = str(message.get("reasoning_content") or "").strip()[:50] or "（无文字回复）"
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError("接口通了，但返回格式不是 OpenAI 兼容的，没法用来批改。")
    return {"ok": True, "reply": str(reply).strip()[:50], "model": ai["model"]}
