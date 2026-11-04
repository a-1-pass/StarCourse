# _*_ coding:utf-8 _*_
"""视频刷取稳定性测试：分级重试、反检测参数、会话兜底与弱网场景。

覆盖：
- 网络异常 / 超时自动重试；5xx 指数退避重试；429 按 Retry-After 等待
- 403 切换 rt 重试；400/202 交给上层刷新会话
- 参考实现（yatori-go-core）：正常播放统一 isdrag=0，到达终点后 playingTime 逐次 +1
- 状态接口多域名重试与 Cookie 兜底
- duration/playingTime 取整后参与 enc，避免签名不一致
- 等时长刷取固定 1 倍速（忽略配置倍速）
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

import classis.Media.Video as video_mod
from classis.Media.Video import REPORT_MAX_RETRIES, Video
from functions.set_time import DealVideo


def _video(cookie="fid=9790; _uid=123; cpi=CPI", duration=600) -> Video:
    video = Video(
        {"objectId": "OID", "jobid": "job", "attDuration": "", "property": {}},
        {"Cookie": cookie, "User-Agent": "UA"},
        {"clazzId": "CL", "courseId": "CID", "cpi": "CPI"},
        name="测试视频",
    )
    video.jobid = "job"
    video.userid = "123"
    return video


class FakeResp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSes:
    """按脚本依次返回响应或抛异常，并记录每次请求参数。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, **_kw):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}), "timeout": timeout})
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """禁用真实等待与日志限流，避免测试变慢。"""
    monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
    monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)


class TestNetworkRetry:
    def test_network_error_then_success(self, monkeypatch):
        video = _video()
        ses = FakeSes([
            requests.exceptions.ConnectionError("网络波动"),
            FakeResp(200, '{"isPassed": true}'),
        ])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (True, 200)
        assert len(ses.calls) == 2  # 首次失败后自动重试

    def test_all_network_errors_give_up_after_budget(self, monkeypatch):
        video = _video()
        ses = FakeSes([requests.exceptions.Timeout("超时")])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (False, 0)
        assert len(ses.calls) == REPORT_MAX_RETRIES
        assert "网络异常" in video._last_error_detail

    def test_server_error_then_success(self, monkeypatch):
        video = _video()
        ses = FakeSes([FakeResp(502, "bad gateway"), FakeResp(200, '{"isPassed": true}')])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (True, 200)
        assert len(ses.calls) == 2

    def test_rate_limited_waits_and_retries(self, monkeypatch):
        video = _video()
        ses = FakeSes([
            FakeResp(429, "too many", {"Retry-After": "1"}),
            FakeResp(200, '{"isPassed": true}'),
        ])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (True, 200)
        assert len(ses.calls) == 2

    def test_unknown_status_retries_once(self, monkeypatch):
        video = _video()
        ses = FakeSes([FakeResp(418, "teapot")])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (False, 418)
        assert len(ses.calls) == 2


class TestForbiddenRetry:
    def test_403_switches_rt_and_recovers(self, monkeypatch):
        video = _video()
        video.rt = "0.9"
        ses = FakeSes([FakeResp(403, "forbidden"), FakeResp(200, '{"isPassed": true}')])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (True, 200)
        assert ses.calls[0]["params"]["rt"] != ses.calls[1]["params"]["rt"]

    def test_400_returns_immediately_for_session_refresh(self, monkeypatch):
        video = _video()
        ses = FakeSes([FakeResp(400, "session expired")])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (False, 400)
        assert len(ses.calls) == 1  # 400 交由上层刷新状态，不在内部空转

    def test_202_returns_immediately(self, monkeypatch):
        video = _video()
        ses = FakeSes([FakeResp(202, "accepted")])
        monkeypatch.setattr(video_mod, "ses", ses)

        passed, state = video._video_progress_log("dt", 600, 100, 0)
        assert (passed, state) == (False, 202)
        assert len(ses.calls) == 1


