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
from classis.Media.Video import (  # noqa: E402
    FINISH_MAX_RETRIES,
    Video,
    normalize_play_time,
    should_give_up_finish,
)


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
    """替换 study() 的所有 IO：状态、进度上报、等待。"""
    monkeypatch.setattr(
        video, "get_status",
        lambda *a, **k: {"status": "success", "duration": duration, "dtoken": "dt"})
    calls = []

    def fake_progress(*_a, **_k):
        calls.append(1)
        if len(calls) > 30:
            raise AssertionError("补报次数失控：到达目标时长后没有停止条件")
        idx = min(len(calls) - 1, len(progress_results) - 1)
        return progress_results[idx]

    monkeypatch.setattr(video, "_video_progress_log", fake_progress)
    monkeypatch.setattr(Video, "_sleep", lambda self, _s: False)
    monkeypatch.setattr(video_mod, "_video_log_rate_limit", lambda: None)
    return calls


# ════════════════════════════════════════════════════════════
# 到达目标时长后的补报上限
# ════════════════════════════════════════════════════════════

class TestFinishRetryBudget:
    def test_should_give_up_finish(self):
        assert should_give_up_finish(0) is False
        assert should_give_up_finish(FINISH_MAX_RETRIES - 1) is False
        assert should_give_up_finish(FINISH_MAX_RETRIES) is True
        assert should_give_up_finish(FINISH_MAX_RETRIES + 1) is True

    def test_study_gives_up_after_bounded_finish_retries(self, monkeypatch):
        """到达 100% 仍不通过时，必须在上限内放弃，不能无限上报。"""
        video = _video(play_time_ms=10 * 1000, duration=10)
        calls = _patch_video_io(monkeypatch, video, [(False, 200)])

        assert video.study() is False
        # 2 次瞬间完成尝试 + 有限次补报
        assert len(calls) <= 2 + FINISH_MAX_RETRIES

    def test_study_succeeds_when_later_finish_report_passes(self, monkeypatch):
        """补报过程中平台判定通过 → 正常返回 True。"""
        video = _video(play_time_ms=10 * 1000, duration=10)
        _patch_video_io(monkeypatch, video, [(False, 200), (False, 200), (True, 200)])

        assert video.study() is True


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
