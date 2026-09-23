# _*_ coding:utf-8 _*_
"""学习模式与章节级进度记忆测试：

- CourseProgressDAO：写入幂等 / 已完成集合 / 续学起点 / 清空隔离
- DealCourse 闯关模式：任务点循环重拉（刚解锁的任务点被处理）
- DealCourse 闯关门禁：章节失败则停止后续章节并写 partial
- DealCourse 顺序模式：不门禁，章节失败仍处理后续章节
- DealCourse 复习模式：isPassed=True 的任务点仍被处理
- DealCourse do_finish：手动勾选章节优先于续学记忆
- EngineAdapter.auto_complete：learning_mode / user_id 透传
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# conftest.py 已将 engine 与 ROOT 加入 sys.path
from course_progress import (  # noqa: E402
    CourseProgressDAO,
    reset_course_progress_dao_for_test,
)
from functions.deal_mission.deal_course import DealCourse  # noqa: E402


# ────────────────────────────────────────────────────────────
# 辅助：构造测试用 DealCourse 与媒体数据
# ────────────────────────────────────────────────────────────

def _media(oid: str, passed: bool = False) -> dict:
    """生成一个带 objectId 的任务点 dict。"""
    return {
        "objectId": oid,
        "isPassed": passed,
        "type": "video",
        "property": {"name": f"task_{oid}", "module": ""},
    }


def _attach(*medias) -> dict:
    """生成一个包含若干任务点的附件页。"""
    return {"attachments": list(medias), "defaults": {}}


def _make_deal_course(learning_mode: str = "stage",
                      chapter_ids: list = None,
                      dao: CourseProgressDAO = None,
                      user_id: str = "U1") -> DealCourse:
    """构造一个不触发网络请求的 DealCourse 实例。"""
    user = MagicMock()
    user.uid = user_id
    user.headers = {}
    course = MagicMock()
    course.course_name = "测试课程"
    course.class_id = "CL1"
    course.course_id = "CID1"
    course.cpi = "CPI1"
    course.ifOpen = True
    course.chapter_list = []
    log = MagicMock()
    runner = DealCourse(
        user, course, log, "first", None, chapter_ids,
        enable_multi_thread=False, max_concurrent_threads=1,
        use_tiku=False,
        learning_mode=learning_mode,
        user_id=user_id,
        progress_dao=dao,
    )
    return runner


def _stub_set_time(monkeypatch):
    """向 sys.modules 注入假的 functions.set_time，避免真实 DealVideo 导入。"""
    fake = types.ModuleType("functions.set_time")
    fake.DealVideo = MagicMock()
    monkeypatch.setitem(sys.modules, "functions.set_time", fake)


# ════════════════════════════════════════════════════════════
# CourseProgressDAO 测试
# ════════════════════════════════════════════════════════════

class TestCourseProgressDAO:
    """章节级进度 DAO 的读写与隔离测试。"""

    def test_save_and_get_completed(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "C1", "ch1", "第一章", status="done")
        dao.save_chapter_done("U1", "C1", "ch2", "第二章", status="done")
        assert dao.get_completed_chapters("U1", "C1") == {"ch1", "ch2"}

    def test_save_idempotent(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "C1", "ch1", "第一章", status="done")
        # 同主键再写一次（章节名更新）
        dao.save_chapter_done("U1", "C1", "ch1", "第一章(更新)", status="done")
        records = dao.list_chapter_progress("U1", "C1")
        assert len(records) == 1
        assert records[0]["chapter_name"] == "第一章(更新)"

    def test_resume_position_returns_partial(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "C1", "ch1", status="done")
        dao.save_chapter_done("U1", "C1", "ch2", status="partial")
        dao.save_chapter_done("U1", "C1", "ch3", status="done")
        assert dao.get_resume_position("U1", "C1") == "ch2"

    def test_resume_position_none_when_all_done(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "C1", "ch1", status="done")
        dao.save_chapter_done("U1", "C1", "ch2", status="done")
        assert dao.get_resume_position("U1", "C1") is None

    def test_resume_position_none_when_empty(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        assert dao.get_resume_position("U1", "C1") is None

    def test_clear_course_progress_isolates_courses(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "C1", "ch1", status="done")
        dao.save_chapter_done("U1", "C2", "chX", status="done")
        dao.clear_course_progress("U1", "C1")
        assert dao.get_completed_chapters("U1", "C1") == set()
        assert dao.get_completed_chapters("U1", "C2") == {"chX"}

    def test_user_isolation(self, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "C1", "ch1", status="done")
        dao.save_chapter_done("U2", "C1", "ch1", status="partial")
        assert dao.get_completed_chapters("U1", "C1") == {"ch1"}
        assert dao.get_completed_chapters("U2", "C1") == set()
        assert dao.get_resume_position("U2", "C1") == "ch1"


# ════════════════════════════════════════════════════════════
# DealCourse 闯关模式测试
# ════════════════════════════════════════════════════════════

class TestStageMode:
    """闯关模式：任务点循环重拉 + 章节严格门禁。"""

    def test_repulls_for_newly_unlocked_task(self, monkeypatch, tmp_path):
        """核心痛点修复：刷完上一个任务点后，刚解锁的下一个任务点也被处理。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="stage", dao=dao)
        runner.mission_list = [{"id": "ch1", "name": "第一章", "knowledge_id": "k1"}]

        call_seq = []

        def fake_deal_chapter(_mission):
            call_seq.append("pull")
            # 第1次：只有任务A未完成
            # 第2次：A已通过，B刚解锁未完成
            # 第3次：A、B均通过 → 无新增
            if len(call_seq) == 1:
                return [_attach(_media("A", passed=False))]
            elif len(call_seq) == 2:
                return [_attach(_media("A", passed=True), _media("B", passed=False))]
            else:
                return [_attach(_media("A", passed=True), _media("B", passed=True))]

        monkeypatch.setattr(runner, "deal_chapter", fake_deal_chapter)

        processed = []

        def fake_process(media, _a, _m, _dv):
            processed.append(media["objectId"])
            return True, media["objectId"]

        monkeypatch.setattr(runner, "_process_media", fake_process)

        runner._do_stage_mode(MagicMock(), dao)

        # 拉取3次（A未过→处理A→重拉→B解锁→处理B→重拉→全过→退出）
        assert len(call_seq) == 3
        # 处理了A和刚解锁的B
        assert processed == ["A", "B"]
        # 章节通过，写入 done
        assert "ch1" in dao.get_completed_chapters("U1", "CID1")

    def test_chapter_gate_stops_on_failure(self, monkeypatch, tmp_path):
        """章节门禁：第二章失败则第三章不被处理，并写 partial。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="stage", dao=dao)
        runner.mission_list = [
            {"id": "ch1", "name": "第一章", "knowledge_id": "k1"},
            {"id": "ch2", "name": "第二章", "knowledge_id": "k2"},
            {"id": "ch3", "name": "第三章", "knowledge_id": "k3"},
        ]

        chapter_calls = []

        def fake_deal_chapter(mission):
            ch_id = mission["id"]
            chapter_calls.append(ch_id)
            return [_attach(_media(f"m_{ch_id}", passed=False))]

        monkeypatch.setattr(runner, "deal_chapter", fake_deal_chapter)

        results = {"ch1": True, "ch2": False}

        def fake_process(media, _a, mission, _dv):
            return results.get(mission["id"], True), media["objectId"]

        monkeypatch.setattr(runner, "_process_media", fake_process)

        runner._do_stage_mode(MagicMock(), dao)

        # ch1 通过、ch2 失败 → ch3 不被调用
        assert "ch3" not in chapter_calls
        # ch1 done、ch2 partial
        assert "ch1" in dao.get_completed_chapters("U1", "CID1")
        assert dao.get_resume_position("U1", "CID1") == "ch2"

    def test_empty_chapter_skipped(self, monkeypatch, tmp_path):
        """空章节（无任务点）直接通过。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="stage", dao=dao)
        runner.mission_list = [{"id": "ch1", "name": "空章", "knowledge_id": "k1"}]

        monkeypatch.setattr(runner, "deal_chapter", lambda _m: [])
        monkeypatch.setattr(runner, "_process_media",
                            lambda *a: pytest.fail("不应调用 _process_media"))

        runner._do_stage_mode(MagicMock(), dao)
        assert "ch1" in dao.get_completed_chapters("U1", "CID1")

    def test_notopen_chapter_breaks(self, monkeypatch, tmp_path):
        """未开放章节返回 False，跳过但不影响后续。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="stage", dao=dao)
        runner.mission_list = [
            {"id": "ch1", "name": "未开放", "knowledge_id": "k1"},
            {"id": "ch2", "name": "正常", "knowledge_id": "k2"},
        ]

        pull_count = [0]

        def fake_deal_chapter(mission):
            pull_count[0] += 1
            if mission["id"] == "ch1":
                return False
            return [_attach(_media("m_ch2", passed=False))]

        monkeypatch.setattr(runner, "deal_chapter", fake_deal_chapter)
        monkeypatch.setattr(runner, "_process_media",
                            lambda *a: (True, "ok"))

        runner._do_stage_mode(MagicMock(), dao)
        # ch1 跳过，ch2 处理通过
        assert "ch2" in dao.get_completed_chapters("U1", "CID1")


# ════════════════════════════════════════════════════════════
# DealCourse 顺序/复习模式测试
# ════════════════════════════════════════════════════════════

class TestSinglePassModes:
    """sequential / concurrent / review 模式的单次遍历行为。"""

    def test_sequential_no_gate(self, monkeypatch, tmp_path):
        """sequential：第2章失败仍处理第3章，deal_chapter 每章只调1次。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="sequential", dao=dao)
        runner.mission_list = [
            {"id": "ch1", "name": "第一章", "knowledge_id": "k1"},
            {"id": "ch2", "name": "第二章", "knowledge_id": "k2"},
            {"id": "ch3", "name": "第三章", "knowledge_id": "k3"},
        ]

        chapter_calls = []

        def fake_deal_chapter(mission):
            chapter_calls.append(mission["id"])
            return [_attach(_media(f"m_{mission['id']}", passed=False))]

        monkeypatch.setattr(runner, "deal_chapter", fake_deal_chapter)

        results = {"ch1": True, "ch2": False, "ch3": True}

        def fake_process(media, _a, mission, _dv):
            return results[mission["id"]], media["objectId"]

        monkeypatch.setattr(runner, "_process_media", fake_process)

        runner._do_single_pass_mode(MagicMock(), dao)

        # 每章只拉取1次，3章都被处理（无门禁）
        assert chapter_calls == ["ch1", "ch2", "ch3"]
        # sequential 记录进度
        assert "ch1" in dao.get_completed_chapters("U1", "CID1")
        assert dao.get_resume_position("U1", "CID1") == "ch2"

    def test_review_processes_passed_media(self, monkeypatch, tmp_path):
        """review：isPassed=True 的任务点仍被 _process_media 处理。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="review", dao=dao)
        runner.mission_list = [{"id": "ch1", "name": "复习章", "knowledge_id": "k1"}]

        monkeypatch.setattr(runner, "deal_chapter",
                            lambda _m: [_attach(_media("m1", passed=True))])

        processed = []
        monkeypatch.setattr(runner, "_process_media",
                            lambda media, *a: (processed.append(media["objectId"]), True, media["objectId"])[1:])

        runner._do_single_pass_mode(MagicMock(), dao)
        assert processed == ["m1"]

    def test_concurrent_ignores_progress(self, monkeypatch, tmp_path):
        """concurrent 模式不写章节级进度（仅 sequential 写）。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="concurrent", dao=dao)
        runner.mission_list = [{"id": "ch1", "name": "第一章", "knowledge_id": "k1"}]

        monkeypatch.setattr(runner, "deal_chapter",
                            lambda _m: [_attach(_media("m1", passed=False))])
        monkeypatch.setattr(runner, "_process_media",
                            lambda *a: (True, "ok"))

        runner._do_single_pass_mode(MagicMock(), dao)
        # concurrent 不写进度
        assert dao.get_completed_chapters("U1", "CID1") == set()
        assert dao.get_resume_position("U1", "CID1") is None


