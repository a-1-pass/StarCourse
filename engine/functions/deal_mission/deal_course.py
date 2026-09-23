# _*_ coding:utf-8 _*_
import random
import threading
import time

import loguru

from config import GloConfig
from classis.Media.Book import Book
from classis.Media.BBS import BBS
from classis.Media.Document import Document
from classis.Media.Live import Live
from classis.Media.Read import Read
from classis.Media.Video import Video
from classis.Media.Quiz import Quiz
from classis.Media.Work import Work
from card_decode import fetch_chapter_attachments, is_live_attachment, is_read_attachment, study_empty_page
import classis.User


class DealCourse:
    # 学习模式标识符（与 GUI 选项、config.yml 一致）
    MODE_STAGE = "stage"            # 闯关：任务点循环重拉 + 章节严格门禁
    MODE_SEQUENTIAL = "sequential"  # 顺序：单次遍历，不门禁
    MODE_CONCURRENT = "concurrent"  # 并发：多线程并发（现有行为）
    MODE_REVIEW = "review"         # 复习：单次遍历，忽略 isPassed 重做

    def __init__(self, user: classis.User.User, course: classis.User.Course, log, answer_strategy: str = "first",
                 ai_client=None, chapter_ids: list = None,
                 enable_multi_thread: bool = None, max_concurrent_threads: int = None,
                 use_tiku: bool = None,
                 learning_mode: str = "stage",
                 user_id: str = None,
                 progress_dao=None):
        self.user = user
        self.course = course
        self.log = log
        self.answer_strategy = answer_strategy
        self.ai_client = ai_client
        self.chapter_ids = chapter_ids
        self.course_name = course.course_name
        self.class_id = course.class_id
        self.course_id = course.course_id
        self.cpi = course.cpi
        self.mission_list = []
        self.video_mode = GloConfig.data.get("FunConfig").get("deal-mission").get("video-mode")
        self.single_thread = GloConfig.data.get("FunConfig").get("deal-mission").get("single-thread")

        # ── 学习模式与章节级进度 ──
        # GUI 传入优先；缺省回退到 config.yml；最终缺省 'stage'（闯关模式为学习通主流）
        self.learning_mode = learning_mode or GloConfig.data.get("FunConfig").get(
            "deal-mission").get("learning-mode", "stage") or "stage"
        # user_id 缺省时从 user 对象取 uid
        self.user_id = user_id or getattr(user, "uid", None)
        # progress_dao 缺省时按 config.yml 的 progress-persistence 懒加载
        self._progress_dao = progress_dao
        self._progress_dao_inited = progress_dao is not None

        self.tiku = None
        # use_tiku=False 由 GUI 显式关闭题库路径（题库/AI 双路径独立开关）；
        # None/True 时按 engine/config.yml 的 TikuConfig 决定（独立运行默认行为）
        if use_tiku is not False:
            try:
                from tiku import Tiku
                tiku_config = GloConfig.data.get("TikuConfig", {})
                if tiku_config and tiku_config.get("provider"):
                    tiku = Tiku()
                    tiku.config_set(tiku_config)
                    tiku.get_tiku_from_config()
                    if not tiku.DISABLE:
                        self.tiku = tiku
                        log.info("✅ 题库系统已启用")
            except Exception as e:
                loguru.logger.debug(f"题库初始化失败: {e}")

        if enable_multi_thread is not None:
            self.enable_multi_thread = enable_multi_thread
        else:
            self.enable_multi_thread = GloConfig.data.get("FunConfig").get("deal-mission").get("enable-multi-thread", False)

        if max_concurrent_threads is not None:
            self.max_concurrent_threads = max_concurrent_threads
        else:
            self.max_concurrent_threads = GloConfig.data.get("FunConfig").get("deal-mission").get("max-concurrent-threads", 15)

        self.thread_pool = []

        # ── 停止控制（继承快捷操作的可中断能力） ──
        # GUI 停止/暂停按钮通过 EngineAdapter.request_stop() 置位此事件；
        # 各章节循环、任务点处理、视频/直播等待处均有检查点。
        self._stop_event = threading.Event()

        if not self.enable_multi_thread:
            self.single_thread = True

    # ── 进度 DAO 懒加载 ──
    def _get_progress_dao(self):
        """按 config.yml 的 progress-persistence 决定是否启用章节级进度记忆。

        - 显式传入 progress_dao 时直接使用；
        - 否则读取 `FunConfig.deal-mission.progress-persistence`，为 True 时调用单例
          `get_course_progress_dao()`；为 False 时返回 None（禁用进度记忆）。
        """
        if self._progress_dao_inited:
            return self._progress_dao
        self._progress_dao_inited = True
        try:
            persistence = GloConfig.data.get("FunConfig").get("deal-mission").get("progress-persistence", True)
        except Exception:
            persistence = True
        if not persistence or not self.user_id:
            self._progress_dao = None
            return None
        try:
            from course_progress import get_course_progress_dao
            self._progress_dao = get_course_progress_dao()
        except Exception as e:
            loguru.logger.debug(f"进度 DAO 加载失败: {e}")
            self._progress_dao = None
        return self._progress_dao

    # ── 停止控制（快捷操作可中断能力的继承） ──
    def request_stop(self):
        """请求中断刷课流程（GUI 停止/暂停按钮调用，线程安全）。"""
        self._stop_event.set()

    @property
    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def _interruptible_sleep(self, seconds: float) -> bool:
        """可被停止中断的等待。

        :return: True 表示在等待期间被停止；False 表示正常等满
        """
        return self._stop_event.wait(timeout=max(0.0, float(seconds or 0.0)))

    def _check_stopped(self, where: str = "") -> bool:
        """循环检查点：已停止则记录日志并返回 True。"""
        if self._stop_event.is_set():
            self.log.warning(f"⏹ 刷课已被停止（{where}）")
            return True
        return False

    def do_finish(self):
        if not self.course.ifOpen:
            self.log.warning("本课程已结课或锁定，将自动跳过")
            return
        from functions.set_time import DealVideo
        self.deal_course()
        self.thread_pool.clear()
        if not self.mission_list:
            return

        # ── 章节过滤（用户手动勾选优先） ──
        if self.chapter_ids:
            self.log.info(f"用户选择学习 {len(self.chapter_ids)} 个章节")
            filtered_missions = []
            for mission_item in self.mission_list:
                chapter_id = mission_item.get('id') or mission_item.get('chapter_id')
                if chapter_id and str(chapter_id) in [str(cid) for cid in self.chapter_ids]:
                    filtered_missions.append(mission_item)
            self.log.info(f"过滤后剩余 {len(filtered_missions)} 个章节待完成")
            self.mission_list = filtered_missions
        else:
            self.log.info(f"共读取到 {len(self.mission_list)} 个章节待完成")

        # ── 章节级进度加载与续学起点（仅 stage/sequential 且未手动勾选） ──
        dao = self._get_progress_dao()
        completed_set = set()
        resume_chapter_id = None
        if (dao and self.user_id
                and self.learning_mode in (self.MODE_STAGE, self.MODE_SEQUENTIAL)
                and not self.chapter_ids):
            try:
                completed_set = dao.get_completed_chapters(self.user_id, self.course_id)
                resume_chapter_id = dao.get_resume_position(self.user_id, self.course_id)
                if completed_set:
                    self.log.info(f"📚 已完成章节记忆: {len(completed_set)} 章")
            except Exception as e:
                loguru.logger.debug(f"进度加载失败: {e}")

            # stage 模式：跳过已 done 的章节
            if self.learning_mode == self.MODE_STAGE and completed_set:
                before = len(self.mission_list)
                self.mission_list = [m for m in self.mission_list
                                     if str(m.get('id') or m.get('chapter_id') or "")
                                     not in completed_set]
                skipped = before - len(self.mission_list)
                if skipped > 0:
                    self.log.info(f"闯关模式跳过 {skipped} 个已完成章节")

            # 从续学起点（最近 partial 章节）开始
            if resume_chapter_id:
                resume_idx = -1
                for idx, m in enumerate(self.mission_list):
                    cid = m.get('id') or m.get('chapter_id')
                    if cid and str(cid) == str(resume_chapter_id):
                        resume_idx = idx
                        break
                if resume_idx >= 0:
                    self.log.info(f"▶ 从章节 '{self.mission_list[resume_idx].get('name')}' 续学")
                    self.mission_list = self.mission_list[resume_idx:]

        self.log.info(f"📊 共 {len(self.mission_list)} 个章节待处理 (模式: {self.learning_mode})")

        if self._check_stopped("开始刷课前"):
            return

        # ── 按模式分派 ──
        if self.learning_mode == self.MODE_STAGE:
            self._do_stage_mode(DealVideo, dao)
        else:
            self._do_single_pass_mode(DealVideo, dao)

    # ── 闯关模式：任务点循环重拉 + 章节严格门禁 ──
    def _do_stage_mode(self, DealVideo, dao=None):
        """闯关模式核心实现。

        痛点修复：闯关模式下章节内任务点是逐个解锁的（A 完成才解锁 B）。
        原实现一次性拉取章节附件列表后只遍历一次，刷完已解锁任务点就结束进程，
        **没有继续刷刚解锁的任务点**。

        本方法采用"循环重拉"策略：每完成一个任务点就重新调用 `deal_chapter`
        获取最新附件列表，发现新解锁的任务点继续刷，直到无新解锁为止。
        章节门禁：本章有任务点失败则停止后续章节（status='partial'）。
        """
        completed_tasks = 0
        for mission_item in self.mission_list:
            chapter_id = str(mission_item.get('id') or mission_item.get('chapter_id') or "")
            chapter_name = mission_item.get('name', '')

            # 停止检查点（章节之间）
            if self._check_stopped(f"章节'{chapter_name}'之前"):
                return

            self.log.info(f"开始处理章节'{chapter_name}'（闯关模式）")

            processed_object_ids = set()
            chapter_has_failure = False
            chapter_processed_count = 0

            while True:
                # 停止检查点（每轮重拉前）；停止时本章记 partial，下次可续学
                if self._check_stopped(f"章节'{chapter_name}'重拉"):
                    if dao and self.user_id:
                        dao.save_chapter_done(
                            self.user_id, self.course_id, chapter_id, chapter_name,
                            task_total=chapter_processed_count, task_done=chapter_processed_count,
                            status="partial")
                    return
                attach_list = self.deal_chapter(mission_item)
                if attach_list is False:
                    self.log.warning(f"章节'{chapter_name}'未开放，跳过")
                    break
                if not attach_list:
                    break

                new_found = False
                for attach_item in attach_list:
                    medias = attach_item.get("attachments") or []
                    for media in medias:
                        # 用 objectId 去重（同任务点多次出现不重复处理）
                        oid = (media.get("objectId")
                               or (media.get("property") or {}).get("objectId")
                               or media.get("jobid"))
                        if not oid:
                            # 无 ID 的任务点：用对象 id 兜底，保证至少处理一次
                            oid = f"__no_id_{id(media)}"
                        if oid in processed_object_ids:
                            continue
                        if media.get("isPassed"):
                            processed_object_ids.add(oid)
                            continue

                        ok, name = self._process_media(media, attach_item, mission_item, DealVideo)
                        processed_object_ids.add(oid)
                        new_found = True
                        chapter_processed_count += 1
                        completed_tasks += 1
                        if not ok:
                            chapter_has_failure = True

                        # 闯关核心：完成一个就 break，重新拉取以获取刚解锁的下一个任务点
                        self.log.info(f"   🔄 任务点'{name}'处理完毕，重新拉取章节附件以发现新解锁任务点...")
                        break
                    if new_found:
                        break

                if not new_found:
                    break

            # 章节门禁：本章有失败则停止后续章节
            if chapter_has_failure:
                self.log.warning(f"章节'{chapter_name}'闯关未通过，停止后续章节")
                if dao and self.user_id:
                    dao.save_chapter_done(
                        self.user_id, self.course_id, chapter_id, chapter_name,
                        task_total=chapter_processed_count, task_done=chapter_processed_count,
                        status="partial")
                break

            if dao and self.user_id:
                dao.save_chapter_done(
                    self.user_id, self.course_id, chapter_id, chapter_name,
                    task_total=chapter_processed_count, task_done=chapter_processed_count,
                    status="done")
            self.log.success(f"✓ 章节'{chapter_name}'闯关通过（处理 {chapter_processed_count} 个任务点）")

    # ── 顺序/并发/复习模式：单次遍历（保留原有行为） ──
    def _do_single_pass_mode(self, DealVideo, dao=None):
        """单次遍历模式：预拉取附件列表 + 一次性处理。

        - sequential：按章节顺序处理，不门禁（章节失败仍继续下一章）
        - concurrent：多线程并发刷取（行为同 sequential，并发体现在视频线程池）
        - review：忽略 isPassed 重做，不读进度
        """
        ignore_passed = self.learning_mode != self.MODE_REVIEW

        # 预拉取所有章节附件（与原 do_finish 一致）
        temp_mission_list = []
        for mission_item in self.mission_list:
            if self._check_stopped("预拉取章节附件"):
                break
            attach_list = self.deal_chapter(mission_item)
            if attach_list:
                temp_mission_list.append((mission_item, attach_list))

        # 计数待处理任务点：非 review 模式仅统计未通过；review 模式统计全部
        total_tasks = 0
        for _mission, attach_list in temp_mission_list:
            for attach_item in attach_list:
                for media in attach_item.get("attachments") or []:
                    if not media.get("isPassed") or not ignore_passed:
                        total_tasks += 1

        self.log.info(f"📊 共检测到 {total_tasks} 个待完成任务点")
        completed_tasks = 0

        for mission_item, attach_list in temp_mission_list:
            chapter_id = str(mission_item.get('id') or mission_item.get('chapter_id') or "")
            chapter_name = mission_item.get('name', '')

            # 停止检查点（章节之间）；sequential 停止时记 partial 供续学
            if self._check_stopped(f"章节'{chapter_name}'之前"):
                if (dao and self.user_id
                        and self.learning_mode == self.MODE_SEQUENTIAL
                        and not self.chapter_ids):
                    dao.save_chapter_done(
                        self.user_id, self.course_id, chapter_id, chapter_name,
                        task_done=completed_tasks, status="partial")
                return

            self.log.info(f"开始处理章节'{chapter_name}'")
            if attach_list is False:
                continue
            if not attach_list:
                continue
            chapter_failed = False

            for attach_item in attach_list:
                medias = attach_item.get("attachments") or []
                for media in medias:
                    if ignore_passed and media.get("isPassed"):
                        continue
                    # 停止检查点（任务点之间）
                    if self._check_stopped(f"章节'{chapter_name}'任务点处理中"):
                        if (dao and self.user_id
                                and self.learning_mode == self.MODE_SEQUENTIAL
                                and not self.chapter_ids):
                            dao.save_chapter_done(
                                self.user_id, self.course_id, chapter_id, chapter_name,
                                task_done=completed_tasks, status="partial")
                        return
                    ok, name = self._process_media(media, attach_item, mission_item, DealVideo)
                    completed_tasks += 1
                    if not ok:
                        chapter_failed = True
                    if total_tasks > 0:
                        progress = int((completed_tasks / total_tasks) * 50)
                        progress_bar = "[" + "█" * progress + "░" * (50 - progress) + "]"
                        percentage = int((completed_tasks / total_tasks) * 100)
                        self.log.info(f"\n进度: {progress_bar} {percentage}% ({completed_tasks}/{total_tasks})")

            # sequential 模式记录章节级进度（不门禁，但供下次续学参考）
            if (dao and self.user_id
                    and self.learning_mode == self.MODE_SEQUENTIAL
                    and not self.chapter_ids):
                dao.save_chapter_done(
                    self.user_id, self.course_id, chapter_id, chapter_name,
                    task_done=completed_tasks,
                    status="done" if not chapter_failed else "partial")

    # ── 单任务点处理（从原 do_finish 抽取，避免循环重拉里重复 100+ 行代码） ──
    def _process_media(self, media, attach_item, mission_item, DealVideo) -> tuple:
        """处理单个任务点，返回 (是否成功, 媒体名)。

        涵盖 Read / Live / Video / Audio / Document / Book / Work / BBS 各分支。
        异步任务（多线程视频/直播）返回 True（视为非阻塞失败）。
        """
        defaults = attach_item.get("defaults")
        property_dict = media.get("property") or {}
        media_type = media.get("type")
        media_module = property_dict.get("module", "")
        media_name = property_dict.get("name") or property_dict.get("title") or "未知任务"
        finish_status = False

        if media.get("job") is None:
            if is_read_attachment(media):
                self.log.info(f"开始处理阅读任务点:{media_name}")
                finish_status = Read(media, self.user.headers, defaults, self.course_id).do_finish()
                if finish_status:
                    self.log.success(f"任务点'{media_name}'完成成功")
                else:
                    self.log.error(f"任务点'{media_name}'完成失败")
            return finish_status, media_name

        if is_live_attachment(media):
            _live = Live(media, self.user.headers, defaults, self.course_id)
            _thread = threading.Thread(
                target=DealVideo.run_live,
                args=(_live, self.user, self.log, self._stop_event))
            if self.single_thread:
                self.log.info(f"开始处理直播任务点:{_live.name}")
                self.log.info(f"   模式: 单线程顺序刷取")
                _thread.start()
                _thread.join()
            else:
                self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                while len(self.thread_pool) >= self.max_concurrent_threads:
                    if self.is_stopped:
                        return False, _live.name
                    self.log.debug(f"   线程池已满 ({len(self.thread_pool)}/{self.max_concurrent_threads})，等待3秒后重试...")
                    self._interruptible_sleep(3)
                    self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                self.thread_pool.append(_thread)
                self.log.info(f"   模式: 多线程并发刷取 (最大并发: {self.max_concurrent_threads})")
                self.log.info(f"   已启动直播: '{_live.name}'")
                _thread.start()
                self._interruptible_sleep(random.random() + 0.5)
            return True, _live.name

        if media_type == "video":
            if media_module == "insertaudio":
                self.log.info(f"开始处理音频任务点:{media_name}")
                _audio = Video(media, self.user.headers, defaults, "Audio", name=media_name,
                               course_id=self.course_id, class_id=self.class_id,
                               userid=self.user.uid)
                _audio._stop_event = self._stop_event
                finish_status = _audio.do_finish()
            else:
                self.log.info(f"开始处理视频任务点:{media_name}")
                _video = Video(media, self.user.headers, defaults, name=media_name,
                               course_id=self.course_id, class_id=self.class_id,
                               userid=self.user.uid)
                _video._stop_event = self._stop_event
                if self.video_mode == 0:
                    self.log.info(f"   模式: 立即完成")
                    finish_status = _video.do_finish()
                    if not finish_status:
                        self.log.warning(f"   视频模式失败，尝试音频模式...")
                        _audio_fallback = Video(
                            media, self.user.headers, defaults, "Audio", name=media_name,
                            course_id=self.course_id, class_id=self.class_id,
                            userid=self.user.uid
                        )
                        _audio_fallback._stop_event = self._stop_event
                        finish_status = _audio_fallback.do_finish()
                else:
                    _thread = threading.Thread(
                        target=DealVideo.run_video,
                        args=(_video, self.user, self.log, self._stop_event))
                    if self.single_thread:
                        self.log.info(f"   模式: 单线程顺序刷取")
                        _thread.start()
                        _thread.join()
                        finish_status = True
                    else:
                        self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                        while len(self.thread_pool) >= self.max_concurrent_threads:
                            if self.is_stopped:
                                return False, media_name
                            self.log.debug(f"   线程池已满 ({len(self.thread_pool)}/{self.max_concurrent_threads})，等待3秒后重试...")
                            self._interruptible_sleep(3)
                            self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                        self.thread_pool.append(_thread)
                        self.log.info(f"   模式: 多线程并发刷取 (最大并发: {self.max_concurrent_threads})")
                        self.log.info(f"   已启动: '{media_name}'")
                        _thread.start()
                        self._interruptible_sleep(random.random() + 0.5)
            return finish_status, media_name

        if media_type == "document":
            self.log.info(f"开始处理Doc文件任务点:{media_name}")
            finish_status = Document(media, self.user.headers, defaults, self.course_id).do_finish()
            return finish_status, media_name

        if media_type == "live":
            _live = Live(media, self.user.headers, defaults, self.course_id)
            _thread = threading.Thread(
                target=DealVideo.run_live,
                args=(_live, self.user, self.log, self._stop_event))
            if self.single_thread:
                self.log.info(f"开始处理直播任务点:{_live.name}")
                _thread.start()
                _thread.join()
            else:
                self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                while len(self.thread_pool) >= self.max_concurrent_threads:
                    if self.is_stopped:
                        return False, _live.name
                    self._interruptible_sleep(3)
                    self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                self.thread_pool.append(_thread)
                _thread.start()
                self._interruptible_sleep(random.random() + 0.5)
            return True, _live.name

        if property_dict.get("bookname"):
            self.log.info(f"开始处理图书任务点:{media_name}")
            finish_status = Book(media, self.user.headers, defaults, self.course_id).do_finish()
            return finish_status, media_name

        if media_type == "workid" or (media.get("property") and media.get("property").get("module") == "work"):
            work_title = media.get("property", {}).get("title", media_name or "作业")
            worktype = media.get("property", {}).get("worktype", "") or media.get("worktype", "")
            is_chapter_quiz = worktype in ("workA", "worka")
            task_label = "章节测验" if is_chapter_quiz else "作业"
            self.log.info(f"开始处理{task_label}任务点:{work_title}")
            if self.answer_strategy == "ai" and self.ai_client and self.ai_client.is_configured():
                self.log.info("   使用 DeepSeek AI 答题")
            elif self.answer_strategy == "random":
                self.log.info("   使用随机选择策略")
            else:
                self.log.info("   使用选第一个策略")
            finish_status = Work(
                media, self.user.headers, defaults, self.course_id,
                self.answer_strategy, self.ai_client, tiku=self.tiku,
                clazzId=self.class_id, userid=self.user.uid,
                ktoken=defaults.get("ktoken", ""),
                knowledgeid=str(mission_item.get("knowledge_id", "")),
                cpi=defaults.get("cpi", "")
            ).do_finish()
            return finish_status, work_title

        if media_type in ("bbs", "discuss", "topic") or media_module in ("bbs", "discuss", "topic"):
            self.log.info(f"开始处理讨论任务点:{media_name}")
            finish_status = BBS(
                media, self.user.headers, defaults,
                course_id=self.course_id, class_id=self.class_id,
                ai_client=self.ai_client, strategy=self.answer_strategy
            ).do_finish()
            return finish_status, media_name

        self.log.warning(f"检测到不支持的任务点类型:{media_type} / module:{media_module}")
        loguru.logger.info(media)
        return False, media_name

    def deal_course(self):
        self.mission_list.clear()
        self.log.info(f"获取'{self.course_name}'课程的章节中...")
        self.course.get_chapter()
        if self.course.chapter_list:
            self.log.success(f"获取'{self.course_name}'课程章节成功，即将展示")
            time.sleep(0.4)
            total_unfinished = 0
            for catalog_item in self.course.chapter_list:
                print(catalog_item.get("catalog_name"))
                for chapter_item in catalog_item.get("child_chapter"):
                    unfinished = chapter_item.get("unfinished_count", 0)
                    total_unfinished += unfinished
                    status_text = f"    ✍待完成任务点 {unfinished}" if unfinished > 0 else f"    ✓已完成 ({chapter_item.get('completed_count', 0)}/{chapter_item.get('job_count', 0)})" if chapter_item.get('job_count', 0) > 0 else ""
                    print("----" * (chapter_item.get("depth") + 1), chapter_item.get("name"), status_text)
                    self.mission_list.append(chapter_item)
                print("🔷" * 35)
            self.log.success(f"'{self.course_name}'课程章节展示完毕，总共有 {total_unfinished} 个未完成任务点")
        else:
            self.log.warning(f"'{self.course_name}'课程章节数为零，请核实或检查网络问题。如有出入请反馈issue")

    def deal_chapter(self, chapter_item: dict):
        """
        处理章节内容，获得章节的具体任务点（adapted from reference implementation

        闯关模式下会多次调用本方法以重新拉取最新附件列表，发现刚解锁的任务点。

        :param chapter_item: 章节dict
        :return: 媒体页列表；章节未开放时返回 False
        """
        knowledge_id = chapter_item.get("knowledge_id")
        attach_list, job_info = fetch_chapter_attachments(
            self.user, self.class_id, self.course_id, self.cpi, knowledge_id
        )
        if job_info.get("notOpen"):
            self.log.warning(f"章节'{chapter_item.get('name')}'未开放，跳过")
            return False
        if not attach_list:
            if study_empty_page(self.user, self.class_id, self.course_id, self.cpi, knowledge_id):
                self.log.info(f"章节'{chapter_item.get('name')}'无任务点，已标记空页面完成")
            else:
                self.log.warning(f"章节'{chapter_item.get('name')}'空页面标记失败")
        return attach_list
