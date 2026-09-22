# _*_ coding:utf-8 _*_
"""Work 全链路测试：题目获取(mock) -> 答案生成(假题库/假AI) -> 表单构造 -> 提交(捕获)。"""
from classis.Media.Work import Work


def make_work(strategy="first", ai_client=None, tiku=None):
    attachment = {
        "jobid": "work-9001",
        "enc": "testenc",
        "property": {"workid": "work-9001", "title": "测试作业"},
    }
    return Work(
        attachment, {"Cookie": "k=v"}, {}, "1001",
        strategy=strategy, ai_client=ai_client, tiku=tiku,
        clazzId="2001", userid="3001", ktoken="kt",
        knowledgeid="4001", cpi="5",
    )


def fetch_data():
    return {
        "questions": [
            {
                "id": "101", "title": "中国的首都是____，简称____。", "type": "completion",
                "options": "",
                "answerField": {"answer101": "", "answertype101": "2"},
            },
            {
                "id": "102", "title": "中华人民共和国成立于1949年。", "type": "judgement",
                "options": "",
                "answerField": {"answer102": "", "answertype102": "3"},
            },
        ],
        "pyFlag": "", "enc": "testenc", "workId": "9001",
        "answerwqbid": "101,102,",
    }


def patch_submit(monkeypatch, w, captured):
    """mock 题目获取与提交，捕获最终提交表单。"""
    monkeypatch.setattr(w, "_fetch_questions_from_api", lambda: fetch_data())

    def fake_submit(questions_data):
        captured.update(questions_data)
        return True

    monkeypatch.setattr(w, "_submit_answers", fake_submit)


class TestTikuPath:
    def test_completion_multiblank_and_judgement(self, monkeypatch, fake_tiku_cls):
        tiku = fake_tiku_cls({"completion": "北京;京", "judgement": "对"})
        w = make_work(tiku=tiku)
        captured = {}
        patch_submit(monkeypatch, w, captured)

        assert w.do_finish() is True
        # 填空题：两个空按 \n 连接写入同一字段（题库分号分隔已适配）
        assert captured["answer101"] == "北京\n京"
        assert captured["answertype101"] == "2"
        # 判断题
        assert captured["answer102"] == "true"
        assert captured["answertype102"] == "3"
        assert captured["answerwqbid"] == "101,102,"
        # 答案来源随答案一起落库到 answerSource 字段前的题目结构中

    def test_tiku_mode_never_calls_ai(self, monkeypatch, fake_tiku_cls, fake_ai):
        """题库答题路径与 AI 路径独立：题库模式下 AI 客户端零调用。"""
        tiku = fake_tiku_cls({"completion": "北京;京", "judgement": "对"})
        w = make_work(strategy="tiku", ai_client=fake_ai, tiku=tiku)
        captured = {}
        patch_submit(monkeypatch, w, captured)

        assert w.do_finish() is True
        assert captured["answer101"] == "北京\n京"
        assert captured["answer102"] == "true"
        assert fake_ai.content_calls == []
        assert fake_ai.letter_calls == []

    def test_answer_record_saved(self, monkeypatch, fake_tiku_cls, tmp_path):
        import answer_record
        from answer_record import AnswerRecordDAO
        tiku = fake_tiku_cls({"completion": "北京;京", "judgement": "对"})
        w = make_work(tiku=tiku)
        patch_submit(monkeypatch, w, {})

        dao = AnswerRecordDAO(db_path=str(tmp_path / "records.db"))
        monkeypatch.setattr(answer_record, "get_answer_record_dao", lambda: dao)
        try:
            assert w.do_finish() is True
            progress = dao.get_progress(course_id="1001")
            assert progress["total"] == 2
            assert progress["hit"] == 2
            wrong = dao.get_wrong_records(course_id="1001")
            assert wrong == []
        finally:
            dao.close()


class TestAIPath:
    def test_completion_uses_content_interface_once(self, monkeypatch, fake_ai):
        w = make_work(strategy="ai", ai_client=fake_ai)
        captured = {}
        patch_submit(monkeypatch, w, captured)

        assert w.do_finish() is True
        assert captured["answer101"] == "太白\n青莲居士"
        # 非选择题只应调用内容接口一次，不再重复调用字母接口
        assert len(fake_ai.content_calls) == 2  # completion + judgement 各一次
        assert len(fake_ai.letter_calls) == 0


class TestRandomPath:
    def test_random_fallback_fills_all(self, monkeypatch):
        w = make_work()  # first 策略、无题库无 AI
        captured = {}
        patch_submit(monkeypatch, w, captured)

        assert w.do_finish() is True
        assert captured["answer101"] != ""
        assert captured["answer102"] in ("true", "false")


class TestSubjectivePath:
    """主观题（论述/名词解释/计算/案例分析）统一链路。"""

    @staticmethod
    def essay_work_data():
        data = fetch_data()
        data["questions"] = [
            {
                "id": "201", "title": "试论述供给侧结构性改革", "type": "essay",
                "options": "",
                "answerField": {"answer201": "", "answertype201": "6"},
            },
        ]
        data["answerwqbid"] = "201,"
        return data

    def test_essay_tiku_long_answer(self, monkeypatch, fake_tiku_cls):
        tiku = fake_tiku_cls({"essay": "长" * 40})
        w = make_work(tiku=tiku)
        captured = {}
        patch_submit(monkeypatch, w, captured)
        monkeypatch.setattr(w, "_fetch_questions_from_api", self.essay_work_data)

        assert w.do_finish() is True
        assert captured["answer201"] == "长" * 40
        assert captured["answertype201"] == "6"
        assert captured["answerwqbid"] == "201,"

    def test_essay_ai_template_payload(self, monkeypatch, fake_ai):
        w = make_work(strategy="ai", ai_client=fake_ai)
        captured = {}
        patch_submit(monkeypatch, w, captured)
        monkeypatch.setattr(w, "_fetch_questions_from_api", self.essay_work_data)

        assert w.do_finish() is True
        assert captured["answer201"] == "（1）要点一（2）要点二（3）要点三"
        # 主观题只应调用内容接口且携带模板提示词，不再调用字母接口
        assert len(fake_ai.letter_calls) == 0
        assert "system_prompt" in fake_ai.content_calls[0]

    def test_essay_fallback_text(self, monkeypatch):
        w = make_work()  # first 策略、无题库无 AI -> 兜底占位
        captured = {}
        patch_submit(monkeypatch, w, captured)
        monkeypatch.setattr(w, "_fetch_questions_from_api", self.essay_work_data)

        assert w.do_finish() is True
        assert captured["answer201"] != ""
