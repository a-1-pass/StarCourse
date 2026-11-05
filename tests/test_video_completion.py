# _*_ coding:utf-8 _*_
"""视频刷取「一次刷完所有任务点」相关缺陷测试。

覆盖的核心问题：
- 到达目标时长后若平台仍不判定通过，旧逻辑会无上限重复上报，导致该视频
  永久占用（顺序/闯关模式下后续任务点全部无法处理）。
- 进度上报响应缺少 isPassed 字段时抛 KeyError，视频线程直接崩溃。
- 状态刷新返回的 playTime 单位可能是毫秒，未归一化会污染上报参数。
- 闯关模式下视频异步启动，章节在视频未完成时被判 done，后续任务点不解锁。
- 失败的任务点没有同一轮内的补刷机会。
- 视频线程异常未被捕获，任务点静默丢失。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import classis.Media.Video as video_mod  # noqa: E402
import functions.deal_mission.deal_course as dc_mod  # noqa: E402
from classis.Media.Video import Video, normalize_play_time  # noqa: E402


# ════════════════════════════════════════════════════════════
# 辅助
# ════════════════════════════════════════════════════════════

def _video(play_time_ms: int = 0, duration: int = 10) -> Video:
    """构造不带网络依赖的 Video 实例（attachment.playTime 为毫秒）。"""
    return Video(
        {"objectId": "oid", "jobid": "job", "playTime": play_time_ms},
        {"Cookie": "", "User-Agent": "UA"},
        {},
        name="测试视频",
    )


def _patch_video_io(monkeypatch, video: Video, progress_results, duration=10):
    """替换 study() 的所有 IO：状态、进度上报、等待、进度推进。"""
    monkeypatch.setattr(
        video, "get_status",
        lambda *a, **k: {"status": "success", "duration": duration, "dtoken": "dt"})
    calls = []

    def fake_progress(*_a, **_k):
        calls.append(1)
        idx = min(len(calls) - 1, len(progress_results) - 1)
        return progress_results[idx]

    monkeypatch.setattr(video, "_video_progress_log", fake_progress)
    monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
    monkeypatch.setattr(video_mod, "advance_play_time",
                        lambda _p, _dt, _s, target: float(target))
    monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)
    return calls


# ════════════════════════════════════════════════════════════
# 403 重试上限（循环不会无限制撞 403）
# ════════════════════════════════════════════════════════════

class TestForbiddenRetry:
    def _setup(self, monkeypatch, progress_seq):
        video = _video(play_time_ms=10 * 1000, duration=10)
        video._last_error_detail = "模拟 403"
        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 10, "dtoken": "dt"})
        monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
        monkeypatch.setattr(video_mod, "advance_play_time",
                            lambda _p, _dt, _s, target: float(target))
        monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)
        monkeypatch.setattr(video, "_refresh_video_status",
                            lambda *a, **k: None)  # 刷新状态返回 None → 直接失败
        calls = []

        def fake_progress(*_a, **_k):
            calls.append(1)
            idx = min(len(calls) - 1, len(progress_seq) - 1)
            return progress_seq[idx]

        monkeypatch.setattr(video, "_video_progress_log", fake_progress)
        return video, calls

    def test_gives_up_after_repeated_403(self, monkeypatch):
        """403 多次出现后必须放弃，不能无限重试。"""
        video, calls = self._setup(monkeypatch, [(False, 403), (False, 403), (False, 403)])
        assert video.study() is False
        # 1 次 isdrag=4 + 2 次循环心跳（每次 403 各触发一次刷新 → 再次心跳失败后退出）
        assert len(calls) <= 1 + 3  # 上限：isdrag=4 + 两次 403 + 第三次 403 后退出

    def test_403_then_recover_with_status_refresh(self, monkeypatch):
        """403 刷新状态后恢复 → 继续心跳直到通过。"""
        video = _video(play_time_ms=10 * 1000, duration=10)
        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 10, "dtoken": "dt"})
        monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
        monkeypatch.setattr(video_mod, "advance_play_time",
                            lambda _p, _dt, _s, target: float(target))
        monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)
        # 刷新状态后成功，之后心跳返回通过
        refreshed_called = [0]
        def fake_refresh(*_a, **_k):
            refreshed_called[0] += 1
            return {"status": "success", "duration": 10, "dtoken": "dt"}
        monkeypatch.setattr(video, "_refresh_video_status", fake_refresh)
        calls = []
        def fake_progress(*_a, **_k):
            calls.append(1)
            # 第一次 isdrag=4 false；循环里第一次 403；之后一直 true
            if len(calls) == 1:
                return False, 200  # isdrag=4 失败
            if len(calls) == 2:
                return False, 403  # isdrag=3 心跳 403 → 触发刷新
            return True, 200  # 刷新后心跳通过
        monkeypatch.setattr(video, "_video_progress_log", fake_progress)
        assert video.study() is True
        assert refreshed_called[0] == 1


# ════════════════════════════════════════════════════════════
# 进度已到终点但未通过：持续心跳直到通过（无上限）
# ════════════════════════════════════════════════════════════

class TestFinishReporting:
    def test_isdrag_four_then_three_at_duration(self, monkeypatch):
        """流程：先 isdrag=4 直接提交终点；未通过则 while-not-passed + isdrag=3 持续心跳。"""
        video = _video(play_time_ms=10 * 1000, duration=10)
        reported = []

        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 10, "dtoken": "dt"})
        monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
        monkeypatch.setattr(video_mod, "advance_play_time",
                            lambda p, _dt, _s, target: min(float(target), p + 1))
        monkeypatch.setattr(video_mod, "random", SimpleNamespace(uniform=lambda a, b: 0))
        monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)

        def fake_progress(_dtoken, _duration, playing_time, isdrag=3, *_a, **_k):
            reported.append((playing_time, isdrag))
            # 先 isdrag=4 必 false；循环里 isdrag=3 + 第 11 次才 pass
            return isdrag == 3 and len([r for r in reported if r[1] == 3]) >= 10, 200

        monkeypatch.setattr(video, "_video_progress_log", fake_progress)

        assert video.study() is True
        # 先 1 次 isdrag=4 直接提交终点
        assert reported[0] == (10, 4)
        # 循环里 isdrag=3，playing_time 涨到 10 后持续报 10
        isdrag_3_reports = [p for p, d in reported[1:] if d == 3]
        assert all(p == 10 for p in isdrag_3_reports)


# ════════════════════════════════════════════════════════════
# 放弃前的状态复核与诊断信息
# ════════════════════════════════════════════════════════════

class _Recorder:
    def __init__(self):
        self.messages = []

    def info(self, m): self.messages.append(("info", m))
    def warning(self, m): self.messages.append(("warning", m))
    def error(self, m): self.messages.append(("error", m))
    def success(self, m): self.messages.append(("success", m))


class TestFinishDiagnostics:
    """诊断相关：403 放弃错误时仍应带详细信息。"""

    def test_give_up_logs_platform_response_on_403(self, monkeypatch):
        """403 多次重试后放弃时，日志应带出平台响应便于排查。"""
        video = _video(play_time_ms=10 * 1000, duration=10)
        video._last_report_text = '{"isPassed":false,"msg":"学习时长异常"}'
        video._last_report_params = {"jobid": "job", "playingTime": 10, "duration": 10}
        rec = _Recorder()
        monkeypatch.setattr(
            video, "get_status",
            lambda *a, **k: {"status": "success", "duration": 10, "dtoken": "dt"})

        def fake_progress(*_a, **_k):
            return False, 403

        monkeypatch.setattr(video, "_video_progress_log", fake_progress)
        monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
        monkeypatch.setattr(video_mod, "advance_play_time",
                            lambda _p, _dt, _s, target: float(target))
        monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)
        monkeypatch.setattr(video, "_refresh_video_status", lambda *a, **k: None)

        assert video.study(log=rec) is False
        msgs = [m for _lvl, m in rec.messages]
        assert any("403" in m for m in msgs)


# ════════════════════════════════════════════════════════════
# 进度上报响应健壮性
# ════════════════════════════════════════════════════════════

class TestProgressResponse:
    def test_missing_ispassed_field_returns_false(self, monkeypatch):
        """响应缺少 isPassed 时返回 False 而不是抛 KeyError。"""
        video = _video()
        video.jobid = "job"
        video.class_id = "CL"
        video.course_id = "CID"
        video.userid = "U"
        video.cpi = "CPI"

        class FakeResp:
            status_code = 200
            text = "{}"

            def json(self):
                return {}

        class FakeSes:
            def get(self, *_a, **_k):
                return FakeResp()

        monkeypatch.setattr(video_mod, "ses", FakeSes())
        monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)

        passed, state = video._video_progress_log("dtoken", 600, 10)
        assert passed is False
        assert state == 200


# ════════════════════════════════════════════════════════════
# 状态刷新 playTime 归一化
# ════════════════════════════════════════════════════════════

class TestNormalizePlayTime:
    def test_seconds_value_passthrough(self):
        assert normalize_play_time(300, 600) == 300.0

    def test_milliseconds_converted(self):
        assert normalize_play_time(300000, 600) == 300.0
        assert normalize_play_time(700000, 600) == 600.0  # 毫秒转换后仍超目标 → 钳制

    def test_slightly_over_target_treated_as_milliseconds(self):
        assert normalize_play_time(700, 600) == pytest.approx(0.7)

    def test_invalid_values_return_none(self):
        assert normalize_play_time(None, 600) is None
        assert normalize_play_time("abc", 600) is None
        assert normalize_play_time(float("nan"), 600) is None

    def test_negative_clamped_to_zero(self):
        assert normalize_play_time(-5, 600) == 0.0

    def test_zero(self):
        assert normalize_play_time(0, 600) == 0.0


# ════════════════════════════════════════════════════════════
# 闯关模式：视频必须同步完成
# ════════════════════════════════════════════════════════════

def _media(oid: str, passed: bool = False) -> dict:
    return {
        "objectId": oid,
        "job": f"job_{oid}",
        "jobid": f"job_{oid}",
        "isPassed": passed,
        "type": "video",
        "property": {"name": f"task_{oid}", "module": ""},
    }


def _attach(*medias) -> dict:
    return {"attachments": list(medias), "defaults": {}}


def _make_runner(learning_mode: str, single_thread_override: bool = False):
    from functions.deal_mission.deal_course import DealCourse

    user = MagicMock()
    user.uid = "U1"
    user.headers = {}
    course = MagicMock()
    course.course_name = "测试课程"
    course.class_id = "CL1"
    course.course_id = "CID1"
    course.cpi = "CPI1"
    course.ifOpen = True
    course.chapter_list = []
    runner = DealCourse(
        user, course, MagicMock(), "first", None, None,
        enable_multi_thread=True, max_concurrent_threads=5,
        use_tiku=False, learning_mode=learning_mode, user_id="U1",
        progress_dao=None,
    )
    runner.single_thread = single_thread_override
    # 避免真实等待
    runner._interruptible_sleep = lambda _s: False
    return runner


class _FakeThread:
    instances = []

    def __init__(self, target=None, args=(), kwargs=None, name=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.started = False
        self.joined = False
        _FakeThread.instances.append(self)

    def start(self):
        self.started = True
        self.target(*self.args, **self.kwargs)

    def join(self, timeout=None):
        self.joined = True

    def is_alive(self):
        return False


class TestStageModeSyncVideo:
    """闯关模式必须等待视频完成，否则章节被提前判 done。"""

    def _setup(self, monkeypatch, learning_mode):
        _FakeThread.instances.clear()
        monkeypatch.setattr(dc_mod, "threading",
                            SimpleNamespace(Thread=_FakeThread, Event=threading.Event))
        runner = _make_runner(learning_mode)
        # 固定为等时长分支，避免本机 config.yml 的 video-mode=0 让测试走真实网络
        runner.video_mode = 1

        class FakeDealVideo:
            calls = []

            @staticmethod
            def run_video(video, _user, _log, all_time=0, stop_event=None):
                FakeDealVideo.calls.append(video.name)
                return True

            @staticmethod
            def run_live(*_a, **_k):
                return None

        FakeDealVideo.calls.clear()
        return runner, FakeDealVideo

    def test_stage_mode_runs_video_synchronously(self, monkeypatch):
        runner, fake_dv = self._setup(monkeypatch, "stage")
        media = _media("A")
        ok, _name = runner._process_media(media, _attach(media), {"id": "ch1"}, fake_dv)

        assert ok is True
        assert fake_dv.calls == ["task_A"]
        # 闯关模式下不应把视频丢进异步线程池
        assert [t for t in _FakeThread.instances if t.started] == []

    def test_concurrent_mode_still_async(self, monkeypatch):
        runner, fake_dv = self._setup(monkeypatch, "concurrent")
        media = _media("B")
        ok, _name = runner._process_media(media, _attach(media), {"id": "ch1"}, fake_dv)

        assert ok is True
        assert len(_FakeThread.instances) == 1
        assert _FakeThread.instances[0].started is True
        assert _FakeThread.instances[0].joined is False


# ════════════════════════════════════════════════════════════
# 失败任务点补刷
# ════════════════════════════════════════════════════════════

class TestFailedMediaRetry:
    def test_failed_media_recorded_in_single_pass(self, monkeypatch):
        runner = _make_runner("sequential")
        runner.mission_list = [{"id": "ch1", "name": "第一章"}]
        monkeypatch.setattr(runner, "deal_chapter",
                            lambda _m: [_attach(_media("A"), _media("B"))])

        def fake_process(media, *_a):
            return (media["objectId"] == "B"), media["objectId"]

        monkeypatch.setattr(runner, "_process_media", fake_process)
        runner._do_single_pass_mode(MagicMock(), None)

        assert [m["objectId"] for m, _a, _c in runner._failed_media] == ["A"]

    def test_retry_until_success(self, monkeypatch):
        runner = _make_runner("sequential")
        runner.video_retry_rounds = 1
        media = _media("A")
        runner._failed_media = [(media, _attach(media), {"id": "ch1"})]

        calls = []
        monkeypatch.setattr(runner, "_process_media",
                            lambda m, *_a: (calls.append(m["objectId"]), True, m["objectId"])[1:])

        runner._retry_failed_media(MagicMock())
        assert calls == ["A"]
        assert runner._failed_media == []

    def test_retry_is_bounded(self, monkeypatch):
        runner = _make_runner("sequential")
        runner.video_retry_rounds = 2
        media = _media("A")
        runner._failed_media = [(media, _attach(media), {"id": "ch1"})]

        calls = []

        def always_fail(m, *_a):
            calls.append(m["objectId"])
            return False, m["objectId"]

        monkeypatch.setattr(runner, "_process_media", always_fail)
        runner._retry_failed_media(MagicMock())

        assert calls == ["A", "A"]
        assert len(runner._failed_media) == 1

    def test_retry_disabled_when_zero_rounds(self, monkeypatch):
        runner = _make_runner("sequential")
        runner.video_retry_rounds = 0
        media = _media("A")
        runner._failed_media = [(media, _attach(media), {"id": "ch1"})]
        monkeypatch.setattr(runner, "_process_media",
                            lambda *a: pytest.fail("rounds=0 时不应补刷"))

        runner._retry_failed_media(MagicMock())
        assert len(runner._failed_media) == 1

    def test_retry_skipped_in_review_mode(self, monkeypatch):
        runner = _make_runner("review")
        runner.video_retry_rounds = 2
        media = _media("A")
        runner._failed_media = [(media, _attach(media), {"id": "ch1"})]
        monkeypatch.setattr(runner, "_process_media",
                            lambda *a: pytest.fail("复习模式不补刷"))

        runner._retry_failed_media(MagicMock())
        assert len(runner._failed_media) == 1

    def test_do_finish_runs_retry_after_main_pass(self, monkeypatch):
        runner = _make_runner("sequential")
        runner.mission_list = [{"id": "ch1", "name": "第一章"}]

        _stub_set_time(monkeypatch)
        monkeypatch.setattr(runner, "deal_course", lambda: None)

        order = []
        monkeypatch.setattr(runner, "_do_single_pass_mode",
                            lambda *a: order.append("main"))
        monkeypatch.setattr(runner, "_retry_failed_media",
                            lambda *a: order.append("retry"))

        runner.do_finish()
        assert order == ["main", "retry"]


def _stub_set_time(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("functions.set_time")
    fake.DealVideo = MagicMock()
    monkeypatch.setitem(sys.modules, "functions.set_time", fake)


# ════════════════════════════════════════════════════════════
# 闯关模式：失败任务点先补试再门禁
# ════════════════════════════════════════════════════════════

class TestStageModeRetryBeforeGate:
    """闯关模式：任务点首次失败先补试，成功则继续后续章节，不触发门禁。"""

    def test_retry_success_continues_next_chapter(self, monkeypatch):
        runner = _make_runner("stage")
        runner.video_retry_rounds = 1
        runner.mission_list = [
            {"id": "ch1", "name": "第一章", "knowledge_id": "k1"},
            {"id": "ch2", "name": "第二章", "knowledge_id": "k2"},
        ]

        def fake_deal_chapter(mission):
            return [_attach(_media(f"m_{mission['id']}"))]

        monkeypatch.setattr(runner, "deal_chapter", fake_deal_chapter)

        results = {"m_ch1": [False, True], "m_ch2": [True]}
        seen = []

        def fake_process(media, *_a):
            oid = media["objectId"]
            seq = results[oid]
            ok = seq.pop(0) if len(seq) > 1 else seq[0]
            seen.append((oid, ok))
            return ok, oid

        monkeypatch.setattr(runner, "_process_media", fake_process)
        runner._do_stage_mode(MagicMock())

        # m_ch1 首次失败 → 补试成功 → 门禁不触发，第二章继续处理
        assert seen == [("m_ch1", False), ("m_ch1", True), ("m_ch2", True)]

    def test_persistent_failure_still_gates(self, monkeypatch):
        runner = _make_runner("stage")
        runner.video_retry_rounds = 1
        runner.mission_list = [
            {"id": "ch1", "name": "第一章", "knowledge_id": "k1"},
            {"id": "ch2", "name": "第二章", "knowledge_id": "k2"},
        ]
        monkeypatch.setattr(runner, "deal_chapter",
                            lambda m: [_attach(_media(f"m_{m['id']}"))])

        seen = []

        def always_fail(media, *_a):
            seen.append(media["objectId"])
            return False, media["objectId"]

        monkeypatch.setattr(runner, "_process_media", always_fail)
        runner._do_stage_mode(MagicMock())

        # ch1 补试1次后仍失败（共2次），门禁触发，ch2 不处理
        assert seen == ["m_ch1", "m_ch1"]


# ════════════════════════════════════════════════════════════
# 视频线程异常捕获
# ════════════════════════════════════════════════════════════

class TestRunVideoException:
    def test_exception_returns_false_instead_of_crashing_thread(self):
        from functions.set_time import DealVideo

        class BoomVideo:
            name = "boom"
            _stop_event = None

            def study(self, **_k):
                raise RuntimeError("模拟网络异常")

        ok = DealVideo.run_video(BoomVideo(), MagicMock(), MagicMock())
        assert ok is False
