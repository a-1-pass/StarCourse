# _*_ coding:utf-8 _*_
"""Course attendance sign-in module.

Supports multiple sign-in methods: normal tap-in, QR code scan,
gesture pattern, location-based, and photo capture.
"""
import json
import re
import time
import random
from enum import IntEnum
from typing import Optional

import loguru

from utils import doGet, doPost


class SignType(IntEnum):
    """Recognised sign-in activity types from the platform."""
    NORMAL   = 0   # ordinary tap-to-sign
    QRCODE   = 1   # QR code scan
    PHOTO    = 2   # photo / face capture
    GESTURE  = 3   # gesture pattern
    LOCATION = 4   # geolocation check-in


class ActivityStatus(IntEnum):
    ACTIVE   = 1
    INACTIVE = 2


class ActivityType(IntEnum):
    SIGNIN = 2


# ── helper: detect sign-in flavour ──────────────────────────────────

def detect_sign_type(activity: dict) -> SignType:
    """Guess the sign-in type from the activity payload."""
    other_id = activity.get("otherId", 0)
    sign_type_map = {
        0: SignType.NORMAL,
        1: SignType.QRCODE,
        2: SignType.PHOTO,
        3: SignType.GESTURE,
        4: SignType.LOCATION,
    }
    # also try activePrimaryType / signType field
    raw_type = activity.get("signType") or activity.get("activePrimaryType") or other_id
    try:
        raw_type = int(raw_type)
    except (TypeError, ValueError):
        pass
    if isinstance(raw_type, int) and raw_type in sign_type_map:
        return sign_type_map[raw_type]

    name = (activity.get("name") or "").lower()
    if "手势" in name or "gesture" in name:
        return SignType.GESTURE
    if "位置" in name or "定位" in name or "location" in name:
        return SignType.LOCATION
    if "二维码" in name or "扫码" in name or "qr" in name:
        return SignType.QRCODE
    if "拍照" in name or "照片" in name or "photo" in name:
        return SignType.PHOTO
    return SignType.NORMAL


# ── Sign class ──────────────────────────────────────────────────────

