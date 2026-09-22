# _*_ coding:utf-8 _*_
# Cookie 管理模块，adapted from reference implementation
import json
import os
from typing import Dict, Optional

import loguru


class CookieManager:
    def __init__(self, cookie_file: str = None):
        self.cookie_file = cookie_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
        self._cookies: Dict[str, str] = {}

    def save_cookies(self, cookies: Dict[str, str]) -> bool:
        try:
            buffer = ""
            for k, v in cookies.items():
                buffer += f"{k}={v};"
            buffer = buffer.rstrip(";")

            with open(self.cookie_file, "w", encoding="utf-8") as f:
                f.write(buffer)

            loguru.logger.info(f"Cookie 已保存到: {self.cookie_file}")
            return True
        except Exception as e:
            loguru.logger.error(f"保存 Cookie 失败: {e}")
            return False

    def save_cookies_from_session(self, session) -> bool:
        try:
            cookies_dict = {}
            for k, v in session.cookies.items():
                cookies_dict[k] = v
            return self.save_cookies(cookies_dict)
        except Exception as e:
            loguru.logger.error(f"从 Session 保存 Cookie 失败: {e}")
            return False

    def load_cookies(self) -> Dict[str, str]:
        if not os.path.exists(self.cookie_file):
            loguru.logger.debug(f"Cookie 文件不存在: {self.cookie_file}")
            return {}

        try:
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                buffer = f.read().strip()

            cookies = {}
            if buffer:
                for item in buffer.split(";"):
                    item = item.strip()
                    if item and "=" in item:
                        k, v = item.split("=", 1)
                        cookies[k.strip()] = v.strip()

            self._cookies = cookies
            loguru.logger.debug(f"已加载 {len(cookies)} 个 Cookie")
            return cookies
        except Exception as e:
            loguru.logger.error(f"加载 Cookie 失败: {e}")
            return {}

    def load_cookies_from_string(self, cookie_str: str) -> Dict[str, str]:
        cookies = {}
        if cookie_str:
            for item in cookie_str.split(";"):
                item = item.strip()
                if item and "=" in item:
                    k, v = item.split("=", 1)
                    cookies[k.strip()] = v.strip()
        self._cookies = cookies
        return cookies

    def get_cookie_string(self) -> str:
        if not self._cookies:
            self.load_cookies()
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    def get_cookie(self, key: str) -> Optional[str]:
        if not self._cookies:
            self.load_cookies()
        return self._cookies.get(key)

    def set_cookie(self, key: str, value: str):
        if not self._cookies:
            self.load_cookies()
        self._cookies[key] = value

    def clear_cookies(self) -> bool:
        try:
            if os.path.exists(self.cookie_file):
                os.remove(self.cookie_file)
            self._cookies = {}
            loguru.logger.info("Cookie 已清除")
            return True
        except Exception as e:
            loguru.logger.error(f"清除 Cookie 失败: {e}")
            return False

    def has_cookies(self) -> bool:
        if not self._cookies:
            self.load_cookies()
        return bool(self._cookies)

    def get_uid(self) -> Optional[str]:
        return self.get_cookie("_uid") or self.get_cookie("UID")

    def get_fid(self) -> str:
        fid = self.get_cookie("fid")
        return fid if fid else "1024"


def save_cookies(cookies_dict: Dict[str, str], cookie_file: str = None) -> bool:
    manager = CookieManager(cookie_file)
    return manager.save_cookies(cookies_dict)


def use_cookies(cookie_file: str = None) -> Dict[str, str]:
    manager = CookieManager(cookie_file)
    return manager.load_cookies()
