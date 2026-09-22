# _*_ coding:utf-8 _*_
# 通知系统，adapted from reference implementation
from abc import ABC, abstractmethod
from typing import Dict, Optional

import loguru
import requests


class NotificationService(ABC):
    def __init__(self):
        self.name = self.__class__.__name__
        self.url = ""
        self._conf = None
        self.disabled = False

    def config_set(self, config: Dict[str, str]):
        self._conf = config

    @abstractmethod
    def _init_service(self) -> None:
        pass

    @abstractmethod
    def _send(self, message: str) -> None:
        pass

    def send(self, message: str) -> bool:
        if self.disabled:
            return False
        try:
            self._send(message)
            loguru.logger.info(f"通知发送成功: {self.name}")
            return True
        except Exception as e:
            loguru.logger.error(f"通知发送失败: {self.name} - {e}")
            return False

    def init_notification(self) -> bool:
        if not self._conf:
            self.disabled = True
            return False

        try:
            self._init_service()
            return True
        except Exception as e:
            loguru.logger.error(f"通知服务初始化失败: {self.name} - {e}")
            self.disabled = True
            return False


class ServerChan(NotificationService):
    def _init_service(self) -> None:
        self.sendkey = self._conf.get("sendkey", "")
        if not self.sendkey:
            self.disabled = True
            loguru.logger.warning("ServerChan 未配置 sendkey")
            return
        self.url = f"https://sctapi.ftqq.com/{self.sendkey}.send"

    def _send(self, message: str) -> None:
        if not self.sendkey:
            return

        data = {
            "title": "学习通助手",
            "desp": message
        }
        resp = requests.post(self.url, data=data, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")


class Qmsg(NotificationService):
    def _init_service(self) -> None:
        self.key = self._conf.get("key", "")
        if not self.key:
            self.disabled = True
            loguru.logger.warning("Qmsg 未配置 key")
            return
        self.url = f"https://qmsg.zendee.cn/send/{self.key}"

    def _send(self, message: str) -> None:
        if not self.key:
            return

        data = {"msg": message}
        resp = requests.post(self.url, data=data, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")


class Bark(NotificationService):
    def _init_service(self) -> None:
        self.key = self._conf.get("key", "")
        if not self.key:
            self.disabled = True
            loguru.logger.warning("Bark 未配置 key")
            return
        self.url = f"https://api.day.app/{self.key}"

    def _send(self, message: str) -> None:
        if not self.key:
            return

        data = {
            "title": "学习通助手",
            "body": message
        }
        resp = requests.post(self.url, json=data, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")


class Telegram(NotificationService):
    def _init_service(self) -> None:
        self.bot_token = self._conf.get("bot_token", "")
        self.chat_id = self._conf.get("chat_id", "")
        if not self.bot_token or not self.chat_id:
            self.disabled = True
            loguru.logger.warning("Telegram 未配置 bot_token 或 chat_id")
            return
        self.url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def _send(self, message: str) -> None:
        if not self.bot_token or not self.chat_id:
            return

        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        resp = requests.post(self.url, data=data, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")


class PushPlus(NotificationService):
    def _init_service(self) -> None:
        self.token = self._conf.get("token", "")
        if not self.token:
            self.disabled = True
            loguru.logger.warning("PushPlus 未配置 token")
            return
        self.url = "http://www.pushplus.plus/send"

    def _send(self, message: str) -> None:
        if not self.token:
            return

        data = {
            "token": self.token,
            "title": "学习通助手",
            "content": message
        }
        resp = requests.post(self.url, json=data, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")


class NotificationFactory:
    SERVICE_CLASSES = {
        "ServerChan": ServerChan,
        "Qmsg": Qmsg,
        "Bark": Bark,
        "Telegram": Telegram,
        "PushPlus": PushPlus,
    }

    @staticmethod
    def create_service(service_name: str, config: Dict[str, str] = None) -> Optional[NotificationService]:
        service_class = NotificationFactory.SERVICE_CLASSES.get(service_name)
        if service_class:
            service = service_class()
            if config:
                service.config_set(config)
            return service
        loguru.logger.warning(f"未知的通知服务: {service_name}")
        return None


class Notification:
    def __init__(self):
        self._services: list[NotificationService] = []
        self._conf = None
        self.disabled = True

    def config_set(self, config: Dict[str, str]):
        self._conf = config or {}

    def get_notification_from_config(self) -> 'Notification':
        if not self._conf:
            return self

        provider = self._conf.get("provider", "")
        if not provider:
            return self

        provider_list = [p.strip() for p in provider.split(",") if p.strip()]

        for provider_name in provider_list:
            service = NotificationFactory.create_service(provider_name, self._conf)
            if service:
                self._services.append(service)
                loguru.logger.info(f"已加载通知服务: {provider_name}")

        if self._services:
            self.disabled = False

        return self

    def init_notification(self) -> bool:
        if self.disabled:
            return False

        all_success = True
        for service in self._services:
            if not service.init_notification():
                all_success = False

        return all_success

    def send(self, message: str) -> int:
        if self.disabled or not self._services:
            return 0

        success_count = 0
        for service in self._services:
            if service.send(message):
                success_count += 1

        return success_count