# ════════════════════════════════════════════════════════════
# DealCourse.do_finish 章节过滤与续学测试
# ════════════════════════════════════════════════════════════

class TestDoFinishDispatch:
    """do_finish 的章节过滤、续学起点与模式分派。"""

    def test_stage_skips_done_and_resumes_from_partial(self, monkeypatch, tmp_path):
        """闯关模式：跳过已 done 章节，从最近 partial 章节续学。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "CID1", "ch1", "第一章", status="done")
        dao.save_chapter_done("U1", "CID1", "ch2", "第二章", status="partial")

        runner = _make_deal_course(learning_mode="stage", dao=dao)
        _stub_set_time(monkeypatch)
        monkeypatch.setattr(runner, "deal_course", lambda: None)

        captured = {}

        def fake_stage(_dv, _d):
            captured["mission_list"] = [m["id"] for m in runner.mission_list]

        monkeypatch.setattr(runner, "_do_stage_mode", fake_stage)

        runner.mission_list = [
            {"id": "ch1", "name": "第一章"},
            {"id": "ch2", "name": "第二章"},
            {"id": "ch3", "name": "第三章"},
        ]
        runner.do_finish()

        # ch1 (done) 跳过，从 ch2 (partial) 续学 → ch2、ch3
        assert captured["mission_list"] == ["ch2", "ch3"]

    def test_manual_chapter_ids_override_resume(self, monkeypatch, tmp_path):
        """手动勾选章节时，进度记忆被忽略。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        dao.save_chapter_done("U1", "CID1", "ch1", status="done")
        dao.save_chapter_done("U1", "CID1", "ch2", status="partial")

        runner = _make_deal_course(
            learning_mode="stage", dao=dao, chapter_ids=["ch1", "ch3"])
        _stub_set_time(monkeypatch)
        monkeypatch.setattr(runner, "deal_course", lambda: None)

        captured = {}

        def fake_stage(_dv, _d):
            captured["mission_list"] = [m["id"] for m in runner.mission_list]

        monkeypatch.setattr(runner, "_do_stage_mode", fake_stage)

        runner.mission_list = [
            {"id": "ch1", "name": "第一章"},
            {"id": "ch2", "name": "第二章"},
            {"id": "ch3", "name": "第三章"},
        ]
        runner.do_finish()

        # 手动勾选 ch1 + ch3，resume 被忽略（ch1 虽然 done 但被手动选中）
        assert captured["mission_list"] == ["ch1", "ch3"]

    def test_dispatches_single_pass_for_sequential(self, monkeypatch, tmp_path):
        """sequential 模式走 _do_single_pass_mode 分支。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="sequential", dao=dao)
        _stub_set_time(monkeypatch)
        monkeypatch.setattr(runner, "deal_course", lambda: None)

        called = {"stage": False, "single": False}
        monkeypatch.setattr(runner, "_do_stage_mode",
                            lambda *a: called.__setitem__("stage", True))
        monkeypatch.setattr(runner, "_do_single_pass_mode",
                            lambda *a: called.__setitem__("single", True))

        runner.mission_list = [{"id": "ch1", "name": "第一章"}]
        runner.do_finish()
        assert called == {"stage": False, "single": True}


# ════════════════════════════════════════════════════════════
# DealCourse 停止控制测试（快捷操作可中断能力的继承）
# ════════════════════════════════════════════════════════════

class TestStopControl:
    """刷课引擎可被停止/暂停按钮中断。"""

    def test_request_stop_sets_flag(self):
        runner = _make_deal_course()
        assert runner.is_stopped is False
        runner.request_stop()
        assert runner.is_stopped is True

    def test_interruptible_sleep_returns_immediately(self):
        import time as _time
        runner = _make_deal_course()
        runner.request_stop()
        t0 = _time.monotonic()
        interrupted = runner._interruptible_sleep(30)
        elapsed = _time.monotonic() - t0
        assert interrupted is True
        assert elapsed < 1.0

    def test_interruptible_sleep_waits_when_running(self):
        runner = _make_deal_course()
        t0 = __import__("time").monotonic()
        interrupted = runner._interruptible_sleep(0.2)
        assert interrupted is False
        assert __import__("time").monotonic() - t0 >= 0.15

    def test_stage_stops_before_first_chapter(self, monkeypatch, tmp_path):
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="stage", dao=dao)
        runner.mission_list = [
            {"id": "ch1", "name": "第一章"},
            {"id": "ch2", "name": "第二章"},
        ]

        def boom(*_a, **_k):
            pytest.fail("停止后不应调用 deal_chapter")

        monkeypatch.setattr(runner, "deal_chapter", boom)
        runner.request_stop()
        runner._do_stage_mode(MagicMock(), dao)
        # 未写任何章节进度
        assert dao.list_chapter_progress("U1", "CID1") == []

    def test_stage_repull_stop_writes_partial(self, monkeypatch, tmp_path):
        """闯关重拉时停止：本章写 partial，下次可续学。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="stage", dao=dao)
        runner.mission_list = [{"id": "ch1", "name": "第一章"}]

        pull_count = [0]

        def fake_deal_chapter(_m):
            pull_count[0] += 1
            return [_attach(_media("A", passed=False))]

        monkeypatch.setattr(runner, "deal_chapter", fake_deal_chapter)

        def fake_process(*_a):
            # 完成 A 的同时用户点停止
            runner.request_stop()
            return True, "A"

        monkeypatch.setattr(runner, "_process_media", fake_process)
        runner._do_stage_mode(MagicMock(), dao)

        # 只拉取1次，重拉检查点检测停止后退出
        assert pull_count[0] == 1
        assert dao.get_resume_position("U1", "CID1") == "ch1"

    def test_single_pass_stops_during_prefetch(self, monkeypatch, tmp_path):
        runner = _make_deal_course(learning_mode="sequential")
        runner.mission_list = [{"id": "ch1", "name": "第一章"}]

        def boom(*_a, **_k):
            pytest.fail("预拉取停止后不应调用 deal_chapter")

        monkeypatch.setattr(runner, "deal_chapter", boom)
        runner.request_stop()
        runner._do_single_pass_mode(MagicMock(), None)

    def test_sequential_stop_writes_partial(self, monkeypatch, tmp_path):
        """顺序模式：ch1 刷完后停止，ch1 done、ch2 partial。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="sequential", dao=dao)
        runner.mission_list = [
            {"id": "ch1", "name": "第一章"},
            {"id": "ch2", "name": "第二章"},
        ]

        monkeypatch.setattr(
            runner, "deal_chapter",
            lambda m: [_attach(_media(f"m_{m['id']}", passed=False))])

        def fake_process(*_a):
            runner.request_stop()
            return True, "ok"

        monkeypatch.setattr(runner, "_process_media", fake_process)
        runner._do_single_pass_mode(MagicMock(), dao)

        assert "ch1" in dao.get_completed_chapters("U1", "CID1")
        assert dao.get_resume_position("U1", "CID1") == "ch2"

    def test_sequential_stop_between_medias_in_same_chapter(self, monkeypatch, tmp_path):
        """同一章内任务点之间停止：写本章 partial，不处理后续任务点。"""
        dao = reset_course_progress_dao_for_test(str(tmp_path / "t.db"))
        runner = _make_deal_course(learning_mode="sequential", dao=dao)
        runner.mission_list = [{"id": "ch1", "name": "第一章"}]

        monkeypatch.setattr(
            runner, "deal_chapter",
            lambda _m: [_attach(_media("A", False), _media("B", False))])

        processed = []

        def fake_process(media, *_a):
            processed.append(media["objectId"])
            runner.request_stop()
            return True, media["objectId"]

        monkeypatch.setattr(runner, "_process_media", fake_process)
        runner._do_single_pass_mode(MagicMock(), dao)

        assert processed == ["A"]  # B 不处理
        assert dao.get_resume_position("U1", "CID1") == "ch1"


class TestAdapterRequestStop:
    """EngineAdapter.request_stop 转发到当前 runner。"""

    def test_forwards_to_runner(self):
        from src.engine_adapter import EngineAdapter
        adapter = EngineAdapter()
        runner = MagicMock()
        adapter._current_runner = runner
        adapter.request_stop()
        runner.request_stop.assert_called_once()

    def test_no_runner_is_noop(self):
        from src.engine_adapter import EngineAdapter
        adapter = EngineAdapter()
        adapter._current_runner = None
        adapter.request_stop()  # 不抛异常

    def test_auto_complete_returns_stopped_message(self, monkeypatch, tmp_path):
        """do_finish 中途停止 → auto_complete 返回 stopped 提示。"""
        from src.engine_adapter import EngineAdapter
        adapter = EngineAdapter()
        mock_course = MagicMock()
        mock_course.ifOpen = True
        monkeypatch.setattr(adapter, "_find_course", lambda _cid: mock_course)
        monkeypatch.setattr(adapter, "_get_ai", lambda: None)

        class FakeDealCourse:
            def __init__(self, *a, **k):
                self.thread_pool = []
                self._stopped = False

            def request_stop(self):
                self._stopped = True

            @property
            def is_stopped(self):
                return self._stopped

            def do_finish(self):
                self._stopped = True  # 模拟刷课中途被停止

        import functions.deal_mission.deal_course as dc_mod
        monkeypatch.setattr(dc_mod, "DealCourse", FakeDealCourse)

        ok, _stats, msg = adapter.auto_complete("CID1", "CL1", "first", "CPI1")
        assert ok is True
        assert "stopped" in msg
        # runner 引用已清理
        assert adapter._current_runner is None


# ════════════════════════════════════════════════════════════
# EngineAdapter.auto_complete 透传测试
# ════════════════════════════════════════════════════════════

class TestAutoCompletePassthrough:
    """验证 auto_complete 将 learning_mode / user_id 透传给 DealCourse。"""

    def test_passes_learning_mode_and_user_id(self, monkeypatch, tmp_path):
        from src.engine_adapter import EngineAdapter

        adapter = EngineAdapter()
        # 模拟已登录 + 找到课程
        mock_course = MagicMock()
        mock_course.ifOpen = True
        monkeypatch.setattr(adapter, "_find_course", lambda _cid: mock_course)
        monkeypatch.setattr(adapter, "_get_ai", lambda: None)
        monkeypatch.setattr(adapter, "fetch_chapters",
                            lambda _cid, _clid, _cpi: (True, [], ""))

        captured = {}

        class FakeDealCourse:
            def __init__(self, user, course, log, strategy, ai, chapter_ids,
                         multi_thread, max_threads, use_tiku=None,
                         learning_mode="stage", user_id=None):
                captured["learning_mode"] = learning_mode
                captured["user_id"] = user_id
                self.thread_pool = []
                self._stopped = False

            def request_stop(self):
                self._stopped = True

            @property
            def is_stopped(self):
                return self._stopped

            def do_finish(self):
                captured["do_finish_called"] = True

        import functions.deal_mission.deal_course as dc_mod
        monkeypatch.setattr(dc_mod, "DealCourse", FakeDealCourse)

        ok, _stats, msg = adapter.auto_complete(
            "CID1", "CL1", "first", "CPI1",
            learning_mode="sequential", user_id="U42",
        )

        assert ok is True
        assert captured["learning_mode"] == "sequential"
        assert captured["user_id"] == "U42"
        assert captured["do_finish_called"] is True
