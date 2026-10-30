# _*_ coding:utf-8 _*_
"""engine/config.yml 运行参数：读取与「保留注释」的定点写入。

GUI 只暴露少量高频参数（见 ``SETTING_SPECS``）。写入时按 YAML 缩进定位
目标键，仅替换值本身，文件中的中文注释与其它配置段保持原样。
"""
from __future__ import annotations

import os
import re
import sys

import yaml

#: GUI 可修改参数的元数据。``path`` 为 YAML 中从根到键的层级路径。
SETTING_SPECS = [
    {
        "key": "video-mode", "label": "视频模式", "group": "视频",
        "path": ("FunConfig", "deal-mission", "video-mode"),
        "type": "choice", "default": 1,
        "options": [("立即完成", 0), ("等时长刷取", 1)],
        "help": "立即完成会跳过播放时长校验，等时长按真实时长刷取（更稳）",
    },
    {
        "key": "speed", "label": "播放倍速", "group": "视频",
        "path": ("FunConfig", "deal-mission", "speed"),
        "type": "float", "default": 1.0, "min": 1.0, "max": 2.0, "step": 0.1,
        "help": "仅等时长刷取生效，范围 1.0 ~ 2.0",
    },
    {
        "key": "enable-multi-thread", "label": "启用多线程", "group": "并发",
        "path": ("FunConfig", "deal-mission", "enable-multi-thread"),
        "type": "bool", "default": True,
        "help": "关闭后所有任务串行执行",
    },
    {
        "key": "max-concurrent-threads", "label": "最大并发线程数", "group": "并发",
        "path": ("FunConfig", "deal-mission", "max-concurrent-threads"),
        "type": "int", "default": 5, "min": 1, "max": 15,
        "help": "快捷操作与并发刷课的默认线程数",
    },
    {
        "key": "cover-rate", "label": "章节覆盖率阈值", "group": "提交",
        "path": ("FunConfig", "deal-mission", "cover-rate"),
        "type": "float", "default": 0.6, "min": 0.0, "max": 1.0, "step": 0.05,
        "help": "低于该覆盖率仅保存不提交",
    },
    {
        "key": "submit-mode", "label": "提交方式", "group": "提交",
        "path": ("FunConfig", "deal-mission", "submit-mode"),
        "type": "choice", "default": "auto",
        "options": [("自动（按覆盖率）", "auto"), ("仅保存", "save")],
        "help": "仅保存模式下不会向平台提交答案",
    },
    {
        "key": "notopen-action", "label": "任务点关闭时", "group": "容错",
        "path": ("FunConfig", "deal-mission", "notopen-action"),
        "type": "choice", "default": "retry",
        "options": [("重试", "retry"), ("询问", "ask"), ("继续", "continue")],
        "help": "遇到已关闭任务点时的处理策略",
    },
    {
        "key": "progress-persistence", "label": "章节进度记忆", "group": "容错",
        "path": ("FunConfig", "deal-mission", "progress-persistence"),
        "type": "bool", "default": True,
        "help": "开启后重启可从中断章节继续",
    },
    {
        "key": "video-retry-rounds", "label": "视频补刷轮数", "group": "容错",
        "path": ("FunConfig", "deal-mission", "video-retry-rounds"),
        "type": "int", "default": 5, "min": 0, "max": 10,
        "help": "同轮内对失败视频的补刷次数，0 为关闭",
    },
    {
        "key": "exhaustive-enable", "label": "客观题穷举", "group": "容错",
        "path": ("FunConfig", "deal-mission", "exhaustive", "enable"),
        "type": "bool", "default": False,
        "help": "反复提交单选/判断题直到整卷通过，耗时较长",
    },
]

_SPEC_BY_KEY = {spec["key"]: spec for spec in SETTING_SPECS}

_KEY_RE = re.compile(
    r"^(?P<indent>[ ]*)(?P<key>[A-Za-z0-9_\-]+):"
    r"(?P<sep>[ \t]*)(?P<value>[^#\n]*?)(?P<comment>[ \t]*#.*)?$"
)

