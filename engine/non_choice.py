# _*_ coding:utf-8 _*_
"""
非选择题答题模块（填空题 completion / 简答题 shortanswer）

针对原系统"只处理单选、多选、判断，填空题被跳过或只取第一空"的缺陷，
本模块提供完整的非选择题处理链路：

1. 题目解析：识别题干中的空缺（连续下划线、空括号、空方头括号等），统计空数；
2. 答案生成：按「在线题库 -> AI 大模型 -> 兜底文本」的优先级逐题生成答案；
3. 格式适配：兼容题库/AI 的多种返回形式（列表、JSON、# 或分号或换行分隔），
   规范化为超星 addStudentWorkNew 接口要求的单字段多空形式（各空之间以 "\\n" 分隔）。

该模块不依赖网络与 Qt，可被 Work（JSON API 式作业）与 Quiz（HTML 页面式测验）共同复用。
"""
import json
import re

try:
    from loguru import logger
except Exception:  # loguru 缺失时退化为空操作，保证模块可独立导入
    class _DummyLogger:
        def __getattr__(self, _name):
            def _noop(*_args, **_kwargs):
                pass
            return _noop

    logger = _DummyLogger()

# 无任何答案来源时的兜底占位文本（与 Work._random_answer 保持一致）
FALLBACK_TEXT = "暂无答案"

# 题型代码 -> 名称（与超星接口约定一致；5-8 为主观题扩展码，8 在部分课程中为"资料/其他"主观题）
TYPE_MAP = {
    "2": "completion",
    "4": "shortanswer",
    "5": "term_explanation",
    "6": "essay",
    "7": "calculation",
    "8": "case_analysis",
}

# 非选择题全集（客观填空 + 主观题），Work/Quiz 据此分流
NON_CHOICE_TYPES = set(TYPE_MAP.values())
# 主观题子集（AI 模板化作答）
SUBJECTIVE_TYPES = NON_CHOICE_TYPES - {"completion"}
# 主观题题型码 -> 类型名（Quiz 页面解析用）
SUBJECTIVE_TYPE_MAP = {k: v for k, v in TYPE_MAP.items() if k != "2"}
# 类型名 -> 中文标签
TYPE_LABELS = {
    "completion": "填空题",
    "shortanswer": "简答题",
    "term_explanation": "名词解释",
    "essay": "论述题",
    "calculation": "计算题",
    "case_analysis": "案例分析题",
}

# 一个空的答案片段上允许的"弱分隔符"，按优先级尝试
# 说明：换行 / # / 分号是多空答案最常见的分隔方式，顿号和逗号仅在与预期空数吻合时才使用
_WEAK_SEPARATORS = (";", "；", "|", "/", "、", ",", "，")


# ---------------------------------------------------------------------------
# 一、题目解析：空缺识别
# ---------------------------------------------------------------------------
def detect_blank_count(title: str) -> int:
    """识别题干中的空缺数量。

    支持的空缺写法：
      - 连续下划线：``______``
      - 空括号（全角/半角）：``（  ）``、``(   )``
      - 空方头括号/空方括号：``【  】``、``[   ]``

    无法识别时按 1 个空处理。
    """
    if not title:
        return 1

    count = 0
    # 连续下划线（2 个及以上）每段算一个空
    count += len(re.findall(r"_{2,}", title))
    # 全角/半角空圆括号
    count += len(re.findall(r"（\s*）|\(\s*\)", title))
    # 空方头括号（【】）与空半角方括号（[]）
    count += len(re.findall(r"【\s*】|\[\s*\]", title))

    return max(1, count)


# ---------------------------------------------------------------------------
# 二、答案片段清洗与拆分
# ---------------------------------------------------------------------------
def _clean_part(part) -> str:
    """清洗单个空的答案文本。"""
    text = str(part).strip()
    # 去掉模型常带的前缀
    text = re.sub(r"^答案\s*[：:]\s*", "", text)
    # 去掉首尾引号 / 书名号式包裹
    text = text.strip("\"'“”‘’")
    # 去掉残留的 markdown 粗体标记
    text = re.sub(r"^\*\*(.*?)\*\*$", r"\1", text)
    # 去掉序号：① 或 "1. "（点号后须有空白，避免误伤 "1.5亿" 这类合法答案）
    text = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", text)
    text = re.sub(r"^\d+\s*[.、)]\s+", "", text)
    return text.strip()


def _extract_json_answer(text: str):
    """尝试从文本中提取 {"Answer": [...]} 结构，失败返回 None。"""
    if not text or "Answer" not in text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("Answer"), list):
        return obj["Answer"]
    return None


