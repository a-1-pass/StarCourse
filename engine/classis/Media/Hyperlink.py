# _*_ coding:utf-8 _*_
import loguru

from classis.Media import Media
from utils import doGet


class Hyperlink(Media):
    """超链接任务点。

    超链接任务点没有独立的完成上报接口（当前平台接口未知），
    因此本类只做「打开链接 + 预留上报扩展点」的 best-effort 处理。
    """

    def __init__(self, attachment: dict, headers):
        super().__init__(attachment, headers)

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

    def do_finish(self) -> bool:
        _url = self._extract_url()
        if not _url:
            loguru.logger.warning(f"超链接任务点未找到链接地址，跳过: {self.attachment}")
            return False

        try:
            doGet(url=_url, headers=self.headers)
            loguru.logger.info(f"已打开超链接: {_url}")
        except Exception as e:
            # best-effort：打开失败不影响后续任务点，仅告警
            loguru.logger.warning(f"打开超链接失败: {_url} ({e})")

        return self._report_progress()

    def _report_progress(self) -> bool:
        # TODO: 实测补超链接任务点完成上报接口（当前平台接口未知）
        loguru.logger.info("超链接任务点已打开，完成上报接口暂未实现（预留扩展点）")
        return True
