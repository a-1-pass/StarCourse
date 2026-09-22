# -*- coding: utf-8 -*-
"""Chapter task-card decoder — extracts attachments and job info from chapter pages."""
import json
import re
from typing import Any, Dict, List, Tuple

import loguru

from utils import doGet

CARDS_BASE_URL = "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
CARD_API_VERSION = "2025-0424-1038-3"
CARD_NUMS = "0123456"


def _parse_card_html(html_text: str) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
    if not html_text:
        return None, {}

    if "章节未开放" in html_text:
        return None, {"notOpen": True}

    compact = html_text.replace(" ", "")
    match = re.findall(r"mArg=\{(.*?)\};", compact)
    if match:
        try:
            return json.loads("{" + match[0] + "}"), {}
        except json.JSONDecodeError:
            pass

    legacy = re.findall(r"mArg = ({[\s\S]*)}catch", html_text)
    if legacy:
        try:
            return json.loads(legacy[0].strip()[:-1]), {}
        except json.JSONDecodeError:
            pass

    return None, {}


def sanitize_attachment(card: dict) -> dict | None:
    if not card or card.get("isPassed"):
        return None
    if card.get("otherInfo"):
        card["otherInfo"] = card["otherInfo"].split("&")[0]
    return card


def is_live_attachment(media: dict) -> bool:
    card_type = str(media.get("type", "")).lower()
    property_data = media.get("property") or {}
    prop_type = str(property_data.get("type", "")).lower()
    resource_type = str(property_data.get("resourceType", "")).lower()
    return (
        "live" in card_type
        or "live" in prop_type
        or "live" in resource_type
        or "livestream" in card_type
        or property_data.get("liveId") is not None
        or property_data.get("streamName") is not None
        or property_data.get("vdoid") is not None
    )


def is_read_attachment(media: dict) -> bool:
    if media.get("job") is not None:
        return False
    property_data = media.get("property") or {}
    return media.get("type") == "read" and not property_data.get("read", False)


def fetch_chapter_attachments(user, class_id: str, course_id: str, cpi: str, knowledge_id: str
                              ) -> Tuple[List[dict], dict]:
    """
    Fetch all task cards for a chapter.
    Returns ([{attachments, defaults}, ...], job_info).
    """
    attach_pages: List[dict] = []
    job_info: Dict[str, Any] = {}

    for num in CARD_NUMS:
        url = (
            f"{CARDS_BASE_URL}?clazzid={class_id}&courseid={course_id}"
            f"&knowledgeid={knowledge_id}&num={num}&ut=s&cpi={cpi}"
            f"&v={CARD_API_VERSION}&mooc2=1"
        )
        rsp = doGet(url=url, headers=user.headers)
        if not rsp:
            continue

        data, info = _parse_card_html(rsp)
        if info.get("notOpen"):
            loguru.logger.info("该章节未开放")
            return [], info
        if not data:
            continue

        defaults = data.get("defaults") or {}
        job_info.update(defaults)

        attachments = []
        for raw in data.get("attachments") or []:
            card = sanitize_attachment(dict(raw))
            if card:
                attachments.append(card)

        if attachments:
            attach_pages.append({"attachments": attachments, "defaults": defaults})

    return attach_pages, job_info


def study_empty_page(user, class_id: str, course_id: str, cpi: str, chapter_id: str) -> bool:
    """Mark an empty chapter as completed."""
    url = (
        "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax?"
        f"courseId={course_id}&clazzid={class_id}&chapterId={chapter_id}&cpi={cpi}"
        f"&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0"
    )
    rsp = doGet(url=url, headers=user.headers, ifFullBack=True)
    return rsp is not None and getattr(rsp, "status_code", 0) == 200
