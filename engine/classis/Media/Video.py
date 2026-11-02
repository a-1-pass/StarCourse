# _*_ coding:utf-8 _*_
"""视频/音频任务点刷取。

本模块在原实现基础上补齐：
1. 每个请求显式携带账号 Cookie，避免共享 requests.Session 的 cookie 竞争导致 403；
2. 上报请求分级重试（网络/超时/5xx/429/403/400/202 分别处理，指数退避 + 抖动）；
3. 正常播放使用 isdrag=0，终点使用 isdrag=4；仅 video-mode=0 保留「瞬间完成」；
4. duration/playingTime 统一取整后再参与 enc，避免浮点字符串导致签名不一致；
5. 失败时输出请求参数、HTTP 状态、平台响应摘要，便于定位。
"""
import hashlib
import json
import random
import re
import threading
import time

import loguru
import requests
from requests.adapters import HTTPAdapter

from . import Media
from utils import ses


VIDEO_PROGRESS_INTERVAL = 0.5
_VIDEO_LOG_WAIT_MIN = 2.0
_VIDEO_LOG_WAIT_MAX = 4.0
_video_log_lock = threading.Lock()
_last_video_log_time = 0.0

# 视频倍速配置（config.yml FunConfig.deal-mission.speed）的钳制范围
SPEED_MIN = 1.0
SPEED_MAX = 2.0
SPEED_DEFAULT = 1.0

# 到达目标时长后，平台仍不判定通过时的最大补报次数
FINISH_MAX_RETRIES = 5
# 单次进度上报内部的最大尝试次数（网络抖动/5xx/429/403 切换 rt）
REPORT_MAX_RETRIES = 4
# 状态接口的最大尝试次数（多域名 + 退避）
STATUS_MAX_RETRIES = 3
# 上报/状态请求超时：(连接超时, 读取超时)。弱网环境下比旧值 8s 更宽容
REQUEST_TIMEOUT = (5, 15)

# 状态接口：先走移动端 API 域名，再回退 PC 域名（与 yatori-go-core / yuban10703 一致）
STATUS_HOSTS = (
    "https://mooc1-api.chaoxing.com",
    "https://mooc1.chaoxing.com",
)


def should_give_up_finish(finish_reports: int, max_retries: int = FINISH_MAX_RETRIES) -> bool:
    """到达目标时长后仍未被判定通过时，是否应停止补报并放弃本轮。

    旧逻辑在 100% 后无上限重复上报，会让单个视频永久占用刷取线程，
    顺序/闯关模式下后续任务点因此全部处理不到。补报次数达到上限即放弃，
    交给同一轮内的失败任务点补刷。
    """
    return finish_reports >= max_retries


def normalize_play_time(raw, target_duration):
    """把状态接口返回的 playTime 归一化为秒，并钳制到 [0, target_duration]。

    平台可能返回毫秒（如 300000），也可能返回秒；超过目标时长的整数值按毫秒处理。
    无法解析时返回 None，调用方保留原进度。
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    if value < 0:
        return 0.0
    target = max(0.0, float(target_duration or 0.0))
    if value > target:
        value = value / 1000.0
    return max(0.0, min(target, value))


def normalize_duration(raw):
    """把平台返回的 duration 统一为整数秒；非法值返回 None。

    enc 签名里使用 ``duration * 1000``，若 duration 是浮点/字符串会生成
    ``300000.0`` 这类与平台不一致的签名，导致上报被判无效。
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0:  # NaN / 非正数
        return None
    return int(value)


def parse_is_passed(body):
    """解析上报响应中的 isPassed；无法解析返回 None，未通过返回 False。"""
    try:
        data = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("isPassed", False)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def restart_play_time_if_at_target(play_time, target_duration):
    """进度已到终点但平台仍未判定通过时，返回 0 表示从头重新上报。

    平台按上报进度累计学时；继续停在已上报过的终点重复上报不会产生新增量，
    视频会永远判不通过（常见于被打回的任务点）。其他开源实现的做法是
    「进度已到终点却未通过：从头重报」，否则会一直卡在终点。
    """
    try:
        target = float(target_duration or 0)
        value = float(play_time or 0)
    except (TypeError, ValueError):
        return play_time
    if target > 0 and value >= target:
        return 0.0
    return value


