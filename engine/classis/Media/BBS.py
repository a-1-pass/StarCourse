# _*_ coding:utf-8 _*_
import json
import random
import time

import loguru

from classis.Media import Media
from utils import doGet, doPost


REPLY_TEMPLATES = [
    "老师讲得很清楚，受益匪浅！",
    "这个话题很有意思，学习了。",
    "感谢分享，内容很有价值。",
    "课程内容丰富，收获满满。",
    "认真学习了，讲得很透彻。",
    "知识点讲解清晰，容易理解。",
    "学习了，感谢老师分享！",
    "讨论很有深度，学到很多。",
    "这部分内容很重要，已学习。",
    "受益匪浅，期待更多内容。",
]


class BBS(Media):
    """讨论/话题任务点处理类"""

    def __init__(self, attachment: dict, headers, defaults: dict, course_id: str = "", class_id: str = "",
                 ai_client=None, strategy: str = "random"):
        super().__init__(attachment, headers)
        self.defaults = defaults
        self.course_id = course_id
        self.class_id = class_id
        self.ai_client = ai_client
        self.strategy = strategy
        property_dict = attachment.get("property") or {}
        self.name = property_dict.get("name") or property_dict.get("title") or "讨论任务"
        self.topic_id = ""
        self.bbs_id = ""
        self._parse_topic_ids()

    def _parse_topic_ids(self):
        """从 attachment 中提取话题ID"""
        property_dict = self.attachment.get("property") or {}
        
        for key in ["topicId", "topic_id", "topicid", "id"]:
            val = property_dict.get(key) or self.attachment.get(key)
            if val and len(str(val)) > 10:
                self.topic_id = str(val)
                break
        
        for key in ["bbsId", "bbs_id", "bbsid", "groupId", "group_id"]:
            val = property_dict.get(key) or self.attachment.get(key)
            if val and len(str(val)) > 10:
                self.bbs_id = str(val)
                break
        
        if not self.topic_id:
            jobid = self.attachment.get("jobid", "")
            if jobid and len(jobid) > 10:
                self.topic_id = str(jobid)

    def _generate_reply(self) -> str:
        """生成回复内容"""
        if self.strategy == "ai" and self.ai_client and self.ai_client.is_configured():
            try:
                if hasattr(self.ai_client, 'chat'):
                    prompt = f"请用中文写一段30-50字的课程讨论回复，话题是：{self.name}。要求语气自然，像学生的真实发言，不要太正式，不要带标题。"
                    reply = self.ai_client.chat(prompt)
                    if reply and len(reply) > 5:
                        reply = reply.strip().strip('"').strip("'")
                        if len(reply) > 200:
                            reply = reply[:200]
                        loguru.logger.info(f"[BBS] AI生成回复: {reply[:50]}...")
                        return reply
            except Exception as e:
                loguru.logger.debug(f"[BBS] AI生成回复失败: {e}")
        
        return random.choice(REPLY_TEMPLATES)

    def do_finish(self) -> bool:
        """完成讨论任务点：发帖回复"""
        clazz_id = self.class_id or self.defaults.get("clazzId", "")
        course_id = self.course_id or self.defaults.get("courseid", "")
        cpi = self.defaults.get("cpi", "")
        
        loguru.logger.debug(f"[BBS] 讨论任务: name={self.name}, topic_id={self.topic_id}, bbs_id={self.bbs_id}")
        
        if not self.topic_id:
            loguru.logger.warning(f"[BBS] 无法获取话题ID，跳过任务: {self.name}")
            return False
        
        reply_content = self._generate_reply()
        
        _headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'Origin': 'https://groupweb.chaoxing.com',
            'Referer': f'https://groupweb.chaoxing.com/course/topic/v3/bbs/{self.topic_id}/{self.bbs_id or ""}/replysList?courseId={course_id}&classId={clazz_id}',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest',
        }
        _headers.update(self.headers)
        
        posted = False
        tried_urls = []
        
        # 尝试多种API路径
        api_urls = self._build_api_urls(course_id, clazz_id, cpi)
        
        for api_url, post_data in api_urls:
            tried_urls.append(api_url)
            try:
                loguru.logger.debug(f"[BBS] 尝试发帖: {api_url[:80]}...")
                _rsp = doPost(url=api_url, data=post_data, headers=_headers)
                if self._check_post_success(_rsp):
                    loguru.logger.info(f"[BBS] 讨论回复发布成功: {self.name}")
                    posted = True
                    break
                else:
                    loguru.logger.debug(f"[BBS] 回复发布返回: {_rsp[:100] if _rsp else '空'}")
            except Exception as e:
                loguru.logger.debug(f"[BBS] 发帖异常: {e}")
                continue
        
        if not posted:
            # 兜底：尝试用阅读接口标记完成
            loguru.logger.info(f"[BBS] 直接发布失败，尝试标记任务完成...")
            posted = self._mark_job_done(clazz_id, course_id, cpi)
        
        time.sleep(random.uniform(1, 2))
        return posted

    def _build_api_urls(self, course_id: str, class_id: str, cpi: str) -> list:
        """构建多种可能的发帖API URL"""
        urls = []
        reply_content = "回复内容"
        
        base_domains = [
            "https://groupweb.chaoxing.com",
            "https://mooc1-1.chaoxing.com",
            "https://mooc1-2.chaoxing.com",
            "https://mooc2-ans.chaoxing.com",
        ]
        
        for domain in base_domains:
            # groupweb v3 API
            if self.bbs_id:
                url = f"{domain}/course/topic/v3/bbs/{self.topic_id}/{self.bbs_id}/addReply"
                data = {
                    "courseId": course_id,
                    "classId": class_id,
                    "content": reply_content,
                    "anonymous": "0",
                }
                urls.append((url, data))
            
            # 旧版 API 格式
            url = f"{domain}/mooc-ans/work/addTopicReply"
            data = {
                "courseId": course_id,
                "classId": class_id,
                "topicId": self.topic_id,
                "content": reply_content,
                "cpi": cpi,
            }
            urls.append((url, data))
            
            # bbs/add 格式
            url = f"{domain}/mooc/bbs/addReply"
            data = {
                "courseId": course_id,
                "clazzId": class_id,
                "topicId": self.topic_id,
                "content": reply_content,
            }
            urls.append((url, data))
        
        return urls

    def _check_post_success(self, response: str) -> bool:
        """检查回复发布是否成功"""
        if not response:
            return False
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                if data.get("success") or data.get("status") or data.get("result"):
                    return True
                if data.get("code") in (1, 200, "1", "200", "success"):
                    return True
                if data.get("msg") and "成功" in str(data.get("msg")):
                    return True
        except:
            pass
        if "成功" in response or "success" in response.lower():
            return True
        return False

    def _mark_job_done(self, clazz_id: str, course_id: str, cpi: str) -> bool:
        """兜底：尝试标记任务点完成"""
        if not self.jobid:
            return False
        
        _headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive',
            'X-Requested-With': 'XMLHttpRequest',
        }
        _headers.update(self.headers)
        
        urls = [
            f"https://mooc1-1.chaoxing.com/ananas/job/bbs?jobid={self.jobid}&knowledgeid={self.defaults.get('knowledgeid', '')}&courseid={course_id}&clazzid={clazz_id}&jtoken={self.attachment.get('jtoken', '')}&_dc={int(time.time() * 1000)}",
            f"https://mooc1-2.chaoxing.com/ananas/job/bbs?jobid={self.jobid}&knowledgeid={self.defaults.get('knowledgeid', '')}&courseid={course_id}&clazzid={clazz_id}&jtoken={self.attachment.get('jtoken', '')}&_dc={int(time.time() * 1000)}",
        ]
        
        for url in urls:
            try:
                _rsp = doGet(url=url, headers=_headers)
                loguru.logger.debug(f"[BBS] 标记完成返回: {_rsp[:80] if _rsp else '空'}")
                if _rsp and "成功" in _rsp:
                    return True
                if _rsp:
                    try:
                        data = json.loads(_rsp)
                        if data.get("status") or data.get("success"):
                            return True
                    except:
                        pass
            except Exception as e:
                loguru.logger.debug(f"[BBS] 标记完成异常: {e}")
                continue
        
        return False