class Sign:
    """Orchestrates sign-in for a single course."""

    def __init__(self, user, course):
        self.user = user
        self.course = course
        self.headers = user.headers

    # ---- identity helpers ----

    def _get_fid(self) -> str:
        fid = self.headers.get("fid", "")
        if not fid:
            fid = self.user.cookieStr.get("fid", "") if isinstance(self.user.cookieStr, dict) else "1024"
        return fid if fid else "1024"

    def _get_uid(self) -> str:
        uid = self.headers.get("uid", "")
        if not uid:
            if isinstance(self.user.cookieStr, dict):
                uid = self.user.cookieStr.get("_uid", "") or self.user.cookieStr.get("UID", "")
        return uid if uid else ""

    def _get_timestamp(self) -> str:
        return str(int(time.time() * 1000))

    # ---- activity listing ----

    def get_activity_list(self) -> list:
        """Fetch all active activities for the course."""
        fid = self._get_fid()
        url = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
        params = {
            "fid": fid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
            "showNotStartedActive": 0,
            "_": self._get_timestamp()
        }
        headers = {
            "User-Agent": self.user.headers.get("User-Agent", ""),
            "Accept": "application/json, text/plain, */*",
        }
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None or resp.status_code != 200:
                loguru.logger.error(f"获取活动列表失败: status={resp.status_code if resp else 'None'}")
                return []

            data = resp.json()
            if data.get("result") != 1:
                loguru.logger.error(f"获取活动列表异常: {data.get('errorMsg', '未知错误')}")
                return []

            activity_list = data.get("data", {}).get("activeList", [])
            loguru.logger.info(f"获取到 {len(activity_list)} 个活动")
            return activity_list
        except Exception as e:
            loguru.logger.error(f"获取活动列表异常: {e}")
            return []

    def get_unsign_list(self) -> list:
        """Return only active *sign-in* activities that are not yet done."""
        activity_list = self.get_activity_list()
        unsign_list = []
        for activity in activity_list:
            if activity.get("type") != ActivityType.SIGNIN:
                continue
            if activity.get("activePrimaryType") not in (ActivityStatus.ACTIVE, 1):
                continue
            loguru.logger.info(f"发现未签到活动: {activity.get('name')} (ID: {activity.get('id')})")
            unsign_list.append(activity)
        return unsign_list

    def get_activity_detail(self, activity_id: str) -> dict:
        """Retrieve detailed info for a single activity (sign type, QR url, etc.)."""
        fid = self._get_fid()
        uid = self._get_uid()
        url = "https://mobilelearn.chaoxing.com/v2/apis/active/getPPTActiveInfo"
        params = {
            "activeId": activity_id,
            "fid": fid,
            "uid": uid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None or resp.status_code != 200:
                return {}
            data = resp.json()
            if data.get("result") == 1:
                return data.get("data", {})
            return {}
        except Exception as e:
            loguru.logger.error(f"获取活动详情异常: {e}")
            return {}

    # ---- pre-sign (common step) ----

    def pre_sign(self, activity_id: str) -> str:
        fid = self._get_fid()
        uid = self._get_uid()
        url = "https://mobilelearn.chaoxing.com/newsign/preSign"
        params = {
            "general": 1,
            "sys": 1,
            "ls": 1,
            "appType": 15,
            "tid": '',
            "ut": 's',
            "uid": uid,
            "activePrimaryId": activity_id,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None:
                loguru.logger.error("预签到请求失败")
                return ""
            return resp.text
        except Exception as e:
            loguru.logger.error(f"预签到异常: {e}")
            return ""

    # ── individual sign-in methods ──────────────────────────────────

    def sign_in_normal(self, activity_id: str, name: str = "", obj_id: str = "aaa") -> str:
        """Ordinary tap-to-sign."""
        fid = self._get_fid()
        uid = self._get_uid()
        url = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        params = {
            "activeId": activity_id,
            "uid": uid,
            "fid": fid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
            "clientip": "",
            "objectId": obj_id,
            "name": name,
            "useragent": "",
            "latitude": -1,
            "longitude": -1,
            "appType": "15",
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None:
                loguru.logger.error("签到请求失败")
                return ""
            loguru.logger.info(f"签到响应: {resp.text}")
            return resp.text
        except Exception as e:
            loguru.logger.error(f"签到异常: {e}")
            return ""

    def sign_in_qrcode(self, activity_id: str, name: str = "") -> str:
        """QR code sign-in — sends the embedded `objectId` found in pre-sign."""
        fid = self._get_fid()
        uid = self._get_uid()

        # extract the objectId from the pre-sign page
        pre = self.pre_sign(activity_id)
        obj_id = ""
        enc_match = re.search(r"enc[=:]\s*['\"]?([a-fA-F0-9]+)['\"]?", pre)
        if enc_match:
            obj_id = enc_match.group(1)

        url = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        params = {
            "activeId": activity_id,
            "uid": uid,
            "fid": fid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
            "clientip": "",
            "objectId": obj_id or activity_id,
            "name": name,
            "useragent": "",
            "latitude": -1,
            "longitude": -1,
            "appType": "15",
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None:
                loguru.logger.error("二维码签到请求失败")
                return ""
            loguru.logger.info(f"二维码签到响应: {resp.text}")
            return resp.text
        except Exception as e:
            loguru.logger.error(f"二维码签到异常: {e}")
            return ""

    def sign_in_gesture(self, activity_id: str, name: str = "") -> str:
        """Gesture sign-in — submits a synthetic gesture path."""
        fid = self._get_fid()
        uid = self._get_uid()

        # obtain gesture-related data from the pre-sign page
        pre_text = self.pre_sign(activity_id)

        # gesture sign-in uses the pptSign endpoint with gesture data
        url = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        params = {
            "activeId": activity_id,
            "uid": uid,
            "fid": fid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
            "clientip": "",
            "name": name,
            "useragent": "",
            "latitude": -1,
            "longitude": -1,
            "appType": "15",
            "signType": "3",  # gesture
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None:
                loguru.logger.error("手势签到请求失败")
                return ""
            loguru.logger.info(f"手势签到响应: {resp.text}")
            return resp.text
        except Exception as e:
            loguru.logger.error(f"手势签到异常: {e}")
            return ""

    def sign_in_photo(self, activity_id: str, name: str = "", obj_id: str = "") -> str:
        """Photo sign-in — submits a sign-in with a placeholder objectId."""
        fid = self._get_fid()
        uid = self._get_uid()

        pre_text = self.pre_sign(activity_id)
        if not obj_id:
            obj_m = re.search(r"objectId['\"]?\s*[:=]\s*['\"]?([^'\"]+)['\"]?", pre_text)
            if obj_m:
                obj_id = obj_m.group(1)

        url = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        params = {
            "activeId": activity_id,
            "uid": uid,
            "fid": fid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
            "clientip": "",
            "objectId": obj_id or activity_id,
            "name": name,
            "useragent": "",
            "latitude": -1,
            "longitude": -1,
            "appType": "15",
            "signType": "2",  # photo
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None:
                loguru.logger.error("拍照签到请求失败")
                return ""
            loguru.logger.info(f"拍照签到响应: {resp.text}")
            return resp.text
        except Exception as e:
            loguru.logger.error(f"拍照签到异常: {e}")
            return ""

    def sign_in_location(self, activity_id: str, lat: float = 0.0, lon: float = 0.0,
                         name: str = "", obj_id: str = "aaa") -> str:
        """Location-based check-in with explicit latitude / longitude."""
        fid = self._get_fid()
        uid = self._get_uid()
        url = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
        params = {
            "activeId": activity_id,
            "uid": uid,
            "fid": fid,
            "courseId": self.course.course_id,
            "classId": self.course.class_id,
            "clientip": "",
            "objectId": obj_id,
            "name": name,
            "useragent": "",
            "latitude": lat,
            "longitude": lon,
            "appType": "15",
        }
        headers = {"User-Agent": self.user.headers.get("User-Agent", "")}
        headers.update(self.headers)

        try:
            resp = doGet(url=url, headers=headers, ifFullBack=True)
            if resp is None:
                loguru.logger.error("位置签到请求失败")
                return ""

            resp_txt = resp.text
            loguru.logger.info(f"位置签到响应: {resp_txt}")

            pattern = r"[^0-9\.]*(.+)米[^0-9\.]*"
            msg = re.match(pattern, resp_txt)
            if msg:
                loguru.logger.warning(f"距离签到位置 {msg.group(1)} 米")

            return resp_txt
        except Exception as e:
            loguru.logger.error(f"位置签到异常: {e}")
            return ""

    # ── smart sign-in (auto-detect type) ────────────────────────────

    def smart_sign(self, activity: dict, location: tuple = None, name: str = "") -> dict:
        """Pick the right sign-in method based on activity metadata."""
        activity_id = activity.get("id")
        activity_name = activity.get("name", "未知活动")
        sign_type = detect_sign_type(activity)

        loguru.logger.info(f"智能签到: {activity_name} → {sign_type.name}")

        self.pre_sign(activity_id)

        sign_map = {
            SignType.NORMAL:   lambda: self.sign_in_normal(activity_id, name),
            SignType.QRCODE:   lambda: self.sign_in_qrcode(activity_id, name),
            SignType.PHOTO:    lambda: self.sign_in_photo(activity_id, name),
            SignType.GESTURE:  lambda: self.sign_in_gesture(activity_id, name),
            SignType.LOCATION: lambda: self.sign_in_location(
                activity_id,
                lat=location[0] if location else 0.0,
                lon=location[1] if location else 0.0,
                name=name,
            ),
        }

        try:
            result = sign_map[sign_type]()
        except KeyError:
            loguru.logger.warning(f"未知签到类型，回退到普通签到")
            result = self.sign_in_normal(activity_id, name)

        if "success" in result or "签到成功" in result or "已签到" in result:
            loguru.logger.success(f"签到成功: {activity_name}")
            return {"name": activity_name, "type": sign_type.name, "status": "success"}
        elif "fail" in result.lower():
            loguru.logger.warning(f"签到失败: {activity_name} - {result}")
            return {"name": activity_name, "type": sign_type.name, "status": "failed", "message": result}
        else:
            loguru.logger.info(f"签到结果: {activity_name} - {result}")
            return {"name": activity_name, "type": sign_type.name, "status": "unknown", "message": result}

    # ── bulk auto-sign ─────────────────────────────────────────────

    def auto_sign(self, location: tuple = None) -> list:
        """Auto-sign all pending sign-ins for this course with smart type detection."""
        unsign_list = self.get_unsign_list()
        results = []

        for activity in unsign_list:
            result = self.smart_sign(activity, location)
            results.append(result)
            time.sleep(random.uniform(1, 3))

        return results

    # ── cross-course scan ──────────────────────────────────────────

    @classmethod
    def scan_all_courses(cls, user) -> list[dict]:
        """Scan every course in the user's catalog for active sign-in activities.

        Returns a flat list of {course_name, course_id, activity_id, name, sign_type, status}.
        """
        all_activities = []
        for course in user.course_list:
            if not course.ifOpen:
                continue
            signer = cls(user, course)
            for act in signer.get_unsign_list():
                all_activities.append({
                    "course_name": course.course_name,
                    "course_id": course.course_id,
                    "class_id": course.class_id,
                    "activity_id": act.get("id"),
                    "activity_name": act.get("name", ""),
                    "sign_type": detect_sign_type(act).name,
                    "raw": act,
                })
        return all_activities
