# _*_ coding:utf-8 _*_
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
        self.userid = userid or self.defaults.get("userid", "")
        self.cpi = self.defaults.get("cpi", "")

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
        return ses.cookies.get("fid", "1024")

    def _build_status_headers(self) -> dict:
        referer = (
            "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842"
            if self.dtype == "Audio"
            else "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842"
        )
        headers = {
            "Referer": referer,
            "User-Agent": self.headers.get("User-Agent", ""),
        }
        return headers

    def get_status(self, max_retries: int = 3) -> 'dict|None':
        fid = self._get_fid()
        status_url = "https://mooc1.chaoxing.com/ananas/status/{}?k={}&flag=normal&_dc={}".format(
            self.objectId, fid, int(time.time() * 1000)
        )
        headers = self._build_status_headers()
        loguru.logger.debug(f"获取视频状态: objectId={self.objectId}, fid={fid}")

        last_error = None
        for attempt in range(max_retries):
            try:
                resp = ses.get(status_url, headers=headers, timeout=8)
            except requests.RequestException as e:
                last_error = f"第{attempt+1}次请求失败: {e}"
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(1, 2))
                    continue
                loguru.logger.error(f"视频状态请求失败: objectId={self.objectId}, 重试{max_retries}次后仍失败")
                return None

            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(1, 2))
                    continue
                loguru.logger.error(f"视频状态HTTP错误: {resp.status_code}, objectId={self.objectId}")
                return None

            try:
                status_json = json.loads(resp.text)
                if status_json.get("status") == "success":
                    loguru.logger.debug(f"视频状态成功: duration={status_json.get('duration')}")
                    return status_json
                else:
                    last_error = f"status={status_json.get('status')}"
                    if attempt < max_retries - 1:
                        time.sleep(random.uniform(1, 2))
                        continue
                    loguru.logger.warning(f"视频状态返回非success: {status_json.get('status')}")
                    return status_json
            except json.JSONDecodeError as e:
                last_error = f"JSON解析失败: {e}, 响应: {resp.text[:200]}"
                if attempt < max_retries - 1:
                    time.sleep(random.uniform(1, 2))
                    continue
                loguru.logger.error(f"视频状态JSON解析失败: {e}, objectId={self.objectId}")
                return None

        return None

    def _check_is_passed(self, response_text: str) -> bool:
        try:
            if not response_text:
                return False
            result = json.loads(response_text)
            return bool(result.get("isPassed", False))
        except Exception:
            return False

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
        }
        return headers

    def _get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return hashlib.md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]"
            .encode()).hexdigest()

    def _video_progress_log(self, _dtoken, _duration, _playingTime, _isdrag: int = 3, headers=None) -> tuple:
        _video_log_rate_limit()

        if headers is None:
            headers = self._get_report_headers()

        otherInfo = self.attachment.get("otherInfo", "") or self.attachment.get("otherinfo", "")
        enc = self._get_enc(self.class_id, self.jobid, self.objectId, _playingTime, _duration, self.userid)

        params = {
            "clazzId": self.class_id,
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": self.objectId,
            "otherInfo": otherInfo,
            "courseId": self.course_id,
            "jobid": self.jobid,
            "userid": self.userid,
            "isdrag": _isdrag,
            "view": "pc",
            "enc": enc,
            "dtype": self.dtype
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{self.cpi}/"
            f"{_dtoken}"
        )

        if self.videoFaceCaptureEnc:
            params["videoFaceCaptureEnc"] = self.videoFaceCaptureEnc
        if self.attDuration:
            params["attDuration"] = self.attDuration
        if self.attDurationEnc:
            params["attDurationEnc"] = self.attDurationEnc

        rt = self._resolve_rt()
        if rt:
            loguru.logger.trace(f"Got rt: {rt}")
            params.update({"rt": rt, "_t": get_timestamp()})
            try:
                resp = ses.get(_url, params=params, headers=headers, timeout=8)
            except requests.RequestException as e:
                loguru.logger.debug(f"进度上报请求异常: {e}")
                return False, 0
        else:
            loguru.logger.warning("Failed to get rt")
            for rt_val in [0.9, 1]:
                params.update({"rt": str(rt_val), "_t": get_timestamp()})
                try:
                    resp = ses.get(_url, params=params, headers=headers, timeout=8)
                except requests.RequestException as e:
                    loguru.logger.debug(f"进度上报请求异常(rt={rt_val}): {e}")
                    continue
                if resp.status_code == 200:
                    loguru.logger.trace(resp.text)
                    return resp.json()["isPassed"], 200
                elif resp.status_code == 403:
                    loguru.logger.warning("出现403报错, 正常尝试切换rt")
                else:
                    loguru.logger.warning("未知错误 jobid={}, status_code={}, 摘要:\n{}",
                                   self.jobid, resp.status_code, resp.text[:200])
                    break
            return False, resp.status_code if 'resp' in dir() else 0

        if resp.status_code == 200:
            loguru.logger.trace(resp.text)
            return resp.json()["isPassed"], 200

        elif resp.status_code == 403:
            loguru.logger.debug(
                "视频进度上报返回403, jobid={}, 摘要={}",
                self.jobid,
                resp.text[:200],
            )
            return False, 403

        loguru.logger.error(f"未知错误: {resp.status_code}")
        loguru.logger.error("请求url:", resp.url)
        return False, resp.status_code

    def _refresh_video_status(self, _type) -> 'dict|None':
        headers = self._get_report_headers()
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{self.objectId}?"
            f"k={self._get_fid()}&flag=normal"
        )
        try:
            resp = ses.get(info_url, timeout=8, headers=headers)
        except requests.RequestException as exc:
            loguru.logger.debug("刷新视频状态失败: {}", exc)
            return None

        if resp.status_code != 200:
            loguru.logger.debug("刷新视频状态返回码异常: {}" % resp.status_code)
            loguru.logger.debug(resp.text)
            return None

        try:
            data = resp.json()
        except ValueError as exc:
            loguru.logger.debug("解析视频状态响应失败: {}", exc)
            return None

        if data.get("status") == "success":
            return data

        return None

    def _log(self, log, level: str, msg: str):
        if log:
            getattr(log, level, log.info)(msg)
        else:
            getattr(loguru.logger, level if level != "success" else "success", loguru.logger.info)(msg)

    def study(self, all_time: int = 0, log=None) -> bool:
        video_status = self.get_status()
        if not video_status:
            self._log(log, "error", f"视频 '{self.name}' 状态获取失败")
            return False

        if video_status.get("status") != "success":
            self._log(log, "error", f"视频 '{self.name}' 状态异常: {video_status.get('status')}")
            return False

        duration = video_status.get('duration')
        dtoken = video_status.get('dtoken')
        if not duration or not dtoken:
            self._log(log, "error", f"视频 '{self.name}' 状态无效: duration={duration}, dtoken={dtoken}")
            return False

        headers = self._get_report_headers()
        target_duration = duration if all_time == 0 else min(int(all_time) * 60, duration)
        play_time = int(self.attachment.get("playTime", 0)) // 1000
        play_time = min(play_time, target_duration)

        if play_time > 0:
            self._log(log, "info", f"视频 '{self.name}' 从 {int(play_time)} 秒处继续刷取")

        self._log(log, "info", f"视频 '{self.name}' 总时长: {duration}秒, 需要刷取: {target_duration}秒")

        passed, state = self._video_progress_log(dtoken, target_duration, target_duration, _isdrag=4, headers=headers)
        if passed:
            self._log(log, "success", f"视频 '{self.name}' 瞬间完成！")
            return True

        passed, state = self._video_progress_log(dtoken, target_duration, target_duration, _isdrag=4, headers=headers)
        if passed:
            self._log(log, "success", f"视频 '{self.name}' 瞬间完成！(重试)")
            return True

        self._log(log, "info", f"视频 '{self.name}' 需要正常刷取，开始进度上报...")

        last_log_time = 0
        last_iter = time.time()
        wait_time = int(random.uniform(30, 90))
        is_passed = False
        forbidden_retry = 0
        max_forbidden_retry = 2

        while not is_passed:
            if play_time - last_log_time >= wait_time or int(play_time) == target_duration:
                is_passed, state = self._video_progress_log(
                    dtoken, target_duration, int(play_time), _isdrag=3, headers=headers
                )

                if state == 403:
                    if forbidden_retry >= max_forbidden_retry:
                        self._log(log, "warning", f"视频 '{self.name}' 403重试失败，跳过")
                        return False
                    forbidden_retry += 1
                    self._log(log, "warning",
                              f"视频 '{self.name}' 出现403报错, 正在尝试刷新会话状态 (第{forbidden_retry}次)")
                    time.sleep(random.uniform(2, 4))
                    refreshed_meta = self._refresh_video_status(self.dtype)
                    if refreshed_meta:
                        dtoken = refreshed_meta.get("dtoken", dtoken)
                        duration = refreshed_meta.get("duration", duration)
                        target_duration = duration if all_time == 0 else min(int(all_time) * 60, duration)
                        play_time = refreshed_meta.get("playTime", play_time)
                        loguru.logger.debug(f"刷新后 dtoken={dtoken}, duration={duration}, play_time={play_time}")
                        continue

                elif not is_passed and state != 200:
                    self._log(log, "error", f"视频 '{self.name}' 上报异常 HTTP {state}")
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
            play_time = min(target_duration, play_time + dt)

            time.sleep(VIDEO_PROGRESS_INTERVAL)

        self._log(log, "success", f"视频 '{self.name}' 刷取完成！")
        return True

    def do_finish(self) -> bool:
        return self.study(all_time=0)