def _split_by_weak_separator(text: str, expected: int):
    """当只有一个片段但预期有多空时，尝试用弱分隔符切分。

    优先返回切分后数量恰好等于预期的方案；否则选择数量最接近预期的方案。
    """
    best = None
    for sep in _WEAK_SEPARATORS:
        if sep not in text:
            continue
        pieces = [p.strip() for p in text.split(sep) if p.strip()]
        if len(pieces) == expected:
            return pieces
        if best is None or abs(len(pieces) - expected) < abs(len(best) - expected):
            best = pieces
    return best


def split_blank_answers(raw, expected: int = 1):
    """将题库/AI 的原始返回拆分为逐空答案列表。

    Args:
        raw: 原始答案，可为 list / tuple / str / None。
        expected: 根据题干识别出的预期空数。

    Returns:
        清洗后的非空答案片段列表（可能为空列表）。
    """
    if raw is None:
        return []

    # 1) 列表/元组：逐项清洗（题库或 JSON 接口的标准返回）
    if isinstance(raw, (list, tuple)):
        parts = [_clean_part(p) for p in raw]
        return [p for p in parts if p]

    text = str(raw).strip()
    if not text:
        return []

    # 2) JSON 包装：{"Answer": ["空1", "空2"]}
    json_answer = _extract_json_answer(text)
    if json_answer is not None:
        parts = [_clean_part(p) for p in json_answer]
        return [p for p in parts if p]

    # 3) 单空：不再切分，直接清洗（避免把答案内部标点误当分隔符）
    if expected <= 1:
        return [_clean_part(text)] if _clean_part(text) else []

    # 4) 多空场景：先按强分隔符（换行 / #）切分
    parts = [p.strip() for p in re.split(r"[\r\n#]+", text) if p.strip()]
    if len(parts) == 1:
        weak = _split_by_weak_separator(parts[0], expected)
        if weak:
            parts = weak

    parts = [_clean_part(p) for p in parts]
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# 三、格式适配
# ---------------------------------------------------------------------------
def normalize_completion(raw, title: str = "", expected: int = 0) -> str:
    """把填空题原始答案规范化为提交格式：单字段、各空以 "\\n" 分隔。"""
    blank_count = expected or detect_blank_count(title)
    parts = split_blank_answers(raw, blank_count)
    if not parts:
        return ""

    if blank_count > 1:
        # 与空数对齐：不足补空串以保持位置对应，超出截断
        if len(parts) < blank_count:
            parts = parts + [""] * (blank_count - len(parts))
        elif len(parts) > blank_count:
            parts = parts[:blank_count]

    return "\n".join(parts)


def normalize_shortanswer(raw) -> str:
    """规范化简答题答案：保留正文内部换行，仅去掉外层包装与常见前缀。"""
    if raw is None:
        return ""

    if isinstance(raw, (list, tuple)):
        items = [str(p).strip() for p in raw if str(p).strip()]
        if not items:
            return ""
        text = "\n".join(items)
    else:
        text = str(raw).strip()
        json_answer = _extract_json_answer(text)
        if json_answer is not None:
            text = "\n".join(str(p).strip() for p in json_answer if str(p).strip())

    text = re.sub(r"^答案\s*[：:]\s*", "", text.strip())
    text = re.sub(r"^\*\*(.*?)\*\*$", r"\1", text.strip())
    return text.strip()


def _clean_text_answer(text) -> str:
    """轻量清洗整段主观题答案：去引号包裹、答案前缀、markdown 粗体。"""
    t = str(text).strip().strip("\"'“”")
    t = re.sub(r"^答案\s*[：:]\s*", "", t)
    t = re.sub(r"^\*\*(.*?)\*\*$", r"\1", t)
    return t.strip()


# ---------------------------------------------------------------------------
# 五、学科识别与主观题答题模板
# ---------------------------------------------------------------------------
# 题库参考答案低于该长度时视为要点不完整，交由 AI 参考扩充
TIKU_ANSWER_MIN_LENGTH = 30

# 学科特征关键词（按顺序匹配，命中即停）
SUBJECT_HINTS = (
    ("math", ("计算", "求解", "求证", "方程", "概率", "导数", "积分", "函数", "矩阵", "面积", "体积", "百分比")),
    ("law", ("法律", "法规", "宪法", "刑法", "民法", "合同", "侵权", "诉讼", "行政法", "物权", "法条")),
    ("medicine", ("患者", "临床", "病理", "诊断", "治疗", "用药", "护理", "症状", "手术")),
    ("econ", ("经济", "供求", "财政", "货币", "通货膨胀", "GDP", "边际", "利润率", "供给", "需求曲线")),
    ("management", ("管理", "市场营销", "战略", "组织结构", "人力资源", "绩效", "企业文化", "项目管理")),
    ("education", ("教育", "教学", "课程", "学习动机", "德育")),
    ("literature", ("文学", "诗歌", "小说", "散文", "作者", "作品", "艺术", "美学", "历史意义")),
)

