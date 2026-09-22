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
    def __init__(self, user: classis.User.User, course: classis.User.Course, log, answer_strategy: str = "first",
                 ai_client=None, chapter_ids: list = None,
                 enable_multi_thread: bool = None, max_concurrent_threads: int = None,
                 use_tiku: bool = None):
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
        
        if not self.enable_multi_thread:
            self.single_thread = True

    def do_finish(self):
        if not self.course.ifOpen:
            self.log.warning("本课程已结课或锁定，将自动跳过")
            return
        from functions.set_time import DealVideo
        self.deal_course()
        self.thread_pool.clear()
        if self.mission_list:
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
            
            total_tasks = 0
            temp_mission_list = []
            for mission_item in self.mission_list:
                attach_list = self.deal_chapter(mission_item)
                if attach_list:
                    temp_mission_list.append((mission_item, attach_list))
                    for attach_item in attach_list:
                        medias = attach_item.get("attachments")
                        for media in medias:
                            if not media.get("isPassed"):
                                total_tasks += 1
            
            self.log.info(f"📊 共检测到 {total_tasks} 个待完成任务点")
            completed_tasks = 0
            
            for mission_item, attach_list in temp_mission_list:
                self.log.info(f"开始处理章节'{mission_item.get('name')}'")
                if attach_list is False:
                    continue
                if attach_list:
                    for attach_item in attach_list:
                        medias = attach_item.get("attachments")
                        defaults = attach_item.get("defaults")
                        for media in medias:
                            if media.get("isPassed"):
                                continue

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
                                continue

                            if is_live_attachment(media):
                                _live = Live(media, self.user.headers, defaults, self.course_id)
                                _thread = threading.Thread(target=DealVideo.run_live, args=(_live, self.user, self.log))
                                if self.single_thread:
                                    self.log.info(f"开始处理直播任务点:{_live.name}")
                                    self.log.info(f"   模式: 单线程顺序刷取")
                                    _thread.start()
                                    _thread.join()
                                else:
                                    self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                                    while len(self.thread_pool) >= self.max_concurrent_threads:
                                        self.log.debug(f"   线程池已满 ({len(self.thread_pool)}/{self.max_concurrent_threads})，等待3秒后重试...")
                                        time.sleep(3)
                                        self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                                    self.thread_pool.append(_thread)
                                    self.log.info(f"   模式: 多线程并发刷取 (最大并发: {self.max_concurrent_threads})")
                                    self.log.info(f"   已启动直播: '{_live.name}'")
                                    _thread.start()
                                    time.sleep(random.random() + 0.5)
                                continue

                            if media_type == "video":
                                if media_module == "insertaudio":
                                    self.log.info(f"开始处理音频任务点:{media_name}")
                                    finish_status = Video(media, self.user.headers, defaults, "Audio", name=media_name,
                                                          course_id=self.course_id, class_id=self.class_id, 
                                                          userid=self.user.uid).do_finish()
                                else:
                                    self.log.info(f"开始处理视频任务点:{media_name}")
                                    _video = Video(media, self.user.headers, defaults, name=media_name,
                                                  course_id=self.course_id, class_id=self.class_id,
                                                  userid=self.user.uid)
                                    if self.video_mode == 0:
                                        self.log.info(f"   模式: 立即完成")
                                        finish_status = _video.do_finish()
                                        if not finish_status:
                                            self.log.warning(f"   视频模式失败，尝试音频模式...")
                                            finish_status = Video(
                                                media, self.user.headers, defaults, "Audio", name=media_name,
                                                course_id=self.course_id, class_id=self.class_id,
                                                userid=self.user.uid
                                            ).do_finish()
                                    else:
                                        _thread = threading.Thread(target=DealVideo.run_video, args=(_video, self.user, self.log))
                                        if self.single_thread:
                                            self.log.info(f"   模式: 单线程顺序刷取")
                                            _thread.start()
                                            _thread.join()
                                            finish_status = True
                                        else:
                                            self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                                            while len(self.thread_pool) >= self.max_concurrent_threads:
                                                self.log.debug(f"   线程池已满 ({len(self.thread_pool)}/{self.max_concurrent_threads})，等待3秒后重试...")
                                                time.sleep(3)
                                                self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                                            self.thread_pool.append(_thread)
                                            self.log.info(f"   模式: 多线程并发刷取 (最大并发: {self.max_concurrent_threads})")
                                            self.log.info(f"   已启动: '{media_name}'")
                                            _thread.start()
                                            time.sleep(random.random() + 0.5)
                                        continue

                            elif media_type == "document":
                                self.log.info(f"开始处理Doc文件任务点:{media_name}")
                                finish_status = Document(media, self.user.headers, defaults, self.course_id).do_finish()
                            elif media_type == "live":
                                _live = Live(media, self.user.headers, defaults, self.course_id)
                                _thread = threading.Thread(target=DealVideo.run_live, args=(_live, self.user, self.log))
                                if self.single_thread:
                                    self.log.info(f"开始处理直播任务点:{_live.name}")
                                    _thread.start()
                                    _thread.join()
                                else:
                                    self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                                    while len(self.thread_pool) >= self.max_concurrent_threads:
                                        time.sleep(3)
                                        self.thread_pool = [t for t in self.thread_pool if t.is_alive()]
                                    self.thread_pool.append(_thread)
                                    _thread.start()
                                    time.sleep(random.random() + 0.5)
                                continue

                            elif property_dict.get("bookname"):
                                self.log.info(f"开始处理图书任务点:{media_name}")
                                finish_status = Book(media, self.user.headers, defaults, self.course_id).do_finish()
                            elif media_type == "workid" or (media.get("property") and media.get("property").get("module") == "work"):
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
                                media_name = work_title
                            elif media_type in ("bbs", "discuss", "topic") or media_module in ("bbs", "discuss", "topic"):
                                self.log.info(f"开始处理讨论任务点:{media_name}")
                                finish_status = BBS(
                                    media, self.user.headers, defaults,
                                    course_id=self.course_id, class_id=self.class_id,
                                    ai_client=self.ai_client, strategy=self.answer_strategy
                                ).do_finish()
                            else:
                                self.log.warning(f"检测到不支持的任务点类型:{media_type} / module:{media_module}")
                                loguru.logger.info(media)
                                continue

                            if finish_status:
                                self.log.success(f"任务点'{media_name}'完成成功")
                            else:
                                self.log.error(f"任务点'{media_name}'完成失败")
                            
                            completed_tasks += 1
                            if total_tasks > 0:
                                progress = int((completed_tasks / total_tasks) * 50)
                                progress_bar = "[" + "█" * progress + "░" * (50 - progress) + "]"
                                percentage = int((completed_tasks / total_tasks) * 100)
                                self.log.info(f"\n进度: {progress_bar} {percentage}% ({completed_tasks}/{total_tasks})")

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