def resolve_speed(config_data=None) -> float:
    """解析视频倍速配置。

    - 上限钳制 2.0、下限钳制 1.0
    - 配置缺失 / 非法值（非数字、NaN 等）一律回退 1.0
    - config_data 缺省时读取全局 GloConfig（延迟导入避免循环依赖）
    """
    if config_data is None:
        try:
            from config import GloConfig
            config_data = GloConfig.data
        except Exception:
            config_data = None
    try:
        raw = (config_data or {}).get("FunConfig", {}).get("deal-mission", {}).get("speed", SPEED_DEFAULT)
        speed = float(raw)
    except (TypeError, ValueError, AttributeError):
        return SPEED_DEFAULT
    if speed != speed:  # NaN
        return SPEED_DEFAULT
    return min(SPEED_MAX, max(SPEED_MIN, speed))


def advance_play_time(play_time: float, dt: float, speed: float, target_duration: float) -> float:
    """按倍速推进上报进度（秒），并钳制到目标时长。

    speed=1.0 时与原行为（play_time + dt）完全一致；
    speed=2.0 时上报进度以 2 倍真实时间推进，等时长刷取的实际等待时间减半。
    """
    return min(target_duration, play_time + max(0.0, dt) * max(SPEED_MIN, speed))


def _backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """指数退避 + 随机抖动（attempt 从 1 开始）。"""
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay + random.uniform(0, 0.5)