#: 当 config.yml 完全缺失时自动落盘的默认配置（保留全部中文注释与默认结构）
_DEFAULT_CONFIG_YML = """\
InfoStr:
  instructions: |-
    StarCourse — 超星学习通课程自动化工具
    使用前请确认您使用的是最新版，防止因为超星系统更新导致的功能失效
    
    使用须知与功能介绍:
    1.本项目支持一键完成的任务点不包括考试与测试
    2.输入密码时会被自动隐藏，防止您的密码被偷窥
    3.项目不能完全保证不被系统识别异常，请理性使用
    4.所有功能均采用发送GET/POST请求包完成，效率更高且占用资源低
  signStr: |-
    1.使用用户名（手机号）与密码进行登录
    2.使用Cookie进行登录
    请选择您的登录方式：
  errSignMode: 请输入正确的序号，如果您不清楚怎么选，请默认选择1

GloConfig:
  timeout: 3
  delay:
    enable: True
    time: 0.5
  headers:
    User-Agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  debug:
    enable: True
    level: 8

LogConfig:
  max_log_size_mb: 10
  max_backup_count: 5
  max_log_age_days: 7
  enable_auto_cleanup: True

CacheConfig:
  max_cache_size_mb: 50
  max_cache_age_days: 14
  max_cache_items: 10000
  enable_auto_cleanup: True
  ai_cache_ttl_days: 30
  tiku_cache_ttl_days: 7
  cookie_cache_ttl_days: 7

FunConfig:
  deal-mission:
    video-mode: 1 # 0为立即完成,1为等时长刷取
    single-thread: false
    enable-multi-thread: true
    max-concurrent-threads: 5
    cover-rate: 0.6
    submit-mode: auto
    notopen-action: retry
    speed: 1.0
    learning-mode: stage
    progress-persistence: true
    video-retry-rounds: 5
    exhaustive:
      enable: false
      interval: 3.0
      per-question-max-tries: 5
      max-submissions: 30
      backoff-on-fail: 5.0
      max-vars: 8
      max-combos: 200
    font-decode:
      enable: false
      map-table: 'data/font_map_table.json'
    vision-ocr:
      enable: false
      model: ''
      max-image-mb: 5
  set-log:
    delay: 30

TikuConfig:
  provider: ''
  token: ''
  tikuadapter_url: ''
  api_key: ''
  base_url: 'https://api.deepseek.com'
  model: 'deepseek-chat'
  delay: 0
  cover_rate: 0.8
  submit: false
  check_llm_connection: true

AIConfig:
  active: deepseek
  priority:
    - deepseek
    - openai
    - claude
    - wenxin
  timeout: 30
  max_retries: 1
  providers:
    deepseek:
      api_key: ''
      base_url: 'https://api.deepseek.com'
      model: 'deepseek-chat'
      max_tokens: 10
      max_tokens_content: 512
      temperature: 0.0
    openai:
      api_key: ''
      base_url: 'https://api.openai.com'
      model: 'gpt-4o-mini'
      max_tokens: 10
      max_tokens_content: 512
      temperature: 0.0
    claude:
      api_key: ''
      base_url: 'https://api.anthropic.com'
      model: 'claude-3-5-haiku-20241022'
      max_tokens: 10
      max_tokens_content: 512
      temperature: 0.0
    wenxin:
      api_key: ''
      secret_key: ''
      base_url: 'https://aip.baidubce.com'
      model: 'ernie-4.0-turbo-8k'
      max_tokens: 10
      max_tokens_content: 512
      temperature: 0.0

SignConfig:
  enable: true
  check_interval: 60

UserData:
  cookie: ''
  auto-sign: True
"""


