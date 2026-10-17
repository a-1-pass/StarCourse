# _*_ coding:utf-8 _*_
"""视觉模型 OCR（可插拔模块，config.yml 默认关闭，仅多模态 API 路线）。

用途：题目中的图片题（题干/选项是 <img> 图片 URL）无法纯文本作答，
本模块把图片下载转 base64 data-URI，经 OpenAI 兼容 vision chat 接口
识别为文本，再拼接进题目送入既有答题链（题库/AI）。

设计要点：
- 零新重依赖：仅用 requests（经 utils.doGet）+ base64 + 现有 ai_providers；
- 复用 ai_providers 注册表：取 MultiAIClient 的 provider 链，
  仅 OpenAI 兼容 provider（具备 _endpoint/_post_json）支持视觉，
  不支持的 provider 自动跳过并尝试下一个，全部失败返回空串不崩溃；
- 图片下载走 utils.doGet（自动注入反检测头），并补 Referer 以适配学习通图床。
"""
from __future__ import annotations

import base64
import re

import loguru

from utils import doGet

# 题干/选项 HTML 中的图片地址提取
_IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)

# 学习通图床防盗链 Referer
_CHAOXING_REFERER = "https://mooc1.chaoxing.com/"


def contains_image(text: str) -> bool:
    """判断文本（HTML 片段）中是否包含图片。"""
    return bool(text) and bool(_IMG_SRC_RE.search(text))


def extract_image_urls(text: str) -> list:
    """提取文本（HTML 片段）中的全部图片 URL。"""
    if not text:
        return []
    return [u.strip() for u in _IMG_SRC_RE.findall(text) if u.strip()]