class TestStatusRetry:
    def test_status_recovers_after_network_errors(self, monkeypatch):
        video = _video()
        ok_body = json.dumps({"status": "success", "duration": 600.0, "dtoken": "DT"})
        ses = FakeSes([
            requests.exceptions.ConnectionError("断网"),
            requests.exceptions.ConnectionError("断网"),
            FakeResp(200, ok_body),
        ])
        monkeypatch.setattr(video_mod, "ses", ses)

        status = video.get_status()
        assert status["dtoken"] == "DT"
        assert len(ses.calls) == 3
        # 前两次命中不同域名（mooc1-api → mooc1），验证双域名回退
        assert "mooc1-api.chaoxing.com" in ses.calls[0]["url"]
        assert "mooc1-api.chaoxing.com" not in ses.calls[1]["url"]

    def test_status_rejects_non_success_and_missing_dtoken(self, monkeypatch):
        video = _video()
        ses = FakeSes([
            FakeResp(200, '{"status": "fail"}'),
            FakeResp(200, '{"status": "success", "duration": 600}'),
            FakeResp(200, '{"status": "success", "duration": 600, "dtoken": "DT"}'),
        ])
        monkeypatch.setattr(video_mod, "ses", ses)

        assert video.get_status()["dtoken"] == "DT"
        assert len(ses.calls) == 3

    def test_status_uses_request_timeout(self, monkeypatch):
        video = _video()
        ses = FakeSes([FakeResp(200, '{"status": "success", "duration": 600, "dtoken": "DT"}')])
        monkeypatch.setattr(video_mod, "ses", ses)

        video.get_status()
        assert ses.calls[0]["timeout"] == video_mod.REQUEST_TIMEOUT


class TestAntiDetectionParams:
    def test_cookie_passed_explicitly(self):
        video = _video()
        assert video._get_report_headers()["Cookie"] == "fid=9790; _uid=123; cpi=CPI"
        assert video._build_status_headers()["Cookie"] == "fid=9790; _uid=123; cpi=CPI"

    def test_report_params_are_integers_and_enc_consistent(self, monkeypatch):
        video = _video()
        params = video._build_report_params("dt", 600.0, 300.4, 0)
        assert params["duration"] == 600
        assert params["playingTime"] == 300
        assert params["clipTime"] == "0_600"
        assert "attDuration" not in params  # 附件缺失时不伪造 attDuration
        assert params["enc"] == video._get_enc("CL", "job", "OID", 300, 600, "123")

    def test_enc_float_equals_integer(self):
        video = _video()
        # 整数值的浮点（300.0）必须与整数签名完全一致，不能出现 "300000.0"
        assert video._get_enc("CL", "job", "OID", 100.0, 600.0, "123") == \
            video._get_enc("CL", "job", "OID", 100, 600, "123")
        assert ".0" not in video._get_enc("CL", "job", "OID", 100.0, 600.0, "123")

    def test_audio_dtype_headers_and_params(self):
        video = Video(
            {"objectId": "OID", "jobid": "job"},
            {"Cookie": "fid=1", "User-Agent": "UA"},
            {"clazzId": "CL", "courseId": "CID", "cpi": "CPI"},
            dtype="Audio", name="音频",
        )
        video.userid = "123"
        assert "modules/audio" in video._get_report_headers()["Referer"]
        assert video._build_report_params("dt", 600, 10, 0)["dtype"] == "Audio"

    def test_cookie_fallback_for_identity(self, monkeypatch):
        video = Video(
            {"objectId": "OID"},
            {"Cookie": "fid=9790; _uid=456", "User-Agent": "UA"},
            {},
        )
        assert video.userid == "456"
        assert video._get_fid() == "9790"


