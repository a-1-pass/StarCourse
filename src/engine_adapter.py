"""
Engine adapter — bridges the GUI with the low-level task engine.

Wraps the ``engine`` package (User / Course / Media handlers) behind a clean
API so the GUI layer never touches engine internals directly.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any

# Ensure the engine package is importable as flat modules (legacy design)
_ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

log = logging.getLogger("starcourse.adapter")


@dataclass
class CourseBrief:
    """Lightweight course descriptor returned to the GUI."""
    course_id: str
    class_id: str
    cpi: str
    name: str
    author: str
    if_open: bool


class EngineAdapter:
    """High-level façade around the engine package."""

    def __init__(self):
        self._init_engine_config()
        self._user = None
        self._courses: list[CourseBrief] = []

    # ---- lifecycle ----
    def _init_engine_config(self):
        try:
            from config import GloConfig  # type: ignore[import-not-found]
            GloConfig.init_yaml_data()
            log.info("Engine config initialised")
        except Exception as exc:
            log.error(f"Engine config init failed: {exc}")

    def login(self, username: str, password: str) -> tuple[bool, str]:
        try:
            from classis.SelfException import LoginException  # type: ignore[import-not-found]
            from classis.User import User  # type: ignore[import-not-found]

            try:
                self._user = User(username=username, password=password)
                log.info(f"Login OK — {self._user.name}")
                return True, f"Login successful — welcome {self._user.name}"
            except LoginException as exc:
                return False, str(exc)
            except Exception as exc:
                return False, f"Login failed: {exc}"
        except Exception as exc:
            return False, f"Engine unavailable: {exc}"

    @property
    def cookie(self) -> str:
        """Return the raw cookie string from the current user session, or ''."""
        if self._user is None:
            return ""
        try:
            return self._user.headers.get("Cookie", "")
        except Exception:
            return ""

    def login_with_cookie(self, cookie: str) -> tuple[bool, str]:
        try:
            from classis.SelfException import LoginException  # type: ignore[import-not-found]
            from classis.User import User  # type: ignore[import-not-found]

            try:
                self._user = User(cookieStr=cookie)
                log.info(f"Cookie login OK — {self._user.name}")
                return True, f"Login successful — welcome {self._user.name}"
            except LoginException as exc:
                return False, str(exc)
            except Exception as exc:
                return False, f"Login failed: {exc}"
        except Exception as exc:
            return False, f"Engine unavailable: {exc}"

    # ---- course listing ----
    def fetch_courses(self) -> tuple[bool, list[CourseBrief], str]:
        if not self._user:
            return False, [], "Please log in first"

        try:
            self._user.getCourse()
            self._courses.clear()
            for c in self._user.course_list:
                self._courses.append(CourseBrief(
                    course_id=c.course_id, class_id=c.class_id, cpi=c.cpi,
                    name=c.course_name, author=c.course_author, if_open=c.ifOpen,
                ))
            log.info(f"Retrieved {len(self._courses)} courses")
            return True, self._courses, "OK"
        except Exception as exc:
            return False, [], f"Failed to fetch courses: {exc}"

    def _find_course(self, course_id: str):
        if not self._user:
            return None
        for c in self._user.course_list:
            if c.course_id == course_id:
                return c
        return None

    # ---- chapter listing ----
    def fetch_chapters(self, course_id: str, class_id: str, cpi: str = "") -> tuple[bool, list[dict], str]:
        course = self._find_course(course_id)
        if not course:
            return False, [], "Course not found"
        try:
            course.get_chapter()
            chapters = []
            for catalog in course.chapter_list:
                for child in catalog.get("child_chapter", []):
                    if child.get("knowledge_id"):
                        chapters.append({
                            "chapter_id": child.get("knowledge_id"),
                            "name": child.get("name"),
                            "job_count": child.get("job_count", 0),
                            "depth": child.get("depth", 0),
                        })
            log.info(f"Found {len(chapters)} chapters")
            return True, chapters, "OK"
        except Exception as exc:
            return False, [], f"Failed to fetch chapters: {exc}"

    # ---- task discovery ----
    def fetch_tasks(self, course_id: str, class_id: str, chapter_id: str, cpi: str = "") -> tuple[bool, list[dict], str]:
        course = self._find_course(course_id)
        if not course:
            return False, [], "Course not found"
        try:
            from card_decode import fetch_chapter_attachments  # type: ignore[import-not-found]

            pages, job_info = fetch_chapter_attachments(self._user, class_id, course_id, cpi or course.cpi, chapter_id)
            if job_info.get("notOpen"):
                return False, [], "Chapter is not open"

            tasks: list[dict] = []
            for page in pages:
                defaults = page.get("defaults", {})
                for att in page.get("attachments", []):
                    task = self._classify_attachment(att, defaults)
                    if task:
                        tasks.append(task)
            log.info(f"Discovered {len(tasks)} task items")
            return True, tasks, "OK"
        except Exception as exc:
            return False, [], f"Failed to fetch tasks: {exc}"

    # ---- individual task handlers ----
    def run_read_task(self, task: dict, course_id: str = "") -> bool:
        try:
            from classis.Media.Read import Read  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            title = attachment.get("property", {}).get("name", "unknown")
            cid = course_id or defaults.get("courseId", "")

            if task.get("is_passed"):
                log.info("Read task already complete — skipping")
                return True

            reader = Read(attachment, self._user.headers, defaults, cid)
            ok = reader.do_finish()
            log.info(f"Read '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Read task error: {exc}")
            return False

    def run_video_task(self, task: dict) -> bool:
        try:
            from classis.Media.Video import Video  # type: ignore[import-not-found]
            from config import GloConfig  # type: ignore[import-not-found]

            defaults = task.get("defaults", {})
            cid = defaults.get("courseId", "")
            clid = defaults.get("clazzId", "")
            attachment = task.get("attachment", {})
            module = attachment.get("property", {}).get("module", "")
            title = attachment.get("property", {}).get("name", "unknown")

            if task.get("is_passed"):
                log.info("Video already complete — skipping")
                return True

            video_mode = GloConfig.data.get("FunConfig", {}).get("deal-mission", {}).get("video-mode", 0)

            if module == "insertaudio":
                video = Video(attachment, self._user.headers, defaults, "Audio", title, course_id=cid, class_id=clid, userid=self._user.uid)
            else:
                video = Video(attachment, self._user.headers, defaults, name=title, course_id=cid, class_id=clid, userid=self._user.uid)

            if video_mode == 0:
                ok = video.do_finish()
            else:
                from functions.set_time import DealVideo  # type: ignore[import-not-found]
                ok = DealVideo.run_video(video, self._user, self._make_logger())

            log.info(f"Video '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Video task error: {exc}")
            return False

    def run_quiz_task(self, task: dict, course_id: str = "", strategy: str = "first",
                      use_tiku: bool = False) -> bool:
        try:
            from classis.Media.Quiz import Quiz  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            title = attachment.get("property", {}).get("title", "unknown")
            cid = course_id or defaults.get("courseId", "")

            if task.get("is_passed"):
                log.info("Quiz already complete — skipping")
                return True

            quiz = Quiz(attachment, self._user.headers, defaults, cid, self._get_ai(), strategy,
                        tiku=(self._get_tiku() if use_tiku else None))
            ok = quiz.do_finish()
            log.info(f"Quiz '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Quiz task error: {exc}")
            return False

    def run_work_task(self, task: dict, course_id: str = "", strategy: str = "first",
                      use_tiku: bool = False) -> bool:
        try:
            from classis.Media.Work import Work  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            title = attachment.get("property", {}).get("title", "unknown")
            cid = course_id or defaults.get("courseId", "")

            if task.get("is_passed"):
                log.info("Work already complete — skipping")
                return True

            work = Work(attachment, self._user.headers, defaults, cid, strategy, self._get_ai(),
                        tiku=(self._get_tiku() if use_tiku else None))
            ok = work.do_finish()
            log.info(f"Work '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Work task error: {exc}")
            return False

    def run_document_task(self, task: dict, course_id: str = "") -> bool:
        try:
            from classis.Media.Document import Document  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            title = attachment.get("property", {}).get("name", "unknown")
            cid = course_id or defaults.get("courseId", "")

            if task.get("is_passed"):
                log.info("Document already complete — skipping")
                return True

            doc = Document(attachment, self._user.headers, defaults, cid)
            ok = doc.do_finish()
            log.info(f"Document '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Document task error: {exc}")
            return False

    def run_book_task(self, task: dict, course_id: str = "") -> bool:
        try:
            from classis.Media.Book import Book  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            title = attachment.get("property", {}).get("name", "unknown")
            cid = course_id or defaults.get("courseId", "")

            if task.get("is_passed"):
                log.info("Book already complete — skipping")
                return True

            book = Book(attachment, self._user.headers, defaults, cid)
            ok = book.do_finish()
            log.info(f"Book '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Book task error: {exc}")
            return False

    def run_bbs_task(self, task: dict, course_id: str = "", class_id: str = "", strategy: str = "random") -> bool:
        """Handle discussion-forum (BBS) tasks."""
        try:
            from classis.Media.BBS import BBS  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            props = attachment.get("property", {})
            title = props.get("name", props.get("title", "unknown"))
            cid = course_id or defaults.get("courseId", "")
            clid = class_id or defaults.get("clazzId", "")

            if task.get("is_passed"):
                log.info("BBS already complete — skipping")
                return True

            bbs = BBS(attachment, self._user.headers, defaults, cid, clid, self._get_ai(), strategy)
            ok = bbs.do_finish()
            log.info(f"BBS '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"BBS task error: {exc}")
            return False

    def run_live_task(self, task: dict, course_id: str = "") -> bool:
        """Handle live-streaming tasks."""
        try:
            from classis.Media.Live import Live  # type: ignore[import-not-found]
            defaults = task.get("defaults", {})
            attachment = task.get("attachment", {})
            title = attachment.get("property", {}).get("title", "unknown")
            cid = course_id or defaults.get("courseId", "")

            if task.get("is_passed"):
                log.info("Live already complete — skipping")
                return True

            live = Live(attachment, self._user.headers, defaults, cid)
            ok = live.do_finish()
            log.info(f"Live '{title}': {'done' if ok else 'failed'}")
            return ok
        except Exception as exc:
            log.error(f"Live task error: {exc}")
            return False

    # ---- bulk course processing ----
    def auto_complete(
        self,
        course_id: str,
        class_id: str,
        strategy: str = "first",
        cpi: str = "",
        on_progress=None,
        chapter_ids: list[str] | None = None,
        multi_thread: bool = False,
        max_threads: int = 5,
        use_tiku: bool = None,
    ) -> tuple[bool, dict, str]:
        course = self._find_course(course_id)
        if not course:
            return False, {}, "Course not found"
        if not course.ifOpen:
            return False, {}, "Course is closed or locked"

        stats = {"chapters": 0, "videos": 0, "quizzes": 0, "completed": 0, "failed": 0}

        try:
            from functions.deal_mission.deal_course import DealCourse  # type: ignore[import-not-found]

            progress_logger = _ProgressBridge(on_progress)
            ai = self._get_ai()

            if on_progress:
                on_progress("=" * 50)
                on_progress("Starting course automation...")
                from config import GloConfig  # type: ignore[import-not-found]
                vm = GloConfig.data.get("FunConfig", {}).get("deal-mission", {}).get("video-mode", 0)
                on_progress(f"  Video mode: {'timed' if vm == 1 else 'instant'}")
                on_progress(f"  Threading:  {'multi (' + str(max_threads) + ' workers)' if multi_thread else 'single'}")
                on_progress(f"  Strategy:   {strategy}")
                on_progress(f"  Tiku path:  {'on' if use_tiku is not False else 'off'} (题库/AI 双路径独立)")
                if chapter_ids:
                    on_progress(f"  Chapters:   {len(chapter_ids)} selected")
                on_progress("=" * 50)

            runner = DealCourse(self._user, course, progress_logger, strategy, ai, chapter_ids,
                                multi_thread, max_threads, use_tiku=use_tiku)
            runner.do_finish()

            # join background video threads
            if runner.thread_pool:
                if on_progress:
                    on_progress(f"Waiting for {len(runner.thread_pool)} video workers...")
                for idx, thr in enumerate(runner.thread_pool):
                    thr.join()
                    if on_progress:
                        on_progress(f"  Worker {idx + 1}/{len(runner.thread_pool)} finished")
                stats["videos"] = len(runner.thread_pool)

            ok, chapters, _ = self.fetch_chapters(course_id, class_id, cpi)
            if ok:
                stats["chapters"] = len(chapters)

            return True, stats, "Course automation completed"
        except Exception as exc:
            log.error(f"Automation failure: {exc}")
            return False, stats, f"Automation error: {exc}"

    # ---- sign-in ----

    def scan_sign_ins(self) -> tuple[bool, list[dict], str]:
        """Scan all courses for active (unfinished) sign-in activities."""
        if not self._user:
            return False, [], "Please log in first"
        try:
            from classis.Sign import Sign  # type: ignore[import-not-found]
            activities = Sign.scan_all_courses(self._user)
            log.info(f"Sign-in scan: {len(activities)} pending")
            return True, activities, f"Found {len(activities)} pending sign-in(s)"
        except Exception as exc:
            return False, [], f"Sign-in scan failed: {exc}"

    def do_sign_in(self, course_id: str, class_id: str, activity: dict,
                   location: tuple = None, name: str = "") -> tuple[bool, str]:
        """Execute a single sign-in for the specified course.

        Args:
            course_id: target course
            class_id: target class
            activity: the raw activity dict from ``scan_sign_ins``
            location: optional (lat, lon) tuple for location sign-in
            name: optional student name override
        """
        course = self._find_course(course_id)
        if not course:
            return False, "Course not found"
        try:
            from classis.Sign import Sign  # type: ignore[import-not-found]
            signer = Sign(self._user, course)
            result = signer.smart_sign(activity, location, name)
            if result.get("status") == "success":
                log.info(f"Sign-in OK: {result.get('name')}")
                return True, f"Sign-in success: {result.get('name')}"
            else:
                msg = result.get("message", "unknown error")
                log.warning(f"Sign-in failed: {msg}")
                return False, f"Sign-in failed: {msg}"
        except Exception as exc:
            return False, f"Sign-in error: {exc}"

    def auto_sign_all(self, on_progress=None) -> tuple[bool, list[dict], str]:
        """Auto-sign all pending sign-ins across all courses."""
        if not self._user:
            return False, [], "Please log in first"
        results: list[dict] = []
        try:
            from classis.Sign import Sign  # type: ignore[import-not-found]
            for course in self._user.course_list:
                if not course.ifOpen:
                    continue
                signer = Sign(self._user, course)
                pending = signer.get_unsign_list()
                if not pending:
                    continue
                msg = f"Course [{course.course_name}]: {len(pending)} pending"
                log.info(msg)
                if on_progress:
                    on_progress(msg)
                for act in pending:
                    name = act.get("name", "?")
                    if on_progress:
                        on_progress(f"  Signing: {name} ...")
                    r = signer.smart_sign(act)
                    r["course_name"] = course.course_name
                    results.append(r)
                    if on_progress:
                        on_progress(f"  → {r.get('status', '?')}")
            ok = any(r.get("status") == "success" for r in results)
            return ok, results, f"Processed {len(results)} sign-in(s)"
        except Exception as exc:
            return False, results, f"Auto-sign error: {exc}"

    def sign_in_by_type(self, sign_type: int, location: tuple = None,
                         on_progress=None) -> tuple[bool, list[dict], str]:
        """Scan and sign-in only activities of a specific type.

        Args:
            sign_type: int matching SignType enum (0=normal, 1=qrcode,
                       2=photo, 3=gesture, 4=location)
            location: optional (lat, lon) for location sign-in
            on_progress: optional callback(msg)
        """
        if not self._user:
            return False, [], "请先登录"
        try:
            from classis.Sign import Sign, SignType, detect_sign_type  # type: ignore[import-not-found]
            type_labels = {0: "普通签到", 1: "二维码签到", 2: "拍照签到",
                           3: "手势签到", 4: "位置签到"}
            type_name = type_labels.get(sign_type, f"类型{sign_type}")
            if on_progress:
                on_progress(f"开始扫描 {type_name} 活动...")

            results: list[dict] = []
            for course in self._user.course_list:
                if not course.ifOpen:
                    continue
                signer = Sign(self._user, course)
                pending = [a for a in signer.get_unsign_list()
                           if detect_sign_type(a) == sign_type]
                if not pending:
                    continue
                if on_progress:
                    on_progress(f"课程 [{course.course_name}]: 发现 {len(pending)} 个{type_name}活动")
                for act in pending:
                    name = act.get("name", "?")
                    if on_progress:
                        on_progress(f"  正在签到: {name} ...")
                    r = signer.smart_sign(act, location)
                    r["course_name"] = course.course_name
                    results.append(r)
                    status = r.get("status", "?")
                    if on_progress:
                        on_progress(f"  → {status}  {r.get('message', '')}")
            ok = any(r.get("status") == "success" for r in results)
            return ok, results, f"{type_name}: 处理 {len(results)} 个活动"
        except Exception as exc:
            return False, [], f"签到错误: {exc}"

    # ---- resource download ----

    def scan_downloadable(self, course_id: str, class_id: str = "",
                          include_attachments: bool = True) -> tuple[bool, list[dict], str]:
        """Scan a course's chapters for downloadable media and resource files.

        Parameters:
            course_id: Target course ID.
            class_id:  Class ID (auto-resolved from course if empty).
            include_attachments: Also scan for courseware / document attachments.

        Returns:
            (ok, items, message) where each item has keys:
            name, objectid, filename, type, module, chapter_name, course_name,
            resource_kind ('media' | 'attachment'), defaults, raw_attachment.
        """
        course = self._find_course(course_id)
        if not course:
            return False, [], "Course not found"
        try:
            from card_decode import fetch_chapter_attachments  # type: ignore[import-not-found]
            items: list[dict] = []
            course.get_chapter()
            actual_class_id = class_id or course.class_id

            for catalog in course.chapter_list:
                chapter_name = catalog.get("name", "")
                for chapter in catalog.get("child_chapter", []):
                    kid = chapter.get("knowledge_id")
                    if not kid:
                        continue
                    try:
                        pages, job_info = fetch_chapter_attachments(
                            self._user, actual_class_id, course_id, course.cpi, kid
                        )
                        if job_info.get("notOpen"):
                            continue
                        for page in pages:
                            atts = page.get("attachments", [])
                            defaults = page.get("defaults", {})
                            for att in atts:
                                props = att.get("property", {})
                                objid = props.get("objectid", "") or att.get("objectId", "")
                                fname = props.get("name", "") or props.get("bookname", "")
                                if not objid or not fname:
                                    continue

                                module_name = props.get("module", "")
                                att_type = att.get("type", "unknown")

                                # Classify as media or attachment
                                media_modules = ("video", "insertvideo", "audio", "flash")
                                attachment_modules = (
                                    "attachment", "insertdoc", "pptx", "ppt",
                                    "document", "docx", "doc", "pdf", "txt",
                                    "zip", "rar", "7z", "xls", "xlsx",
                                )

                                if module_name in media_modules or att_type in ("video", "audio"):
                                    resource_kind = "media"
                                elif module_name in attachment_modules or att_type in attachment_modules:
                                    resource_kind = "attachment"
                                elif "ppt" in fname.lower() or "doc" in fname.lower() or "pdf" in fname.lower():
                                    resource_kind = "attachment"
                                else:
                                    resource_kind = "media"

                                items.append({
                                    "name": fname,
                                    "objectid": objid,
                                    "type": att_type,
                                    "module": module_name,
                                    "resource_kind": resource_kind,
                                    "chapter_name": chapter.get("name", chapter_name),
                                    "course_name": course.course_name,
                                    "defaults": defaults,
                                    "attachment": att,
                                })
                    except Exception as exc:
                        log.debug(f"Skip chapter {kid}: {exc}")
                        continue
            log.info(f"Scanned: {len(items)} downloadable files "
                     f"(media={sum(1 for i in items if i['resource_kind']=='media')}, "
                     f"attachment={sum(1 for i in items if i['resource_kind']=='attachment')})")
            return True, items, f"Found {len(items)} file(s)"
        except Exception as exc:
            return False, [], f"Scan failed: {exc}"

    def download_file(self, course_id: str, file_info: dict,
                      output_dir: str = "./downloads") -> tuple[bool, str, str]:
        """Download a single media or attachment file using its objectid.

        Returns (ok, message, saved_file_path).
        """
        import re as _re
        import requests
        objid = file_info.get("objectid")
        fname = file_info.get("name", "unknown")
        if not objid:
            return False, "Missing objectid", ""

        course = self._find_course(course_id)
        course_name = course.course_name if course else "unknown"
        # Sanitise course name for directory
        safe_course = _re.sub(r'[\\/:*?"<>|]', '_', course_name).strip()
        course_dir = os.path.join(output_dir, safe_course or "unknown_course")

        try:
            import time as _time
            from utils import doGet  # type: ignore[import-not-found]

            status_url = f"https://mooc1-2.chaoxing.com/ananas/status/{objid}?_dc={int(_time.time() * 1000)}"
            _headers = self._user.headers.copy()
            _headers.update({
                "Accept": "*/*",
                "Host": "mooc1-2.chaoxing.com",
                "Referer": "https://mooc1-2.chaoxing.com/ananas/modules/video/index.html",
                "X-Requested-With": "XMLHttpRequest",
            })

            resp = doGet(url=status_url, headers=_headers)
            status_json = json.loads(resp) if isinstance(resp, str) else resp.json()

            # Try multiple possible download URL fields
            dl_url = (
                status_json.get("httphd")
                or status_json.get("http")
                or status_json.get("pdf")
                or status_json.get("url")
                or status_json.get("download")
            )
            real_filename = status_json.get("filename", fname)
            # Sanitise filename
            real_filename = _re.sub(r'[\\/:*?"<>|]', '_', real_filename).strip()
            if not real_filename:
                real_filename = fname
            if not dl_url:
                # Try broader search in status_json values
                for key in ("httphd", "http", "pdf", "url", "download",
                            "headHttp", "videoPath", "filePath", "link"):
                    val = status_json.get(key)
                    if isinstance(val, str) and val.startswith("http"):
                        dl_url = val
                        break
                if not dl_url:
                    return False, f"No download URL found for {fname}", ""

            os.makedirs(course_dir, exist_ok=True)
            safe_path = os.path.join(course_dir, real_filename)

            # Avoid overwriting — append suffix
            if os.path.exists(safe_path):
                base, ext = os.path.splitext(real_filename)
                safe_path = os.path.join(course_dir, f"{base}-{int(_time.time())}{ext}")

            dl_headers = {
                "Referer": "https://mooc1-2.chaoxing.com/ananas/modules/video/index.html",
                "Host": "s1.ananas.chaoxing.com",
            }
            dl_headers.update(self._user.headers)

            r = requests.get(dl_url, headers=dl_headers, stream=True, timeout=180)
            r.raise_for_status()
            total = 0
            with open(safe_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    total += len(chunk)
            log.info(f"Downloaded: {real_filename} ({total} bytes) → {safe_path}")
            return True, f"Saved ({total} bytes)", safe_path
        except Exception as exc:
            return False, f"Download failed: {exc}", ""

    # ---- helpers ----
    def _classify_attachment(self, att: dict, defaults: dict) -> dict | None:
        from card_decode import is_live_attachment, is_read_attachment  # type: ignore[import-not-found]

        if att.get("isPassed"):
            return None

        jobid = att.get("jobid", "") or att.get("_jobid", "")
        if not jobid and not is_read_attachment(att):
            return None

        task_type = att.get("type", "")
        props = att.get("property", {})
        if not isinstance(props, dict):
            props = {}

        workid = props.get("workid", "") or att.get("workid", "")
        worktype = props.get("worktype", "") or att.get("worktype", "")
        other = att.get("otherInfo", "")
        enc = att.get("enc", "")
        if not enc:
            import re
            m = re.search(r"enc_([a-f0-9]+)", other)
            if m:
                enc = m.group(1)

        entry = {
            "jobid": jobid, "type": task_type, "is_passed": att.get("isPassed", False),
            "workid": workid, "worktype": worktype, "mid": att.get("mid", ""),
            "enc": enc, "objectId": att.get("objectId", ""),
            "attDuration": att.get("attDuration", 0), "playTime": att.get("playTime", 0),
            "otherInfo": other, "defaults": defaults, "attachment": att,
        }

        if workid:
            entry["task_type"] = "quiz" if worktype in ("workA", "worka") else "work"
        elif task_type == "video":
            entry["task_type"] = "video"
        elif is_live_attachment(att):
            entry["task_type"] = "live"
        elif is_read_attachment(att) or task_type == "read":
            entry["task_type"] = "read"
        elif task_type in ("document", "ppt", "pptx", "pdf", "doc", "docx"):
            entry["task_type"] = "document"
        elif task_type in ("bbs", "discuss", "topic", "hwork") or props.get("module", "") in ("bbs", "discuss", "topic"):
            entry["task_type"] = "bbs"
        elif props.get("bookname"):
            entry["task_type"] = "book"
        else:
            entry["task_type"] = "other"
        return entry

    def _get_ai(self):
        try:
            core_dir = os.path.dirname(os.path.abspath(__file__))
            if core_dir not in sys.path:
                sys.path.insert(0, core_dir)
            # 优先：多模型统一架构（DeepSeek/GPT/Claude/文心，按优先级容灾）
            try:
                from ai_providers import build_multi_client  # type: ignore[import-not-found]
                multi = build_multi_client()
                if multi is not None:
                    return multi
            except Exception:
                pass
            # 回退：历史单客户端
            try:
                from deepseek_ai_enhanced import DeepSeekAI  # type: ignore[import-not-found]
                return DeepSeekAI()
            except Exception:
                from src.ai_assistant import DeepSeekAI
                return DeepSeekAI()
        except Exception:
            return None

    def _get_tiku(self):
        """构建题库客户端（题库答题路径专用，与 AI 路径相互独立）。"""
        try:
            core_dir = os.path.dirname(os.path.abspath(__file__))
            if core_dir not in sys.path:
                sys.path.insert(0, core_dir)
            from tiku import Tiku  # type: ignore[import-not-found]
            from config import GloConfig  # type: ignore[import-not-found]
            tiku_config = GloConfig.data.get("TikuConfig", {})
            if not (tiku_config and tiku_config.get("provider")):
                return None
            tiku = Tiku()
            tiku.config_set(tiku_config)
            tiku.get_tiku_from_config()
            if tiku.DISABLE:
                return None
            return tiku
        except Exception:
            return None

    def get_answer_mode_status(self) -> dict:
        """题库/AI 两条答题路径的就绪状态（GUI 界面提示用，不做网络请求）。"""
        provider = ""
        try:
            core_dir = os.path.dirname(os.path.abspath(__file__))
            if core_dir not in sys.path:
                sys.path.insert(0, core_dir)
            from config import GloConfig  # type: ignore[import-not-found]
            provider = str(GloConfig.data.get("TikuConfig", {}).get("provider", "") or "")
        except Exception:
            pass
        ai = self._get_ai()
        return {
            "tiku_ready": bool(provider) and self._get_tiku() is not None,
            "tiku_provider": provider,
            "ai_ready": bool(ai and ai.is_configured()),
        }

    @staticmethod
    def _make_logger():
        class _SimpleLog:
            def info(self, m): log.info(m)
            def warning(self, m): log.warning(m)
            def error(self, m): log.error(m)
            def success(self, m): log.info(f"[OK] {m}")
        return _SimpleLog()


class _ProgressBridge:
    """Forwards log calls to an optional GUI callback, adding thread-name context."""
    def __init__(self, callback):
        self._cb = callback

    def _tag(self) -> str:
        name = threading.current_thread().name
        return f"[{name}] " if "Thread-" in name else ""

    def info(self, msg):
        log.info(msg)
        if self._cb:
            self._cb(self._tag() + msg)

    def warning(self, msg):
        log.warning(msg)
        if self._cb:
            self._cb(self._tag() + f"[!] {msg}")

    def error(self, msg):
        log.error(msg)
        if self._cb:
            self._cb(self._tag() + f"[X] {msg}")

    def debug(self, msg):
        log.debug(msg)

    def success(self, msg):
        log.info(f"[OK] {msg}")
        if self._cb:
            self._cb(self._tag() + f"[OK] {msg}")
