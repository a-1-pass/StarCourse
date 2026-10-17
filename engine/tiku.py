# _*_ coding:utf-8 _*_
# 题库系统，adapted from reference implementation
import json
import random
import re
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from urllib.parse import quote

import loguru
import requests

from cache_dao import CacheDAO
from utils import doPost


class TikuBase(ABC):
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cache = CacheDAO()
        self._name = self.__class__.__name__
        self._api = None
        self._conf = None
        self.DISABLE = False
        self.SUBMIT = False
        self.COVER_RATE = 0.8
        self.query_delay = float(config.get("delay", 0)) if config else 0

    @property
    def name(self):
        return self._name

    @abstractmethod
    def query(self, question: dict) -> Optional[str]:
        pass

    @abstractmethod
    def init_tiku(self) -> bool:
        pass

    @abstractmethod
    def check_connection(self) -> bool:
        pass

    def get_submit_params(self) -> str:
        return "1" if self.SUBMIT else ""

    def judgement_select(self, answer: str) -> bool:
        if not answer:
            return random.choice([True, False])
        answer_lower = str(answer).lower().strip()
        if answer_lower in ["true", "1", "对", "√", "正确", "是", "yes", "y", "t"]:
            return True
        elif answer_lower in ["false", "0", "错", "×", "错误", "否", "no", "n", "f"]:
            return False
        return "对" in answer or "正确" in answer