class TestStudyIsdrag:
    def test_playback_uses_isdrag_zero_and_end_time_increments(self, monkeypatch):
        video = _video(duration=5)
        reported = []

        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 5, "dtoken": "DT"})
        monkeypatch.setattr(video_mod, "advance_play_time",
                            lambda p, _dt, _s, target: min(float(target), p + 1))
        monkeypatch.setattr(video_mod, "random", SimpleNamespace(uniform=lambda a, b: 0))

        def fake_progress(_dtoken, _duration, playing_time, isdrag=0, headers=None):
            reported.append((playing_time, isdrag))
            # 前两条终点上报仍是 false，第 3 条（playingTime=7）才判定通过
            return int(playing_time) >= 7, 200

        monkeypatch.setattr(video, "_video_progress_log", fake_progress)

        assert video.study() is True
        # 参考实现：全部用 isdrag=0 正常播放；到终点后 playingTime 每次 +1
        assert {d for _p, d in reported} == {0}
        end_reports = [p for p, _d in reported if p >= 5]
        assert end_reports[:3] == [5, 6, 7]

    def test_run_video_forces_one_x_speed(self, monkeypatch):
        video = _video(duration=10)
        monkeypatch.setattr(video_mod, "resolve_speed", lambda *_a, **_k: 1.5)
        monkeypatch.setattr(video, "study", lambda **_k: True)

        assert DealVideo.run_video(video, MagicMock(), MagicMock()) is True
        # 正常刷取固定 1 倍速，忽略配置里的倍速
        assert video.speed == 1.0


class TestStudyPacing:
    """进度必须按真实时间 1:1 推进：倍速会让人造进度快于墙钟，
    平台按墙钟核算学时会丢弃超额部分，导致 playingTime 报满仍不通过。"""

    def test_progress_advances_at_real_time_even_if_speed_gt_1(self, monkeypatch):
        video = _video(duration=30)
        video.speed = 2.0
        speeds = []

        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 30, "dtoken": "DT"})
        monkeypatch.setattr(video_mod, "random", SimpleNamespace(uniform=lambda a, b: 0))

        def fake_advance(play_time, _dt, speed, target):
            speeds.append(speed)
            return min(float(target), play_time + 1)

        monkeypatch.setattr(video_mod, "advance_play_time", fake_advance)
        monkeypatch.setattr(
            video, "_video_progress_log",
            lambda *a, **k: (a[3] != 4 and int(a[2]) >= 30, 200))

        assert video.study() is True
        assert speeds and set(speeds) == {1.0}

    def test_gives_up_after_bounded_finish_retries(self, monkeypatch):
        """平台始终不判通过时，补满 FINISH_MAX_RETRIES 次即复核放弃，避免永久占用线程。"""
        video = _video(duration=10)

        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 10, "dtoken": "DT"})
        monkeypatch.setattr(video_mod, "advance_play_time",
                            lambda _p, _dt, _s, target: float(target))
        monkeypatch.setattr(video_mod, "random", SimpleNamespace(uniform=lambda a, b: 0))
        monkeypatch.setattr(video, "_refresh_video_status", lambda *a, **k: None)

        calls = []
        monkeypatch.setattr(
            video, "_video_progress_log",
            lambda *a, **k: (calls.append(a[3] if len(a) > 3 else k.get("isdrag")) or (False, 200)))

        assert video.study() is False
        # 播放位首报 1 次 + 终点补报 FINISH_MAX_RETRIES 次
        assert len(calls) == 1 + video_mod.FINISH_MAX_RETRIES


class TestThreadLocalSession:
    """doGet 使用线程本地 Session：全局 ses 不再被清空，杜绝并发 403。"""

    def test_doget_does_not_touch_global_session(self, monkeypatch):
        import utils

        class FakeResp:
            status_code = 200
            text = "ok"

            def close(self):
                pass

        local_ses = utils._thread_session()
        monkeypatch.setattr(local_ses, "get", lambda **_kw: FakeResp())
        monkeypatch.setattr(utils, "jitter_sleep", lambda *a, **k: None)

        utils.ses.headers.clear()
        utils.ses.headers.update({"X-Marker": "keep-me"})
        utils.ses.cookies.set("stale", "1")

        assert utils.doGet("https://example.com", headers={"Cookie": "a=1"}) == "ok"
        # 全局 ses 的 headers/cookies 未被 doGet 清空或重建
        assert utils.ses.headers.get("X-Marker") == "keep-me"
        assert utils.ses.cookies.get("stale") == "1"

    def test_thread_session_isolated_across_threads(self):
        import threading
        import utils

        main_ses = utils._thread_session()
        other = []

        def worker():
            other.append(utils._thread_session())

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert other and other[0] is not main_ses
        assert utils._thread_session() is main_ses