# 各学科的作答补充要求（评分标准导向）
SUBJECT_ADDENDA = {
    "math": "本题为理科/计算类题目：必须给出公式、代入数据与推导步骤，数值结果保留合理精度并注明单位。",
    "law": "本题为法学类题目：引用法条或法学理论须使用规范表述（如《中华人民共和国民法典》第X条），构成要件逐项对照分析。",
    "medicine": "本题为医学类题目：使用规范医学术语，区分诊断依据与处理措施，表述严谨。",
    "econ": "本题为经济类题目：结合经济学理论模型（如供求理论、成本收益分析）分点论述，可联系实际经济现象。",
    "management": "本题为管理类题目：结合管理理论或模型（如SWOT、波特五力、马斯洛需求层次）分点论述，必要时联系企业管理实际。",
    "education": "本题为教育学类题目：结合教育学/心理学理论分点论述，理论名称与提出者表述准确。",
    "literature": "本题为文史哲类题目：论述时引用原文、史实或作品内容佐证观点，确保史实与引文准确。",
    "general": "",
}

# 各题型的作答结构模板
ANSWER_TEMPLATES = {
    "shortanswer": "先直接给出核心结论；再分3~5点展开，每点采用“关键词＋一句阐释”的形式。",
    "term_explanation": "按“定义→核心内涵或特征→意义/典型例子”三段作答：第一句给出标准定义；随后概括2~3个关键特征；最后说明其意义或举一典型例子，全文控制在100字左右。",
    "essay": "采用“总—分—总”结构：开篇亮明总观点；主体分3~5个论证点，每点按“论点＋理论依据＋结合实际的展开”组织；结尾总结升华。",
    "calculation": "严格按步骤作答：①写出所用公式；②代入已知数据；③给出完整计算过程；④明确最终结果并保留单位，中间步骤不得省略。",
    "case_analysis": "按“结论→依据→分析→总结”作答：先给出针对案例的明确结论；再引用相关理论/法条作为依据；然后结合案例材料逐点分析；最后总结。必须紧扣材料，不得脱离案情。",
}

# 评分标准通用要求（踩点给分）
SCORING_RULES = (
    "评分采用踩点给分：答案必须覆盖题目的全部得分要点；使用规范学科术语；"
    "分点编号作答（如（1）（2）（3））；每点“关键词＋简要阐释”；"
    "不输出与题目无关的内容，不使用markdown符号。"
)


def detect_subject(title: str) -> str:
    """基于题干关键词的轻量学科识别，为主观题 prompt 追加学科化要求。"""
    title = str(title or "")
    for subject, keywords in SUBJECT_HINTS:
        if any(kw in title for kw in keywords):
            return subject
    return "general"


def build_subjective_system_prompt(q_type: str, title: str = "") -> str:
    """构建主观题系统提示词：题型标签 + 评分规则 + 结构模板 + 学科补充要求。"""
    subject = detect_subject(title)
    label = TYPE_LABELS.get(q_type, "简答题")
    template = ANSWER_TEMPLATES.get(q_type, ANSWER_TEMPLATES["shortanswer"])
    parts = [f"本题为{label}。", SCORING_RULES, f"作答结构要求：{template}"]
    addenda = SUBJECT_ADDENDA.get(subject, "")
    if addenda:
        parts.append(addenda)
    parts.append(
        '最终以JSON输出：{"Answer": ["完整答案正文"]}，正文内用（1）（2）（3）分点；'
        "除JSON外不要输出任何其他内容，也不要使用MD语法。"
    )
    return "\n".join(parts)


def parse_answer_response(response) -> list:
    """解析内容接口原始返回：JSON Answer 包装优先，纯文本兜底。"""
    if not response:
        return []
    text = str(response).strip()
    # 去掉 markdown 代码块包装
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if match:
        text = match.group(1).strip()
    # JSON {"Answer": [...]}
    json_answer = _extract_json_answer(text)
    if json_answer is not None:
        parts = [p for p in (_clean_part(x) for x in json_answer) if p]
        if parts:
            return parts
    cleaned = _clean_text_answer(text)
    return [cleaned] if cleaned else []


# ---------------------------------------------------------------------------
# 六、答案生成：题库 -> AI -> 兜底
# ---------------------------------------------------------------------------
def _ask_ai(ai_client, payload):
    """调用 AI 客户端答题（内容接口优先）。

    减少大模型调用量：已有内容接口时只调用内容接口一次，
    未命中直接交给随机兜底，不再重复请求通用接口。
    """
    if ai_client is None:
        return None

    if hasattr(ai_client, "answer_question_content"):
        try:
            return ai_client.answer_question_content(payload)
        except Exception as exc:
            logger.warning(f"AI 内容接口调用失败: {exc}")
            return None

    if hasattr(ai_client, "answer_question"):
        try:
            return ai_client.answer_question(payload)
        except Exception as exc:
            logger.warning(f"AI 通用接口调用失败: {exc}")

    return None