def config_yml_path() -> str:
    """config.yml 路径：开发期为 engine/config.yml，打包后为用户数据目录副本。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not getattr(sys, "frozen", False):
        target = os.path.join(root, "engine", "config.yml")
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        target = os.path.join(base, "StarCourse", "config.yml")

    # 文件不存在时自动写入默认配置（真正实现「零配置启动」）
    if not os.path.exists(target):
        _write_default_config(target)
    return target


def _write_default_config(path: str) -> None:
    """将默认配置原子写入指定路径。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_DEFAULT_CONFIG_YML)
    os.replace(tmp, path)


def read_settings(path: str) -> dict:
    """读取可修改参数；文件缺失或解析失败时返回默认值。"""
    data: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        data = {}

    values = {}
    for spec in SETTING_SPECS:
        node = data
        for part in spec["path"]:
            node = node.get(part) if isinstance(node, dict) else None
        values[spec["key"]] = spec["default"] if node is None else node
    return values


def apply_settings(text: str, values: dict) -> str:
    """在配置文本中定点替换参数值，保留原有注释与其它内容。"""
    unknown = set(values) - set(_SPEC_BY_KEY)
    if unknown:
        raise KeyError(f"unknown setting(s): {sorted(unknown)}")

    lines = text.splitlines()
    for key, value in values.items():
        spec = _SPEC_BY_KEY[key]
        _patch_line(lines, spec["path"], _format_value(spec, value))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def save_settings(path: str, values: dict) -> None:
    """读取原文件、定点改值后原子写回（不破坏注释）。

    若文件不存在，先自动写入默认配置再修改，确保「零配置」场景下也能正常保存。
    """
    if not os.path.exists(path):
        _write_default_config(path)
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    new_text = apply_settings(text, values)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.replace(tmp_path, path)


# ── 内部实现 ──────────────────────────────────────────────


def _format_value(spec: dict, value) -> str:
    kind = spec["type"]
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    if kind == "float":
        return f"{float(value):g}"
    return str(value)


def _patch_line(lines: list[str], path: tuple, formatted: str) -> None:
    """定位 ``path`` 指向的行并替换其值；缺失时插入到父块末尾。"""
    parent_idx, parent_indent = -1, -1
    for depth, part in enumerate(path[:-1]):
        idx, indent = _find_child(lines, part, parent_idx, parent_indent)
        if idx < 0:
            raise KeyError(f"config section not found: {'.'.join(path[:-1])}")
        parent_idx, parent_indent = idx, indent

    target_idx, indent = _find_child(lines, path[-1], parent_idx, parent_indent)
    if target_idx >= 0:
        match = _KEY_RE.match(lines[target_idx])
        comment = match.group("comment") or ""
        lines[target_idx] = f"{match.group('indent')}{match.group('key')}: {formatted}{comment}"
        return

    children = _children_range(lines, parent_idx, parent_indent)
    child_indent = _detect_indent(lines, children[0], children[1], parent_indent)
    insert_at = children[1]
    lines.insert(insert_at, f"{' ' * child_indent}{path[-1]}: {formatted}")


def _find_child(lines: list[str], key: str, parent_idx: int, parent_indent: int) -> tuple[int, int]:
    if parent_idx >= 0:
        start, end = _children_range(lines, parent_idx, parent_indent)
    else:
        start, end = 0, len(lines)
    for i in range(start, end):
        match = _KEY_RE.match(lines[i])
        if not match:
            continue
        indent = len(match.group("indent"))
        if parent_idx >= 0 and indent <= parent_indent:
            break
        if match.group("key") == key:
            return i, indent
    return -1, -1


def _children_range(lines: list[str], parent_idx: int, parent_indent: int) -> tuple[int, int]:
    start = parent_idx + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if not lines[i].strip():
            continue
        indent = len(lines[i]) - len(lines[i].lstrip(" "))
        if indent <= parent_indent:
            end = i
            break
    return start, end


def _detect_indent(lines: list[str], start: int, end: int, parent_indent: int) -> int:
    for i in range(start, end):
        match = _KEY_RE.match(lines[i])
        if match:
            return len(match.group("indent"))
    return parent_indent + 2