def _guess_mime(data: bytes) -> str:
    """按魔数识别常见图片格式；无法识别时按 JPEG 兜底（视觉模型普遍兼容）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def get_vision_ocr_config() -> dict:
    """读取 config.yml 的 FunConfig.deal-mission.vision-ocr 节；读取失败返回空 dict。"""
    try:
        from config import GloConfig
        data = GloConfig.data or {}
        cfg = (data.get("FunConfig", {}) or {}).get("deal-mission", {}) or {}
        section = cfg.get("vision-ocr", {})
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def build_ocr_from_config(client=None):
    """接线入口：开关开启时返回 VisionOCR，否则 None（调用方零行为变化）。

    :param client: 可选，已有的 MultiAIClient / 单 provider；
                   None 时按需从 AIConfig 懒构建
    """
    cfg = get_vision_ocr_config()
    if not cfg.get("enable"):
        return None
    try:
        return VisionOCR(cfg, client=client)
    except Exception as e:
        loguru.logger.debug(f"视觉OCR初始化失败: {e}")
        return None


def ocr_images_in_text(text: str, ocr: "VisionOCR", prompt_hint: str = None,
                       headers: dict = None) -> str:
    """把文本中的图片 OCR 成文字并以「[图片文字: ...]」拼接回原文。

    - ocr 为 None（开关关闭）或无图片时原样返回，零行为变化；
    - 单张图片识别失败跳过该图，不影响其余图片与主流程。
    """
    if ocr is None or not text:
        return text
    urls = extract_image_urls(text)
    if not urls:
        return text
    parts = []
    for url in urls:
        try:
            result = ocr.recognize(url, prompt_hint=prompt_hint, headers=headers)
            if result:
                parts.append(f"[图片文字: {result}]")
        except Exception as e:
            loguru.logger.debug(f"图片OCR失败（已跳过）: {url[:60]}... {e}")
    if not parts:
        return text
    return text + "\n" + "\n".join(parts)


class VisionOCR:
    """多模态视觉识别客户端：``recognize(image_url_or_bytes, prompt_hint=None) -> str``。"""

    #: 默认识别提示词（可用 prompt_hint 覆盖补充题目语境）
    DEFAULT_PROMPT = (
        "请识别这张图片中的所有文字内容（可能是题目或选项），"
        "按原文顺序逐行输出，只输出识别到的文字，不要解释。"
    )

    def __init__(self, config: dict = None, client=None):
        self.config = dict(config or {})
        self._client = client
        # 单张图片大小上限（MB），超限跳过识别防止打爆上下文
        try:
            self.max_bytes = float(self.config.get("max-image-mb", 5)) * 1024 * 1024
        except (TypeError, ValueError):
            self.max_bytes = 5 * 1024 * 1024
        # 视觉模型名覆盖（留空沿用 provider 配置的 model）
        self.model_override = str(self.config.get("model", "") or "").strip()

    # ── provider 链 ─────────────────────────────────────

    def _get_providers(self) -> list:
        """取当前 active 的 provider 链（MultiAIClient 或单 provider 均可）。"""
        if self._client is not None:
            if hasattr(self._client, "providers"):
                return list(self._client.providers)
            return [self._client]
        try:
            from ai_providers import build_multi_client
            client = build_multi_client()
            return list(client.providers) if client else []
        except Exception as e:
            loguru.logger.debug(f"视觉OCR构建AI客户端失败: {e}")
            return []

    # ── 图片获取与编码 ───────────────────────────────────

    def _download_image(self, url: str, headers: dict = None):
        """下载图片字节（走 doGet 反检测头 + 学习通图床 Referer）。"""
        req_headers = {"Referer": _CHAOXING_REFERER}
        if isinstance(headers, dict):
            req_headers.update(headers)
        resp = doGet(url=url, headers=req_headers, ifFullBack=True)
        if resp is None or getattr(resp, "status_code", 0) != 200:
            return None
        return resp.content

    def _to_data_uri(self, image_url_or_bytes, headers: dict = None):
        """统一转成 base64 data-URI；超限/下载失败返回 None。"""
        if isinstance(image_url_or_bytes, (bytes, bytearray)):
            data = bytes(image_url_or_bytes)
        else:
            data = self._download_image(str(image_url_or_bytes), headers=headers)
        if not data:
            return None
        if len(data) > self.max_bytes:
            loguru.logger.warning(
                f"图片大小 {len(data) / 1024 / 1024:.1f}MB 超过上限，跳过识别"
            )
            return None
        mime = _guess_mime(data)
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    # ── 视觉调用 ────────────────────────────────────────

    def _recognize_with(self, provider, prompt: str, data_uri: str):
        """用单个 OpenAI 兼容 provider 发起 vision chat 调用。"""
        headers = {
            "Authorization": f"Bearer {provider.config.get('api_key', '')}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model_override or provider.config.get("model"),
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
            "max_tokens": int(provider.config.get("max_tokens_content", 1024)),
            "temperature": 0.0,
            "stream": False,
        }
        data = provider._post_json(provider._endpoint(), headers, body)
        return str(data["choices"][0]["message"]["content"]).strip()

    def recognize(self, image_url_or_bytes, prompt_hint: str = None,
                  headers: dict = None) -> str:
        """识别图片文字；任何环节失败返回空串，不抛异常。"""
        try:
            data_uri = self._to_data_uri(image_url_or_bytes, headers=headers)
        except Exception as e:
            loguru.logger.debug(f"图片获取/编码失败: {e}")
            return ""
        if not data_uri:
            return ""

        prompt = prompt_hint or self.DEFAULT_PROMPT
        for provider in self._get_providers():
            try:
                # 仅 OpenAI 兼容 provider 支持视觉接口；其余（claude/wenxin）跳过
                if not hasattr(provider, "_endpoint") or not hasattr(provider, "_post_json"):
                    loguru.logger.debug(
                        f"视觉OCR跳过不支持的 provider: "
                        f"{getattr(provider, 'provider_name', type(provider).__name__)}"
                    )
                    continue
                if not provider.is_configured():
                    continue
                result = self._recognize_with(provider, prompt, data_uri)
                if result:
                    return result
            except Exception as e:
                loguru.logger.debug(
                    f"视觉OCR provider "
                    f"{getattr(provider, 'provider_name', '?')} 失败，尝试下一个: {e}"
                )
                continue

        loguru.logger.warning("视觉OCR：所有可用 provider 均识别失败，返回空")
        return ""
