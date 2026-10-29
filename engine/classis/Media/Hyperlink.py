# _*_ coding:utf-8 _*_
import json
import time

import loguru

from classis.Media import Media
from utils import doGet


class Hyperlink(Media):
    """超链接任务点。

    处理分两步：
    1. 打开超链接地址（best-effort，失败不阻断）；
    2. 调用 ``ananas/job/url`` 上报任务点完成。
    """

    def __init__(self, attachment: dict, headers, defaults: dict = None,
                 courseId: str = "", classId: str = ""):
        super().__init__(attachment, headers)
        self.defaults = defaults or {}
        self.courseId = courseId
        self.classId = classId

    def _extract_url(self) -> str:
        """从附件多个可能的字段中尽力提取超链接地址。"""
        props = self.attachment.get("property", {})
        if not isinstance(props, dict):
            props = {}

        # 依次尝试常见字段（property 内优先，其次附件顶层）
        for value in (
            props.get("url"),
            self.attachment.get("url"),
            props.get("link"),
            self.attachment.get("link"),
            props.get("href"),
            self.attachment.get("href"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()

        # 兜底：property 的 name / title 里若为以 http 开头的字符串，也当作链接
        for value in (props.get("name"), props.get("title"),
                      self.attachment.get("name"), self.attachment.get("title")):
            if isinstance(value, str) and value.strip().startswith("http"):
                return value.strip()

        return ""

    def _build_report_url(self) -> str:
        """构建任务点完成上报 URL（与 Read/Book/Document 同构，type=url）。"""
        knowledgeid = self.defaults.get("knowledgeid", "")
        clazzid = self.classId or self.defaults.get("clazzId", "")
        jtoken = self.attachment.get("jtoken", "")
        return (
            "https://mooc1-2.chaoxing.com/ananas/job/url"
            f"?jobid={self.jobid}"
            f"&knowledgeid={knowledgeid}"
            f"&courseid={self.courseId}"
            f"&clazzid={clazzid}"
            f"&jtoken={jtoken}"
            f"&_dc={int(time.time() * 1000)}"
        )

    def _report_progress(self) -> bool:
        """调用 ananas/job/url 上报任务点完成。"""
        _headers = {
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Host': 'mooc1-2.chaoxing.com',
            'Referer': 'https://mooc1-2.chaoxing.com/ananas/modules/innerlink/index.html',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
        }
        _headers.update(self.headers)

        try:
            _rsp = doGet(url=self._build_report_url(), headers=_headers)
            if not _rsp:
                loguru.logger.warning("超链接上报返回为空")
                return False
            data = json.loads(_rsp)
            status = bool(data.get("status"))
            if status:
                loguru.logger.info(f"超链接任务点上报成功: jobid={self.jobid}")
            else:
                loguru.logger.warning(f"超链接任务点上报失败: {_rsp[:200]}")
            return status
        except Exception as e:
            loguru.logger.warning(f"超链接任务点上报异常: {e}")
            return False

    def do_finish(self) -> bool:
        _url = self._extract_url()
        if not _url:
            loguru.logger.warning(f"超链接任务点未找到链接地址，跳过: {self.attachment}")
            return False

        # 1. 打开链接（best-effort）
        try:
            doGet(url=_url, headers=self.headers)
            loguru.logger.info(f"已打开超链接: {_url}")
        except Exception as e:
            loguru.logger.warning(f"打开超链接失败: {_url} ({e})")

        # 2. 上报任务点完成
        return self._report_progress()