class TikuYanxi(TikuBase):
    """言溪题库（https://tk.enncy.cn/）—— Token 凭证制在线题库。

    接口约定（来自官方 MCP 对接示例与站点实测）：
      - GET https://tk.enncy.cn/query
      - 参数：token（用户凭证，个人中心获取）、title（题干）、options（可选）、type（可选）
      - 响应：{"code": 0, "data": {"question": ..., "answer": ..., "ai"?}, "message": "请求成功"}
      - 凭证缺失/失效时仍返回 code=0，但 message="请求失败"、answer 为错误提示文本，
        因此成功判定必须同时校验 message 与 answer 内容。
    """
    # 服务端在凭证无效/次数不足时会把错误说明放在 answer 字段里，需过滤
    ANSWER_ERROR_MARKS = ("凭证", "配置错误", "请登录", "充值", "次数不足", "请求失败")

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.base_url = "https://tk.enncy.cn"
        self.token = str(config.get("token", "")).strip() if config else ""

    def init_tiku(self) -> bool:
        return bool(self.token)

    def check_connection(self) -> bool:
        if not self.token:
            loguru.logger.error("言溪题库未配置 token（tk.enncy.cn 个人中心获取）")
            return False
        try:
            resp = requests.get(
                f"{self.base_url}/query",
                params={"token": self.token, "title": "连接测试", "options": "", "type": ""},
                timeout=8,
            )
            data = resp.json()
            return data.get("message") == "请求成功"
        except Exception as e:
            loguru.logger.error(f"言溪题库连接失败: {e}")
            return False

    def query(self, question: dict) -> Optional[str]:
        title = str(question.get("title", "")).strip()
        if not title or not self.token:
            return None

        cached = self.cache.get_cache(title)
        if cached:
            loguru.logger.debug(f"缓存命中: {title[:50]}...")
            return cached

        if self.query_delay > 0:
            time.sleep(self.query_delay)

        options = question.get("options", "")
        if isinstance(options, (list, tuple)):
            options = "\n".join(str(o) for o in options)

        try:
            resp = requests.get(
                f"{self.base_url}/query",
                params={
                    "token": self.token,
                    "title": title,
                    "options": options or "",
                    "type": str(question.get("type", "") or ""),
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("message") == "请求成功" and data.get("code") == 0:
                answer = str((data.get("data") or {}).get("answer", "")).strip()
                if answer and not any(mark in answer for mark in self.ANSWER_ERROR_MARKS):
                    self.cache.add_cache(title, answer)
                    return answer
                if answer:
                    loguru.logger.debug(f"言溪题库返回异常应答: {answer[:50]}")
        except Exception as e:
            loguru.logger.debug(f"言溪题库查询失败: {e}")

        return None


class TikuGo(TikuBase):
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.token = config.get("token", "") if config else ""
        self.base_url = "https://tikuapi.13910287348.top"

    def init_tiku(self) -> bool:
        return bool(self.token)

    def check_connection(self) -> bool:
        if not self.token:
            return False
        try:
            resp = requests.get(f"{self.base_url}/check", params={"token": self.token}, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            loguru.logger.error(f"谷歌题库连接失败: {e}")
            return False

    def query(self, question: dict) -> Optional[str]:
        title = question.get("title", "")
        if not title or not self.token:
            return None

        cached = self.cache.get_cache(title)
        if cached:
            loguru.logger.debug(f"缓存命中: {title[:50]}...")
            return cached

        if self.query_delay > 0:
            time.sleep(self.query_delay)

        try:
            url = f"{self.base_url}/query"
            params = {
                "token": self.token,
                "q": title
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                answer = data.get("answer")
                if answer:
                    self.cache.add_cache(title, answer)
                    return answer
        except Exception as e:
            loguru.logger.debug(f"谷歌题库查询失败: {e}")

        return None


class TikuLike(TikuBase):
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.token = config.get("token", "") if config else ""
        self.base_url = "https://api.tikulike.com"

    def init_tiku(self) -> bool:
        return bool(self.token)

    def check_connection(self) -> bool:
        if not self.token:
            return False
        try:
            resp = requests.get(f"{self.base_url}/check", params={"token": self.token}, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            loguru.logger.error(f"Like题库连接失败: {e}")
            return False

    def query(self, question: dict) -> Optional[str]:
        title = question.get("title", "")
        if not title or not self.token:
            return None

        cached = self.cache.get_cache(title)
        if cached:
            loguru.logger.debug(f"缓存命中: {title[:50]}...")
            return cached

        if self.query_delay > 0:
            time.sleep(self.query_delay)

        try:
            url = f"{self.base_url}/query"
            data = {
                "token": self.token,
                "question": title
            }
            resp = requests.post(url, json=data, timeout=10)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 200:
                    answer = result.get("data", {}).get("answer")
                    if answer:
                        self.cache.add_cache(title, answer)
                        return answer
        except Exception as e:
            loguru.logger.debug(f"Like题库查询失败: {e}")

        return None


class TikuAdapter(TikuBase):
    """TikuAdapter 自建题库适配器（https://github.com/DokiDoki1103/tikuAdapter）。

    接口约定（以官方 README 实际协议为准）：
      - POST {url}/adapter-service/search
      - 请求体 JSON: {"question": 题干, "options": [选项...], "type": 题型码}
        题型码：单选0 多选1 填空2 判断3 问答4
      - URL 可附带查询参数指定题库与各题库 Token，
        如 ?use=wanneng,icodef&icodefToken=xxx（此时 url 直接写到 search 端点）
      - 响应: {"answer": {"bestAnswer": [...], "answerText": "...",
                          "allAnswer": [[...]]}, ...}
    配置项 tikuadapter_url 为空时禁用（init_tiku 返回 False，不参与轮询）。
    网络请求统一走 engine/utils 的 doPost（自动注入反检测头与随机延迟）。
    """

    # 题型字符串 → tikuAdapter 题型码（单选0 多选1 填空2 判断3 问答4）
    TYPE_MAP = {
        "single": 0, "单选": 0, "单选题": 0,
        "multiple": 1, "多选": 1, "多选题": 1,
        "completion": 2, "填空": 2, "填空题": 2,
        "judgement": 3, "判断": 3, "判断题": 3,
        "shortanswer": 4, "简答": 4, "简答题": 4, "问答": 4,
    }

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.url = str(config.get("tikuadapter_url", "")).strip() if config else ""

    def _search_url(self) -> str:
        # 允许 url 直接写到 /adapter-service/search（可附带 use/Token 查询参数）；
        # 只填服务根地址时自动补上端点
        if "/adapter-service/search" in self.url:
            return self.url
        return self.url.rstrip("/") + "/adapter-service/search"

    def _map_type(self, q_type) -> int:
        # 数字题型码直接透传（钳制在 0~4）；字符串按 TYPE_MAP 映射，未知回退单选
        if isinstance(q_type, int) and 0 <= q_type <= 4:
            return q_type
        return self.TYPE_MAP.get(str(q_type or "").strip().lower(), 0)

    def _post_search(self, payload: dict) -> 'dict|None':
        """POST /adapter-service/search，返回解析后的 JSON；失败返回 None。"""
        resp = doPost(
            self._search_url(),
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ifFullBack=True,
        )
        if resp is None or getattr(resp, "status_code", 0) != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _extract_answer(data: 'dict|None') -> 'str|None':
        """从响应提取答案：优先 bestAnswer 列表（# 连接），其次 answerText。"""
        answer = (data or {}).get("answer")
        if not isinstance(answer, dict):
            return None
        best = answer.get("bestAnswer")
        if isinstance(best, list) and best:
            return "#".join(str(a) for a in best)
        text = str(answer.get("answerText", "") or "").strip()
        return text or None

    def init_tiku(self) -> bool:
        return bool(self.url)

    def check_connection(self) -> bool:
        if not self.url:
            loguru.logger.error("TikuAdapter 未配置 tikuadapter_url（自建服务地址，如 http://localhost:8060）")
            return False
        try:
            data = self._post_search({"question": "连接测试", "options": [], "type": 0})
            if data is not None and "answer" in data:
                return True
            loguru.logger.error("TikuAdapter 连接失败: 响应缺少 answer 字段")
            return False
        except Exception as e:
            loguru.logger.error(f"TikuAdapter 连接失败: {e}")
            return False

    def query(self, question: dict) -> Optional[str]:
        title = str(question.get("title", "")).strip()
        if not title or not self.url:
            return None

        cached = self.cache.get_cache(title)
        if cached:
            loguru.logger.debug(f"缓存命中: {title[:50]}...")
            return cached

        if self.query_delay > 0:
            time.sleep(self.query_delay)

        options = question.get("options", "")
        if isinstance(options, str):
            # 字符串选项按行拆分为列表（协议要求数组）
            options = [line.strip() for line in options.splitlines() if line.strip()]

        payload = {
            "question": title,
            "options": list(options) if options else [],
            "type": self._map_type(question.get("type")),
        }

        try:
            data = self._post_search(payload)
            answer = self._extract_answer(data)
            if answer:
                self.cache.add_cache(title, answer)
                return answer
        except Exception as e:
            loguru.logger.debug(f"TikuAdapter 查询失败: {e}")

        return None


class AI(TikuBase):
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.api_key = config.get("api_key", "") if config else ""
        self.base_url = config.get("base_url", "https://api.deepseek.com/v1") if config else "https://api.deepseek.com/v1"
        self.model = config.get("model", "deepseek-chat") if config else "deepseek-chat"
        self.check_llm_connection = str(config.get("check_llm_connection", "true")).lower() == "true" if config else True

    def init_tiku(self) -> bool:
        return bool(self.api_key)

    def check_connection(self) -> bool:
        if not self.api_key:
            return False
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.model,
                "messages": [{"role": "user", "content": "你好"}],
                "max_tokens": 10
            }
            resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=data, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            loguru.logger.error(f"AI API连接失败: {e}")
            return False

    def check_llm_connection(self) -> bool:
        return self.check_connection()

    def query(self, question: dict) -> Optional[str]:
        title = question.get("title", "")
        options = question.get("options", "")
        q_type = question.get("type", "single")

        if not title or not self.api_key:
            return None

        cached = self.cache.get_cache(title)
        if cached:
            loguru.logger.debug(f"缓存命中: {title[:50]}...")
            return cached

        if self.query_delay > 0:
            time.sleep(self.query_delay)

        system_prompt = "你是一个答题助手，请根据题目和选项，只返回答案（不要解释）。对于单选题返回选项字母，对于多选题返回所有正确选项字母（按字母顺序排列），对于判断题返回true或false。"

        user_content = f"题目: {title}"
        if options:
            user_content += f"\n选项:\n{options}"

        if q_type == "multiple":
            user_content += "\n这是多选题，请返回所有正确选项字母。"
        elif q_type == "judgement":
            user_content += "\n这是判断题，请返回true或false。"
        elif q_type == "completion":
            user_content += "\n这是填空题，请返回答案内容。"

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                "temperature": 0.1,
                "max_tokens": 500
            }
            resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=data, timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                answer = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if answer:
                    self.cache.add_cache(title, answer)
                    return answer
        except Exception as e:
            loguru.logger.debug(f"AI答题失败: {e}")

        return None


class SiliconFlow(AI):
    def __init__(self, config: dict = None):
        super().__init__(config)
        self.base_url = config.get("base_url", "https://api.siliconflow.cn/v1") if config else "https://api.siliconflow.cn/v1"
        self.model = config.get("model", "Qwen/Qwen2.5-72B-Instruct") if config else "Qwen/Qwen2.5-72B-Instruct"


class TikuFallback(TikuBase):
    def __init__(self, config: dict = None):
        super().__init__(config)

    def init_tiku(self) -> bool:
        return True

    def check_connection(self) -> bool:
        return True

    def query(self, question: dict) -> Optional[str]:
        return None


class Tiku(TikuBase):
    TIKU_CLASSES = {
        "TikuYanxi": TikuYanxi,
        "TikuGo": TikuGo,
        "TikuLike": TikuLike,
        "TikuAdapter": TikuAdapter,
        "AI": AI,
        "SiliconFlow": SiliconFlow,
    }

    def __init__(self):
        self._tikus: List[TikuBase] = []
        self._active_tiku: Optional[TikuBase] = None
        self.DISABLE = True
        self.SUBMIT = False
        self.COVER_RATE = 0.8
        self._config = {}

    def config_set(self, config: Dict[str, Any]):
        self._config = config or {}

    def get_tiku_from_config(self) -> 'Tiku':
        if not self._config:
            self.DISABLE = True
            return self

        provider = self._config.get("provider", "")
        if not provider:
            self.DISABLE = True
            return self

        provider_list = [p.strip() for p in provider.split(",") if p.strip()]

        for provider_name in provider_list:
            tiku_class = self.TIKU_CLASSES.get(provider_name)
            if tiku_class:
                tiku = tiku_class(self._config)
                self._tikus.append(tiku)
                loguru.logger.info(f"已加载题库: {provider_name}")

        if self._tikus:
            self.DISABLE = False
            self._active_tiku = self._tikus[0]

        submit = str(self._config.get("submit", "false")).lower()
        self.SUBMIT = submit in ["true", "1", "yes", "y"]

        cover_rate = self._config.get("cover_rate", 0.8)
        try:
            self.COVER_RATE = float(cover_rate)
        except (TypeError, ValueError):
            self.COVER_RATE = 0.8

        return self

    def init_tiku(self) -> bool:
        if self.DISABLE:
            return False

        for tiku in self._tikus:
            if tiku.init_tiku():
                self._active_tiku = tiku
                return True

        return False

    def check_connection(self) -> bool:
        """连通性检查：委托当前 active provider；未就绪/全部不可用返回 False。

        原先该抽象方法未实现导致 ``Tiku()`` 实例化直接 TypeError，
        整个题库轮询路径在生产中被 try/except 静默掩盖，此处补齐。
        active provider 检查失败时按注册顺序继续尝试其余 provider，
        任一可用即切换为 active 并返回 True。
        """
        if self.DISABLE or not self._tikus:
            return False

        # active 优先，其余按注册顺序兜底
        candidates = list(self._tikus)
        if self._active_tiku in candidates:
            candidates.remove(self._active_tiku)
            candidates.insert(0, self._active_tiku)

        for tiku in candidates:
            try:
                if tiku.check_connection():
                    self._active_tiku = tiku
                    return True
            except Exception as e:
                loguru.logger.debug(f"题库 {tiku.name} 连通性检查异常: {e}")
        return False

    def check_llm_connection(self) -> bool:
        if not self._active_tiku:
            return False
        if hasattr(self._active_tiku, 'check_connection'):
            return self._active_tiku.check_connection()
        return True

    def query(self, question: dict) -> Optional[str]:
        if self.DISABLE or not self._tikus:
            return None

        for tiku in self._tikus:
            answer = tiku.query(question)
            if answer:
                loguru.logger.debug(f"从 {tiku.name} 获取答案: {answer[:50] if len(answer) > 50 else answer}...")
                return answer

        return None

    def get_submit_params(self) -> str:
        return "1" if self.SUBMIT else ""

    def judgement_select(self, answer: str) -> bool:
        if self._active_tiku:
            return self._active_tiku.judgement_select(answer)
        return random.choice([True, False])
