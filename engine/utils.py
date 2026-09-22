# -*- coding: utf-8 -*-
"""HTTP helpers and configuration loader for the StarCourse engine."""
import requests
from loguru import logger
from requests import post, get
import yaml
import pyDes
import binascii
import os
import sys
from time import sleep
from classis.SelfException import RequestException
from lxml.etree import _ElementUnicodeResult

import os
import sys

# ── 跨平台 / 反检测工具：优先从主项目 core 导入，独立运行时降级到内联实现 ──
try:
    from src.network_utils import (
        build_antidetect_headers, jitter_sleep, random_delay,
        detect_platform, get_random_user_agent, get_app_data_dir,
    )
    _HAS_PLATFORM_UTILS = True
except Exception:
    _HAS_PLATFORM_UTILS = False
    import platform as _platform
    import random as _random
    import time as _time

    def detect_platform():
        s = _platform.system().lower()
        return {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(s, "unknown")

    def get_random_user_agent(p=None):
        return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def build_antidetect_headers(ua=None, p=None):
        ua = ua or get_random_user_agent(p)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "sec-ch-ua": '"Chromium";v="120", "Not_A Brand";v="8", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }

    def jitter_sleep(base, jitter=0.5):
        _time.sleep(max(0.0, base - jitter) + _random.uniform(0, 2 * jitter))

    def random_delay(a=0.3, b=1.2):
        _time.sleep(_random.uniform(a, b))

    def get_app_data_dir(name="StarCourse"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        p = os.path.join(base, name)
        os.makedirs(p, exist_ok=True)
        return p

_ENGINE_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_PKG_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_PKG_DIR)

with open(os.path.join(_ENGINE_PKG_DIR, "config.yml"), "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)
    glo_headers = config.get("GloConfig").get("headers") or {}
    glo_timeout = config.get("GloConfig").get("timeout") or 30
    logger.success("Loaded config successfully")
    if_delay = config.get("GloConfig").get("delay").get("enable")
    time_delay = config.get("GloConfig").get("delay").get("time")

# 进程内固定一套反检测请求头（同一会话 UA 一致，避免被风控识别为异常跳动）
RUNTIME_HEADERS = build_antidetect_headers()
# 若配置文件中缺少 UA，用反检测头的 UA 兜底，保证三平台都能取到真实浏览器 UA
if "User-Agent" not in glo_headers:
    glo_headers["User-Agent"] = RUNTIME_HEADERS["User-Agent"]

# 请求失败重试配置（提升网络抖动 / 长时运行下的稳定性）
MAX_RETRY = 3
BASE_RETRY_DELAY = 2.0

ses = requests.session()


# 指纹类请求头由 RUNTIME_HEADERS 统一保证，调用方传入的同类头一律忽略，避免冲突
_FINGERPRINT_KEYS = {
    "user-agent", "accept-language", "accept-encoding",
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "upgrade-insecure-requests", "sec-fetch-dest", "sec-fetch-mode",
    "sec-fetch-site", "sec-fetch-user",
}


def _build_effective_headers(headers) -> dict:
    """合并反检测指纹头与调用方业务头（Referer/Origin/Host/X-Requested-With 等保留）。"""
    if not isinstance(headers, dict):
        return dict(RUNTIME_HEADERS)
    merged = dict(RUNTIME_HEADERS)
    for k, v in headers.items():
        if k.lower() in _FINGERPRINT_KEYS:
            continue
        merged[k] = v
    return merged


def doGet(url: str, headers: 'dict|str' = glo_headers, ifFullBack: bool = False) -> 'str|requests.Response':
    """
    调用requests进行Get请求，并输出日志

    - 自动注入跨平台反检测指纹头（UA / Client Hints / Sec-Fetch-*）
    - 请求间随机延迟（jitter），避免固定节奏被风控识别
    - 失败自动重试（MAX_RETRY 次，指数退避）
    - 每次重建 cookie 池，避免跨请求 / 跨账号 cookie 累积与串号

    :param url: 欲访问的链接地址
    :param headers: 请求携带的headers，默认为config文件中GloConfig.headers
    :param ifFullBack 是否返回完整的Response信息
    :return: 返回网页文本信息，即html.text
    """
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            logger.debug("Do Get to Url %s" % url)

            eff_headers = _build_effective_headers(headers)
            cookie_str = eff_headers.pop("Cookie", None)

            ses.headers.clear()
            ses.headers.update(eff_headers)
            # 重建 cookie 池，避免历史 cookie 累积导致内存膨胀或跨账号串号
            ses.cookies.clear()
            if cookie_str:
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        ses.cookies.set(k.strip(), v.strip())

            # 固定延迟叠加随机抖动，模拟人工操作节奏
            jitter_sleep(time_delay if if_delay else 0.2, 0.3)
            resp = ses.get(url=url, timeout=glo_timeout)
            if ifFullBack:
                return resp
            if resp.status_code == 200:
                return resp.text
            resp.close()
            raise RequestException(resp, 0)

        except Exception as e:
            last_err = e
            if attempt < MAX_RETRY - 1:
                logger.warning(f"Get {url} 失败(第{attempt+1}/{MAX_RETRY}次): {e}")
                jitter_sleep(BASE_RETRY_DELAY, 1.0)
            else:
                logger.error(f"Get Url {url} Error\n {e}")
    return "" if not ifFullBack else None


def doPost(url: str, headers: 'dict|str' = glo_headers, data: 'dict|str' = "", ifFullBack: bool = False) -> 'str|requests.Response':
    """
    调用requests进行Post请求，并输出日志

    行为同 doGet：反检测头注入、随机延迟、失败重试、cookie 隔离。

    :param url: 欲访问的链接地址
    :param headers: 请求携带的headers，默认为config文件中GloConfig.headers
    :param data: 请求携带的data数据，默认为空
    :param ifFullBack 是否返回完整的Response信息
    :return: 返回网页文本信息，即html.text
    """
    last_err = None
    for attempt in range(MAX_RETRY):
        try:
            logger.debug("Do Post to Url %s" % url)
            logger.debug("With data: %s" % data)

            eff_headers = _build_effective_headers(headers)
            cookie_str = eff_headers.pop("Cookie", None)

            ses.headers.clear()
            ses.headers.update(eff_headers)
            # 重建 cookie 池，避免历史 cookie 累积导致内存膨胀或跨账号串号
            ses.cookies.clear()
            if cookie_str:
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        ses.cookies.set(k.strip(), v.strip())

            jitter_sleep(time_delay if if_delay else 0.2, 0.3)
            resp = ses.post(url=url, data=data, timeout=glo_timeout)
            if ifFullBack:
                return resp
            if resp.status_code == 200:
                return resp.text
            resp.close()
            raise RequestException(resp, 1)

        except Exception as e:
            last_err = e
            if attempt < MAX_RETRY - 1:
                logger.warning(f"Post {url} 失败(第{attempt+1}/{MAX_RETRY}次): {e}")
                jitter_sleep(BASE_RETRY_DELAY, 1.0)
            else:
                logger.error(f"Post Url {url} Error\n {e}")
    return "" if not ifFullBack else None


def xpath_first(element, path):
    """
    返回xpath获取到的第一个元素，如果没有则返回空字符串
    由于 lxml.etree._Element._Element 为私有类，所以不予设置返回值类型

    :param element: etree.HTML实例
    :param path: xpath路径
    :return: 第一个元素或空字符串
    """
    if type(element) in (str, int):
        return element
    res = element.xpath(path)
    if type(res) == _ElementUnicodeResult:
        return res
    if len(res) == 1:
        return res[0]
    else:
        return ""


def direct_url(old_url: str, headers: dict, ifLoop: bool = False) -> str:
    """
    返回重定向后的真实Url

    :param old_url: 待重定向的链接地址
    :param headers: 附带的请求头
    :param ifLoop: 是否迭代查询直至无跳转
    :return: 最终的Url
    """
    logger.debug("Redirect old url: %s" % old_url)
    location_new = None
    eff_headers = _build_effective_headers(headers)
    try:
        while location_new is None:
            try:
                rsp = ses.get(url=old_url, headers=eff_headers, allow_redirects=False, timeout=glo_timeout)
            except Exception:
                rsp = None
            if rsp is None:
                jitter_sleep(1.0, 0.5)
                continue
            location_new = rsp.headers.get("Location")
            if ifLoop:
                return location_new or old_url
            if location_new is None:
                logger.debug("Final url: %s" % old_url)
                return old_url
            else:
                old_url = location_new
                location_new = None
    except Exception as e:
        logger.error(e)
        return ""


def encrypt_des(msg, key):
    des_obj = pyDes.des(key, key, pad=None, padmode=pyDes.PAD_PKCS5)
    secret_bytes = des_obj.encrypt(msg, padmode=pyDes.PAD_PKCS5)
    return binascii.b2a_hex(secret_bytes)


def clear_console():
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")