def _query_tiku(tiku, title, q_type):
    """查询在线题库，任何异常都视为未命中。"""
    if tiku is None or getattr(tiku, "DISABLE", False):
        return None
    try:
        return tiku.query({"title": title, "type": q_type, "options": ""})
    except Exception as exc:
        logger.warning(f"题库查询异常: {exc}")
        return None


def answer_completion(title: str, ai_client=None, tiku=None, use_ai: bool = True, tiku_answer=None):
    """生成填空题答案。

    Args:
        tiku_answer: 调用方已取得的题库结果（避免对题库重复请求）。

    Returns:
        (规范化答案, 来源)；来源为 "tiku" / "ai" / "random"。
    """
    blank_count = detect_blank_count(title)

    tiku_raw = tiku_answer if tiku_answer is not None else _query_tiku(tiku, title, "completion")
    if tiku_raw:
        answer = normalize_completion(tiku_raw, title, blank_count)
        if answer:
            return answer, "tiku"

    if use_ai:
        ai_raw = _ask_ai(ai_client, {
            "question_type": "填空题",
            "title": title,
            "options": [],
            "blank_count": blank_count,
        })
        if ai_raw:
            answer = normalize_completion(ai_raw, title, blank_count)
            if answer and answer != FALLBACK_TEXT:
                return answer, "ai"

    return FALLBACK_TEXT, "random"


def answer_subjective(q_type: str, title: str, ai_client=None, tiku=None,
                      tiku_answer=None, use_ai: bool = True):
    """主观题（简答/名词解释/论述/计算/案例分析）统一作答。

    答题链路（题库知识关联）：
      1. 题库参考答案长度达标 -> 直接采用（题库答案即标准得分要点）；
      2. 题库答案过短   -> 作为参考要点交给 AI 按答题模板扩充（避免遗漏得分点）；
      3. 无题库参考     -> AI 按“评分规则＋结构模板＋学科要求”生成。

    Returns:
        (规范化答案, 来源)；来源为 "tiku" / "ai" / "random"。
    """
    if tiku_answer is None:
        tiku_answer = _query_tiku(tiku, title, q_type)
    if tiku_answer:
        reference = normalize_shortanswer(tiku_answer)
        if reference:
            if len(reference) >= TIKU_ANSWER_MIN_LENGTH:
                return reference, "tiku"
            # 参考要点过短：AI 关联扩充
            if use_ai:
                ai_raw = _ask_ai(ai_client, {
                    "question_type": TYPE_LABELS.get(q_type, "简答题"),
                    "title": title,
                    "options": [],
                    "system_prompt": build_subjective_system_prompt(q_type, title),
                    "user_content": (
                        f"题目：{title}\n\n"
                        "以下是从题库检索到的参考要点（可能不完整或表述简略，"
                        "仅作为知识要点参考，不得照抄其简略表述，须按题目要求完整作答）：\n"
                        f"{reference}"
                    ),
                })
                if ai_raw:
                    answer = normalize_shortanswer(ai_raw)
                    if answer:
                        return answer, "ai"
            return reference, "tiku"

    if use_ai:
        ai_raw = _ask_ai(ai_client, {
            "question_type": TYPE_LABELS.get(q_type, "简答题"),
            "title": title,
            "options": [],
            "system_prompt": build_subjective_system_prompt(q_type, title),
        })
        if ai_raw:
            answer = normalize_shortanswer(ai_raw)
            if answer and answer != FALLBACK_TEXT:
                return answer, "ai"

    return FALLBACK_TEXT, "random"


def answer_shortanswer(title: str, ai_client=None, tiku=None, use_ai: bool = True, tiku_answer=None):
    """生成简答题答案（委托主观题统一链路，享受模板与学科要求）。"""
    return answer_subjective("shortanswer", title, ai_client, tiku, tiku_answer, use_ai)


def answer_non_question(q_type: str, title: str, ai_client=None, tiku=None,
                        tiku_answer=None, use_ai: bool = True):
    """非选择题统一入口。

    Args:
        q_type: "completion"/"shortanswer"/"term_explanation"/"essay"/"calculation"/
                "case_analysis"，或对应题型码 "2"/"4"/"5"/"6"/"7"/"8"。
        title: 题干文本。
        tiku_answer: 调用方已取得的题库结果（避免对题库重复请求）。

    Returns:
        (答案, 来源)；不支持的题型返回 ("", "")。
    """
    q_type = TYPE_MAP.get(q_type, q_type)
    if q_type == "completion":
        return answer_completion(title, ai_client, tiku, use_ai, tiku_answer)
    if q_type in SUBJECTIVE_TYPES:
        return answer_subjective(q_type, title, ai_client, tiku, tiku_answer, use_ai)
    return "", ""
