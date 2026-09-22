"""跨平台适配与反检测工具模块

本模块集中处理四件跨平台/反检测相关的事：
1. 平台检测        —— 统一返回 windows / linux / macos，避免到处写 platform.system()
2. 动态 User-Agent —— 按平台匹配真实浏览器 UA，解决 Linux/macOS 上暴露 Windows 特征的问题
3. 反检测请求头    —— 生成含现代客户端提示头（Client Hints: sec-ch-ua 等）的完整指纹头
4. 跨平台路径      —— 数据/缓存目录按 OS 规范解析（替代硬编码 Windows 路径）
5. 行为噪声        —— 随机延迟，模拟人工操作节奏，降低固定访问频率被风控识别

仅依赖标准库，无任何第三方依赖，保证三平台零差异运行。
"""
from __future__ import annotations

import os
import platform
import random
import re
import time
from typing import Dict, Optional


# ───────────────────────── 平台检测 ─────────────────────────

def detect_platform() -> str:
    """返回 'windows' | 'linux' | 'macos' | 'unknown'（统一小写）。"""
    sys_name = platform.system().lower()
    if sys_name == "windows":
        return "windows"
    if sys_name == "darwin":
        return "macos"
    if sys_name == "linux":
        return "linux"
    return "unknown"


def detect_locale() -> str:
    """根据系统环境推断 Accept-Language 值。"""
    lang = (os.environ.get("LANG") or os.environ.get("LANGUAGE") or "").lower()
    if "zh" in lang:
        return "zh-CN,zh;q=0.9,en;q=0.8"
    if "en" in lang:
        return "en-US,en;q=0.9"
    # 默认中文环境（多数用户场景）
    return "zh-CN,zh;q=0.9,en;q=0.8"


# ───────────────────────── User-Agent 池 ─────────────────────────
# 各平台均使用真实浏览器 UA，版本较新且自然分散；避免所有请求都来自同一版本/同一 OS
# 借鉴 GitHub 开源项目（如 TechXueXi）"随机 Chrome UA" 的思路，但采用 2025-2026 年
# 仍在主流版本的 Chrome/Edge/Firefox，并按运行平台匹配，降低跨平台暴露 Windows 特征。
_USER_AGENTS: Dict[str, list] = {
    "windows": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
    ],
    "macos": [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    ],
    "linux": [
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:139.0) Gecko/20100101 Firefox/139.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    ],
}


def get_random_user_agent(platform_name: Optional[str] = None) -> str:
    """按平台随机返回一个真实 UA；未指定时使用当前运行平台。

    借鉴开源项目"随机 Chrome UA"做法，但确保：
    - 按运行平台匹配（Linux/macOS 不再暴露 Windows 特征）
    - 版本号落在当前主流区间，避免老旧 UA 被风控标记
    """
    plat = platform_name or detect_platform()
    pool = _USER_AGENTS.get(plat, _USER_AGENTS.get("windows"))
    return random.choice(pool)


def _chrome_version_from_ua(ua: str) -> str:
    """从 UA 提取 Chrome 主版本号，用于构造 sec-ch-ua。"""
    m = re.search(r"Chrome/(\d+)\.", ua)
    return m.group(1) if m else "120"


def _sec_ch_platform(platform_name: Optional[str] = None) -> str:
    plat = platform_name or detect_platform()
    return {"windows": '"Windows"', "macos": '"macOS"', "linux": '"Linux"'}.get(plat, '"Windows"')


def build_antidetect_headers(ua: Optional[str] = None,
                             platform_name: Optional[str] = None) -> Dict[str, str]:
    """生成贴近真实浏览器的请求头，含现代客户端提示头（Client Hints）。

    这些头能有效降低被第三方风控基于 HTTP 指纹识别为脚本的概率：
    - sec-ch-ua / sec-ch-ua-mobile / sec-ch-ua-platform（Client Hints）
    - Sec-Fetch-* 系列（浏览器导航语义）
    """
    plat = platform_name or detect_platform()
    ua = ua or get_random_user_agent(plat)
    chrome_ver = _chrome_version_from_ua(ua)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": detect_locale(),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "sec-ch-ua": f'"Chromium";v="{chrome_ver}", "Not_A Brand";v="8", "Google Chrome";v="{chrome_ver}"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": _sec_ch_platform(plat),
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


# ───────────────────────── 跨平台路径 ─────────────────────────

def get_app_data_dir(app_name: str = "WeLearnHelper") -> str:
    """跨平台返回应用数据目录，自动创建。

    Windows : %LOCALAPPDATA%\\<app_name>
    macOS   : ~/Library/Application Support/<app_name>
    Linux   : $XDG_DATA_HOME/<app_name> 或 ~/.local/share/<app_name>
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
    elif platform.system().lower() == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path


def get_app_cache_dir(app_name: str = "WeLearnHelper") -> str:
    """跨平台返回缓存目录，自动创建。"""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local")
    elif platform.system().lower() == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache")
    path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path


# ───────────────────────── 行为噪声 ─────────────────────────

def jitter_sleep(base: float, jitter: float = 0.5) -> None:
    """在 base ± jitter 范围内随机延迟，模拟人工操作间隔。

    固定节奏（如每次都睡 0.5s）是典型脚本特征，加抖动可显著降低被识别概率。
    """
    low = max(0.0, base - jitter)
    high = base + jitter
    time.sleep(random.uniform(low, high))


def random_delay(min_sec: float = 0.3, max_sec: float = 1.2) -> None:
    """随机延迟一段时长。"""
    time.sleep(random.uniform(min_sec, max_sec))


# ───────────────────────── 端口 / 限流助手 ─────────────────────────

def _port_free(port: int) -> bool:
    """检测指定 TCP 端口当前是否未被占用。"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", port))
        return True
    except OSError:
        return False


def find_available_port(preferred: Optional[int] = None,
                        low: int = 30000, high: int = 65535,
                        max_attempts: int = 50) -> int:
    """找到一个当前未被占用的 TCP 端口（用于本地回调 / OAuth 服务等）。

    移植自开源项目 mooc-work-answer 的 OAuthLoginHandler.find_available_port，
    简化为无依赖的独立函数：优先尝试首选端口，否则在高端口区间随机探测。
    """
    tried: set = set()
    if preferred is not None:
        if _port_free(preferred):
            return preferred
        tried.add(preferred)
    for _ in range(max_attempts):
        p = random.randint(low, high)
        if p in tried:
            continue
        tried.add(p)
        if _port_free(p):
            return p
    raise RuntimeError("无法找到可用端口")


_RATE_LIMIT_RE = re.compile(
    r"Expected available in\s*([\d.]+)\s*seconds?", re.IGNORECASE)


def parse_rate_limit_delay(text: str) -> Optional[float]:
    """解析服务端返回的限流提示，返回需要等待的秒数；无则返回 None。

    移植自开源项目 yuketangHelperBUU 的 homeworkHelper：雨课堂在高频提交时会返回
    ``Expected available in X seconds.``，需按提示退避后重试，否则会持续被拒。
    适用于所有以文本形式返回限流信息的平台。
    """
    if not text:
        return None
    m = _RATE_LIMIT_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None