def _retry_after_seconds(resp) -> float:
    """解析 429/503 的 Retry-After 或平台限流文案。"""
    try:
        header = (resp.headers or {}).get("Retry-After")
        if header:
            return max(1.0, float(header))
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        text = getattr(resp, "text", "") or ""
    except Exception:
        text = ""
    match = re.search(r"(?:available in|请等待|稍后重试)\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if match:
        try:
            return max(1.0, float(match.group(1)))
        except ValueError:
            pass
    return 0.0


def _video_log_rate_limit():
    global _last_video_log_time
    with _video_log_lock:
        elapsed = time.time() - _last_video_log_time
        wait = max(0.0, _VIDEO_LOG_WAIT_MIN + random.uniform(0, _VIDEO_LOG_WAIT_MAX - _VIDEO_LOG_WAIT_MIN) - elapsed)
        if wait > 0:
            time.sleep(wait)
        _last_video_log_time = time.time()


def get_timestamp():
    return str(int(time.time() * 1000))


class Video(Media):
    def __init__(self, attachment: dict, headers, defaults: dict, dtype: str = "Video", name: str = "", course_id: str = "", class_id: str = "", userid: str = ""):
        super().__init__(attachment, headers)
        self.objectId = attachment.get("objectId") or attachment.get("property", {}).get("objectId")
        self.reportUrl = defaults.get("reportUrl") if defaults else None
        self.defaults = defaults or {}
        self.dtype = dtype
        self.name = name
        property_dict = attachment.get("property", {})
        self.rt = property_dict.get("rt") if isinstance(property_dict, dict) else None
        self.attDuration = attachment.get("attDuration", "")
        self.attDurationEnc = attachment.get("attDurationEnc", "")
        self.videoFaceCaptureEnc = attachment.get("videoFaceCaptureEnc", "")
        self.course_id = course_id or self.defaults.get("courseId", "")
        self.class_id = class_id or self.defaults.get("clazzId", "")
        # userid / cpi 缺失时从 Cookie 兜底，避免 enc 签名缺少身份字段
        self.userid = userid or self.defaults.get("userid", "") or self._cookie_value("_uid", "UID", "uid")
        self.cpi = self.defaults.get("cpi", "") or self._cookie_value("cpi")
        # 最近一次进度上报的原始响应，供失败诊断时输出到日志
        self._last_report_text = ""
        self._last_report_params = {}
        self._last_error_detail = ""
        self._last_http_status = 0
        # 可选停止事件（由刷课引擎注入；None 时行为与历史完全一致）
        self._stop_event = None
        # 倍速（由刷课引擎按 config.yml 的 speed 注入，仅 video-mode 1 等时长刷取生效；
        # 默认 1.0 时进度推进与历史行为完全一致）
        self.speed = SPEED_DEFAULT
        # 是否允许「瞬间完成」（video-mode 0 专用）。等时长刷取由 run_video 置 False，
        # 避免未播放就拖到终点的 isdrag=4 请求被平台判定为异常。
        self.instant_finish = True

    def _stopped(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    def _sleep(self, seconds: float) -> bool:
        """可中断等待。返回 True 表示被停止中断。"""
        if self._stop_event is not None:
            return self._stop_event.wait(timeout=max(0.0, float(seconds or 0.0)))
        time.sleep(max(0.0, float(seconds or 0.0)))
        return False

    # ── 身份 / Cookie 兜底 ───────────────────────────────────

    def _cookie_value(self, *names) -> str:
        """从账号 Cookie 中取指定字段（fid/uid/cpi），共享 Session 被清空时仍可用。"""
        raw_cookie = ""
        try:
            raw_cookie = (self.headers or {}).get("Cookie", "") or ""
        except AttributeError:
            raw_cookie = ""
        for item in raw_cookie.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key.strip() in names:
                return value.strip()
        for name in names:
            try:
                value = ses.cookies.get(name)
            except Exception:
                value = None
            if value:
                return value
        return ""

    def _resolve_rt(self):
        if self.rt:
            return self.rt
        other_info = self.attachment.get("otherInfo", "") or self.attachment.get("otherinfo", "")
        rt_search = re.search(r"-rt_([1d])", other_info)
        if rt_search:
            rt_char = rt_search.group(1)
            self.rt = "0.9" if rt_char == "d" else "1"
        else:
            self.rt = "0.9"
        return self.rt

    def _get_fid(self) -> str:
        if self.defaults and self.defaults.get("fid"):
            return self.defaults.get("fid")
        if self.attachment and self.attachment.get("fid"):
            return self.attachment.get("fid")
        if self.attachment and isinstance(self.attachment.get("property"), dict):
            fid = self.attachment["property"].get("fid")
            if fid:
                return fid
        # 共享 Session 可能被其他线程清空，Cookie 兜底后再退回常用 fid
        return self._cookie_value("fid") or "9790"

    def _apply_status(self, status: dict):
        """把状态接口返回的最新字段同步到实例，供后续上报使用。"""
        if not isinstance(status, dict):
            return
        if status.get("rt"):
            self.rt = str(status.get("rt"))
        face_enc = status.get("videoFaceCaptureEnc")
        if face_enc:
            self.videoFaceCaptureEnc = face_enc
        if status.get("attDuration"):
            self.attDuration = status.get("attDuration")
        if status.get("attDurationEnc"):
            self.attDurationEnc = status.get("attDurationEnc")

    def _resume_play_time(self, target_duration: float) -> float:
        """解析附件里的续学位置。附件值可能为毫秒；不能直接 //1000。"""
        raw = self.attachment.get("playTime", 0)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if value <= 0:
            return 0.0
        if value > target_duration:
            value = value / 1000.0
        return max(0.0, min(float(target_duration), value))

    # ── 状态接口 ─────────────────────────────────────────────

    def _build_status_headers(self) -> dict:
        referer = (
            "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842"
            if self.dtype == "Audio"
            else "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842"
        )
        headers = {
            "Referer": referer,
            "User-Agent": self.headers.get("User-Agent", ""),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": self.headers.get("Cookie", ""),
        }
        return headers

    def get_status(self, max_retries: int = STATUS_MAX_RETRIES) -> 'dict|None':
        """获取视频状态（duration/dtoken/rt/人脸参数）。

        依次尝试 mooc1-api 与 mooc1 两个域名；网络异常、非 200、非 JSON、
        status!=success、缺少 duration/dtoken 都视为失败并退避重试。
        """
        if not self.objectId:
            self._last_error_detail = "attachment 缺少 objectId"
            loguru.logger.error("视频状态获取失败: attachment 缺少 objectId")
            return None

        fid = self._get_fid()
        headers = self._build_status_headers()
        loguru.logger.debug(f"获取视频状态: objectId={self.objectId}, fid={fid}")
        last_body = ""

        for attempt in range(1, max(1, int(max_retries)) + 1):
            for host in STATUS_HOSTS:
                status_url = "{}/ananas/status/{}?k={}&flag=normal&_dc={}".format(
                    host, self.objectId, fid, int(time.time() * 1000)
                )
                try:
                    resp = ses.get(status_url, headers=headers, timeout=REQUEST_TIMEOUT)
                except requests.RequestException as exc:
                    self._last_error_detail = f"状态请求异常({host}): {exc}"
                    loguru.logger.warning(
                        "视频状态请求异常(第%d/%d次, %s): %s", attempt, max_retries, host, exc)
                    continue

                if resp.status_code != 200:
                    last_body = (getattr(resp, "text", "") or "")[:120]
                    self._last_error_detail = f"状态接口 HTTP {resp.status_code}({host}): {last_body}"
                    loguru.logger.warning(
                        "视频状态HTTP错误(第%d/%d次, %s): %s",
                        attempt, max_retries, host, resp.status_code)
                    continue

                try:
                    status_json = json.loads(resp.text)
                except (ValueError, json.JSONDecodeError) as exc:
                    last_body = (getattr(resp, "text", "") or "")[:120]
                    self._last_error_detail = f"状态响应非JSON({host}): {exc}; {last_body}"
                    loguru.logger.warning("视频状态JSON解析失败: %s", exc)
                    continue

                if not isinstance(status_json, dict) or status_json.get("status") != "success":
                    last_body = json.dumps(status_json, ensure_ascii=False)[:120] if isinstance(status_json, dict) else last_body
                    self._last_error_detail = f"状态非success({host}): {last_body}"
                    loguru.logger.warning("视频状态返回非success: %s", last_body)
                    continue

                duration = normalize_duration(status_json.get("duration"))
                dtoken = status_json.get("dtoken")
                if not duration or not dtoken:
                    self._last_error_detail = (
                        f"状态字段无效: duration={status_json.get('duration')}, "
                        f"dtoken={'有' if dtoken else '无'}")
                    loguru.logger.warning("视频状态字段无效: duration=%s, dtoken=%s",
                                          status_json.get("duration"), bool(dtoken))
                    continue

                self._apply_status(status_json)
                self._last_error_detail = ""
                loguru.logger.debug("视频状态成功: duration=%s, rt=%s", duration, self._resolve_rt())
                return status_json

            if attempt < max_retries:
                if self._sleep(_backoff_delay(attempt, base=0.8)):
                    return None

        loguru.logger.error(
            "视频状态请求失败: objectId=%s, 重试%d次后仍失败, 最后错误=%s",
            self.objectId, max_retries, self._last_error_detail or "未知")
        return None

    # ── 进度上报 ─────────────────────────────────────────────

    def _get_report_headers(self) -> dict:
        referer = (
            "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842"
            if self.dtype == "Audio"
            else "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842"
        )
        headers = {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json',
            'Sec-Fetch-Dest': 'empty',
            'Host': 'mooc1.chaoxing.com',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Referer': referer,
            'User-Agent': self.headers.get("User-Agent", ""),
            # 显式携带账号 Cookie：共享 Session 被其他线程清空/重建时不会丢鉴权
            'Cookie': self.headers.get("Cookie", ""),
        }
        return headers

    def _get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        """MD5 签名：[clazzId][userid][jobid][objectId][playingMs][magic][durationMs][clipTime]。

        playingTime/duration 先取整，避免浮点数拼接成 ``300000.0`` 导致签名不一致。
        """
        playing_ms = int(round(float(playingTime) * 1000))
        duration_seconds = int(round(float(duration)))
        duration_ms = duration_seconds * 1000
        clip_time = f"0_{duration_seconds}"
        return hashlib.md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playing_ms}][d_yHJ!$pdA~5][{duration_ms}][{clip_time}]"
            .encode()).hexdigest()

    def _build_report_params(self, _dtoken, _duration, _playingTime, _isdrag: int = 0) -> dict:
        duration_seconds = int(round(float(_duration)))
        playing_seconds = int(round(float(_playingTime)))
        other_info = self.attachment.get("otherInfo", "") or self.attachment.get("otherinfo", "")
        params = {
            "clazzId": self.class_id,
            "playingTime": playing_seconds,
            "duration": duration_seconds,
            "clipTime": f"0_{duration_seconds}",
            "objectId": self.objectId,
            "otherInfo": other_info,
            "courseId": self.course_id,
            "jobid": self.jobid,
            "userid": self.userid,
            "isdrag": int(_isdrag),
            "view": "pc",
            "enc": self._get_enc(
                self.class_id, self.jobid, self.objectId, playing_seconds, duration_seconds, self.userid),
            "dtype": self.dtype,
        }

        if self.videoFaceCaptureEnc:
            params["videoFaceCaptureEnc"] = self.videoFaceCaptureEnc
        # attDuration 是平台校验时长一致性的字段；附件缺失时用真实 duration 兜底
        params["attDuration"] = self.attDuration or str(duration_seconds)
        if self.attDurationEnc:
            params["attDurationEnc"] = self.attDurationEnc
        return params

    def _report_url(self, _dtoken: str) -> str:
        return (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{self.cpi}/{_dtoken}"
        )

    def _video_progress_log(self, _dtoken, _duration, _playingTime, _isdrag: int = 0, headers=None) -> tuple:
        """上报一次观看进度，内置分级重试。

        返回值保持历史二元组 ``(isPassed, http_status)``：
        - 200：请求成功（isPassed 决定是否完成）
        - 403/400/202/429/5xx/0：请求失败，调用方按状态码决定刷新会话或放弃
        """
        _video_log_rate_limit()

        if headers is None:
            headers = self._get_report_headers()

        url = self._report_url(_dtoken)
        params = self._build_report_params(_dtoken, _duration, _playingTime, _isdrag)
        rt_primary = self._resolve_rt() or "0.9"
        rt_candidates = [rt_primary, "1" if rt_primary != "1" else "0.9"]
        rt_index = 0
        last_state = 0

        for attempt in range(1, REPORT_MAX_RETRIES + 1):
            if self._stopped():
                self._last_error_detail = "刷取已被停止"
                return False, last_state

            params["rt"] = rt_candidates[min(rt_index, len(rt_candidates) - 1)]
            params["_t"] = get_timestamp()

            try:
                resp = ses.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                last_state = 0
                self._last_http_status = 0
                self._last_error_detail = f"网络异常: {exc}"
                loguru.logger.warning(
                    "进度上报网络异常(第%d/%d次): jobid=%s, objectId=%s, "
                    "playingTime=%s/%s, isdrag=%s, rt=%s, error=%s",
                    attempt, REPORT_MAX_RETRIES, self.jobid, self.objectId,
                    params.get("playingTime"), params.get("duration"),
                    params.get("isdrag"), params.get("rt"), exc)
                if attempt < REPORT_MAX_RETRIES:
                    if self._sleep(_backoff_delay(attempt)):
                        return False, last_state
                    continue
                break

            code = resp.status_code
            last_state = code
            self._last_http_status = code
            text = getattr(resp, "text", "") or ""
            self._remember_report(params, text)

            if code == 200:
                passed = parse_is_passed(text)
                if passed is None:
                    self._last_error_detail = f"上报响应非JSON: {text[:120]}"
                    loguru.logger.warning(
                        "进度上报响应无法解析: jobid=%s, playingTime=%s, body=%s",
                        self.jobid, params.get("playingTime"), text[:120])
                    return False, 200
                if passed:
                    self._last_error_detail = ""
                else:
                    self._last_error_detail = f"平台未判定通过: {text[:120]}"
                return bool(passed), 200

            if code == 403:
                self._last_error_detail = f"HTTP 403: {text[:120]}"
                if rt_index < len(rt_candidates) - 1:
                    rt_index += 1
                    loguru.logger.warning(
                        "进度上报返回403，切换 rt=%s 重试(第%d/%d次): jobid=%s, playingTime=%s",
                        rt_candidates[rt_index], attempt, REPORT_MAX_RETRIES,
                        self.jobid, params.get("playingTime"))
                    if self._sleep(_backoff_delay(attempt)):
                        return False, last_state
                    continue
                loguru.logger.warning(
                    "进度上报连续403（可能触发人脸/会话校验）: jobid=%s, objectId=%s, "
                    "playingTime=%s, rt=%s, body=%s",
                    self.jobid, self.objectId, params.get("playingTime"),
                    params.get("rt"), text[:120])
                break

            if code in (400, 202):
                self._last_error_detail = f"HTTP {code}: {text[:120]}"
                loguru.logger.warning(
                    "进度上报返回%s（疑似会话失效，交由上层刷新状态/重登）: "
                    "jobid=%s, playingTime=%s, body=%s",
                    code, self.jobid, params.get("playingTime"), text[:120])
                break

            if code == 429:
                self._last_error_detail = f"HTTP 429 限流: {text[:120]}"
                delay = _retry_after_seconds(resp) or _backoff_delay(attempt, base=2.0)
                loguru.logger.warning(
                    "进度上报被限流，%.1fs 后重试(第%d/%d次): jobid=%s",
                    delay, attempt, REPORT_MAX_RETRIES, self.jobid)
                if attempt < REPORT_MAX_RETRIES:
                    if self._sleep(delay):
                        return False, last_state
                    continue
                break

            if code >= 500:
                self._last_error_detail = f"HTTP {code}: {text[:120]}"
                loguru.logger.warning(
                    "进度上报服务端错误(第%d/%d次): jobid=%s, HTTP=%s, body=%s",
                    attempt, REPORT_MAX_RETRIES, self.jobid, code, text[:120])
                if attempt < REPORT_MAX_RETRIES:
                    if self._sleep(_backoff_delay(attempt)):
                        return False, last_state
                    continue
                break

            # 其他状态码：重试一次以内，避免个别网关抖动直接失败
            self._last_error_detail = f"HTTP {code}: {text[:120]}"
            loguru.logger.warning(
                "进度上报未知状态(第%d/%d次): jobid=%s, HTTP=%s, body=%s",
                attempt, REPORT_MAX_RETRIES, self.jobid, code, text[:120])
            if attempt < min(2, REPORT_MAX_RETRIES):
                if self._sleep(_backoff_delay(attempt)):
                    return False, last_state
                continue
            break

        loguru.logger.error(
            "视频进度上报最终失败: jobid=%s, objectId=%s, playingTime=%s/%s, "
            "isdrag=%s, lastHTTP=%s, lastError=%s, lastBody=%s",
            self.jobid, self.objectId, params.get("playingTime"), params.get("duration"),
            params.get("isdrag"), last_state, self._last_error_detail,
            self._last_report_text[:120])
        return False, last_state

    def _refresh_video_status(self, _type=None) -> 'dict|None':
        """重新拉取状态接口（获取新 dtoken / rt / 人脸参数 / playTime）。"""
        data = self.get_status(max_retries=2)
        if data:
            self._apply_status(data)
        return data

    def _remember_report(self, params: dict, body: str):
        """记录最近一次上报参数与响应，失败时随日志输出便于对照排查。"""
        self._last_report_params = {
            k: params.get(k) for k in (
                "jobid", "objectId", "playingTime", "duration", "isdrag", "rt", "_t",
            )
        }
        self._last_report_text = (body or "")[:300]

    def _log(self, log, level: str, msg: str):
        if log:
            getattr(log, level, log.info)(msg)
        else:
            getattr(loguru.logger, level if level != "success" else "success", loguru.logger.info)(msg)

    def _finish_retry_delay(self, finish_reports: int) -> float:
        """终点补报的退避：1s 起逐次 +1s，叠加抖动，避免 0.5s 连发被判异常。"""
        return min(6.0, 1.0 + float(finish_reports)) + random.uniform(0, 1.0)

    def study(self, all_time: int = 0, log=None) -> bool:
        if self._stopped():
            self._log(log, "warning", "视频刷取已被停止")
            return False
        video_status = self.get_status()
        if not video_status:
            detail = self._last_error_detail or "接口无有效响应"
            self._log(log, "error", f"视频 '{self.name}' 状态获取失败：{detail}")
            return False

        if video_status.get("status") != "success":
            self._log(log, "error", f"视频 '{self.name}' 状态异常: {video_status.get('status')}")
            return False

        duration = normalize_duration(video_status.get('duration'))
        dtoken = video_status.get('dtoken')
        if not duration or not dtoken:
            self._log(log, "error",
                      f"视频 '{self.name}' 状态无效: duration={duration}, dtoken={dtoken}")
            return False

        self._apply_status(video_status)
        headers = self._get_report_headers()
        try:
            target_duration = duration if all_time == 0 else min(int(all_time) * 60, duration)
        except (TypeError, ValueError):
            target_duration = duration
        if target_duration <= 0:
            self._log(log, "error", f"视频 '{self.name}' 目标时长无效: {target_duration}")
            return False

        play_time = self._resume_play_time(target_duration)
        if play_time >= target_duration:
            # 附件记录的位置已在终点但任务点仍未通过（被打回），
            # 继续从终点上报不会产生新的学时增量，必须从头重新上报
            self._log(log, "warning",
                      f"视频 '{self.name}' 进度已在终点({int(play_time)}秒)但平台未判定通过，从头重新上报")
            play_time = restart_play_time_if_at_target(play_time, target_duration)
        elif play_time > 0:
            self._log(log, "info", f"视频 '{self.name}' 从 {int(play_time)} 秒处继续刷取")

        self._log(log, "info", f"视频 '{self.name}' 总时长: {duration}秒, 需要刷取: {target_duration}秒")

        # ── 瞬间完成（仅 video-mode 0）────────────────────────
        # 参考实现中正常播放不会一上来就 isdrag=4 拖到终点；等时长模式由
        # DealVideo.run_video 置 instant_finish=False，避免被风控判定为拖拽。
        if getattr(self, "instant_finish", True):
            for attempt in range(1, 3):
                if self._stopped():
                    self._log(log, "warning", f"视频 '{self.name}' 刷取已停止")
                    return False
                passed, state = self._video_progress_log(
                    dtoken, target_duration, target_duration, _isdrag=4, headers=headers
                )
                if passed:
                    self._log(log, "success", f"视频 '{self.name}' 瞬间完成！")
                    return True
                loguru.logger.debug(
                    "瞬间完成未通过(第%d次): jobid=%s, HTTP=%s, detail=%s",
                    attempt, self.jobid, state, self._last_error_detail)

        self._log(log, "info", f"视频 '{self.name}' 需要正常刷取，开始进度上报...")

        last_log_time = 0
        last_iter = time.time()
        wait_time = int(random.uniform(30, 90))
        is_passed = False
        forbidden_retry = 0
        max_forbidden_retry = 2
        session_retry = 0
        max_session_retry = 2
        # 到达目标时长后的补报计数：平台迟迟不判定通过时，必须在上限内放弃，
        # 否则单个视频会永久占用线程，后续任务点全部处理不到
        finish_reports = 0

        while not is_passed:
            if self._stopped():
                self._log(log, "warning", f"视频 '{self.name}' 刷取已停止")
                return False
            at_target = int(play_time) >= int(target_duration)
            if at_target and should_give_up_finish(finish_reports):
                # 先复核平台状态：判定的落地可能滞后于上报响应，避免误报失败
                refreshed = self._refresh_video_status(self.dtype)
                if refreshed and refreshed.get("isPassed"):
                    self._log(log, "success", f"视频 '{self.name}' 刷取完成！（状态复核确认通过）")
                    return True
                body = f"；平台响应: {self._last_report_text}" if self._last_report_text else ""
                detail = f"；最后错误: {self._last_error_detail}" if self._last_error_detail else ""
                self._log(log, "warning",
                          f"视频 '{self.name}' 到达目标时长后仍未判定通过，"
                          f"放弃本轮（已补报 {finish_reports} 次, HTTP {self._last_http_status or state}, "
                          f"jobid={self.jobid}, playingTime={int(play_time)}/{target_duration}"
                          f"{body}{detail}）")
                return False
            if at_target or play_time - last_log_time >= wait_time:
                # 正常播放 isdrag=0；只有到达终点才用 isdrag=4
                isdrag = 4 if at_target else 0
                is_passed, state = self._video_progress_log(
                    dtoken, target_duration, int(play_time), isdrag, headers=headers
                )
                if at_target:
                    finish_reports += 1

                if state == 403:
                    if forbidden_retry >= max_forbidden_retry:
                        self._log(log, "warning",
                                  f"视频 '{self.name}' 403重试失败，跳过；"
                                  f"最后错误: {self._last_error_detail}")
                        return False
                    forbidden_retry += 1
                    self._log(log, "warning",
                              f"视频 '{self.name}' 出现403报错, 正在尝试刷新会话状态 (第{forbidden_retry}次)")
                    if self._sleep(random.uniform(2, 4)):
                        self._log(log, "warning", f"视频 '{self.name}' 刷取已停止")
                        return False
                    refreshed_meta = self._refresh_video_status(self.dtype)
                    if refreshed_meta:
                        dtoken = refreshed_meta.get("dtoken", dtoken)
                        duration = normalize_duration(refreshed_meta.get("duration")) or duration
                        target_duration = duration if all_time == 0 else min(int(all_time) * 60, duration)
                        # 状态接口的 playTime 可能是毫秒，直接赋值会让上报参数失真
                        normalized_play_time = normalize_play_time(
                            refreshed_meta.get("playTime"), target_duration)
                        if normalized_play_time is not None:
                            play_time = restart_play_time_if_at_target(normalized_play_time, target_duration)
                        loguru.logger.debug(f"刷新后 dtoken={dtoken}, duration={duration}, play_time={play_time}")
                        continue
                    self._log(log, "error",
                              f"视频 '{self.name}' 403后刷新状态失败: {self._last_error_detail}")
                    return False

                if state in (400, 202):
                    if session_retry >= max_session_retry:
                        self._log(log, "error",
                                  f"视频 '{self.name}' 会话失效（HTTP {state}）且刷新无效，"
                                  f"最后错误: {self._last_error_detail}")
                        return False
                    session_retry += 1
                    self._log(log, "warning",
                              f"视频 '{self.name}' 会话失效(HTTP {state})，"
                              f"刷新状态重试 (第{session_retry}次)")
                    if self._sleep(_backoff_delay(session_retry, base=1.0)):
                        return False
                    refreshed_meta = self._refresh_video_status(self.dtype)
                    if refreshed_meta:
                        dtoken = refreshed_meta.get("dtoken", dtoken)
                        duration = normalize_duration(refreshed_meta.get("duration")) or duration
                        target_duration = duration if all_time == 0 else min(int(all_time) * 60, duration)
                        normalized_play_time = normalize_play_time(
                            refreshed_meta.get("playTime"), target_duration)
                        if normalized_play_time is not None:
                            play_time = restart_play_time_if_at_target(normalized_play_time, target_duration)
                        continue
                    self._log(log, "error",
                              f"视频 '{self.name}' 会话失效后刷新状态失败: {self._last_error_detail}")
                    return False

                elif not is_passed and state != 200:
                    self._log(log, "error",
                              f"视频 '{self.name}' 上报异常 HTTP {state}；"
                              f"最后错误: {self._last_error_detail or '未知'}")
                    return False

                progress_percent = round(play_time / target_duration * 100, 1)
                self._log(log, "info",
                          f"视频 '{self.name}' 进度: {progress_percent}% "
                          f"({int(play_time)}/{target_duration}秒)")

                if is_passed:
                    self._log(log, "success", f"视频 '{self.name}' 刷取完成！")
                    return True

                wait_time = int(random.uniform(30, 90))
                last_log_time = play_time

                loguru.logger.trace("Progress logged")

            dt = time.time() - last_iter
            last_iter = time.time()
            play_time = advance_play_time(play_time, dt, self.speed, target_duration)

            # 终点补报之间退避；正常播放保持 0.5s 的本地进度推进节奏
            sleep_seconds = (
                self._finish_retry_delay(finish_reports)
                if (at_target and not is_passed)
                else VIDEO_PROGRESS_INTERVAL
            )
            if self._sleep(sleep_seconds):
                self._log(log, "warning", f"视频 '{self.name}' 刷取已停止")
                return False

        self._log(log, "success", f"视频 '{self.name}' 刷取完成！")
        return True

    def do_finish(self) -> bool:
        try:
            return self.study(all_time=0)
        except Exception as exc:
            # 兼容 video-mode=0 的同步调用路径：异常不能让整个任务点静默丢失
            loguru.logger.error(
                "视频 '%s' 刷取异常: %s", self.name, exc, exc_info=True)
            return False
