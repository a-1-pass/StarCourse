# -*- coding: utf-8 -*-
"""User authentication and course-data model."""
import json
import re
import requests
import sys

from ..SelfException import LoginException, RequestException
from ..Course import Course
from lxml import etree
from loguru import logger

from utils import doGet, doPost, encrypt_des, xpath_first, direct_url

# 动态 User-Agent（按平台匹配真实浏览器 UA）；独立运行时降级到固定值
try:
    from src.network_utils import get_random_user_agent
    _UA = get_random_user_agent()
except Exception:
    _UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

from config import GloConfig


class User:
    def __init__(self, username: str = "", password: str = "", cookieStr: str = ""):
        self.course_list = []
        self.headers = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Host': 'passport2.chaoxing.com',
            'Origin': 'https://passport2.chaoxing.com',
            'Referer': 'https://passport2.chaoxing.com/login?loginType=4&fid=314&newversion=true&refer=http://i.mooc.chaoxing.com',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'User-Agent': GloConfig.data.get("GloConfig").get("headers").get("User-Agent"),
            'X-Requested-With': 'XMLHttpRequest'
        }
        if cookieStr == "":
            self.username = username
            rsp = doPost("https://passport2.chaoxing.com/fanyalogin",
                         headers=self.headers,
                         data="fid=314&uname={0}&password={1}&refer=http%253A%252F%252Fi.mooc.chaoxing.com&t=true"
                         .format(username, encrypt_des(password, "u2oh6Vu^").decode('utf-8')),
                         ifFullBack=True)
            if rsp.status_code == 200:
                rsp_json = json.loads(rsp.text)
                if rsp_json.get("status"):
                    self.name = rsp_json.get("name")
                    self.uid = rsp.cookies.get('_uid')
                    for item in rsp.cookies:
                        cookieStr = cookieStr + item.name + '=' + item.value + ';'
                    self.cookieStr = cookieStr
                    self.headers = {
                        'User-Agent': _UA,
                        "Cookie": cookieStr
                    }
                    if not self.name:
                        self._get_user_info()
                else:
                    raise LoginException(rsp_json.get("msg2"))
            else:
                raise RequestException(rsp, 1)
        else:
            self.cookieStr = cookieStr
            self.headers = {
                'User-Agent': _UA,
                "Cookie": cookieStr
            }
            if self.__checkLogin():
                self.uid = re.findall(r"_uid=(\d+);", self.cookieStr)[0] if re.findall(r"_uid=(\d+);", self.cookieStr) else ""
                self.username = self.uid
                self._get_user_info()
            else:
                raise LoginException("Cookie失效，请重新获取")

    def _get_user_info(self):
        try:
            _url = "https://i.chaoxing.com/base/settings"
            _headers = {
                'Referer': 'https://i.chaoxing.com/base',
            }
            _headers.update(self.headers)
            _rsp = doGet(url=_url, headers=_headers, ifFullBack=True)
            if _rsp and getattr(_rsp, "status_code", 0) == 200:
                html = _rsp.text
                name_match = re.search(r'<span class="uname">([^<]+)</span>', html)
                if name_match:
                    self.name = name_match.group(1).strip()
                    logger.debug(f"获取到用户名: {self.name}")
        except Exception as e:
            logger.debug(f"获取用户信息失败: {e}")

    def __checkLogin(self) -> bool:
        _url = "https://i.chaoxing.com/base/settings?t=1677930825027"
        _headers = {
            'Refer': 'https://i.chaoxing.com/base?t=1677930160468'
        }
        _headers.update(self.headers)
        _rsp = doGet(url=_url, headers=_headers, ifFullBack=True)
        if _rsp and getattr(_rsp, "status_code", 0) == 200:
            return True
        else:
            return False

    def __str__(self):
        return "\n".join([f"Uid: {self.uid}",
                          f"Username: {self.username}",
                          f"Cookie: {self.cookieStr}"])

    def getCourse(self):
        self.course_list.clear()
        try:
            url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
            data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
            headers = {
                "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            headers.update(self.headers)

            html = doPost(url=url, headers=headers, data=data)
            if not html:
                logger.error("获取课程列表失败：响应为空")
                return

            logger.debug(f"课程列表HTML长度: {len(html)}")
            if len(html) < 500:
                logger.debug(f"课程列表HTML内容: {html[:1000] if len(html) > 1000 else html}")

            ele = etree.HTML(html)
            if ele is None:
                logger.error("解析课程列表HTML失败")
                return

            course_ele = ele.xpath("//div[contains(@class, 'course')]")
            if not course_ele:
                course_ele = ele.xpath("//ul[@id='courseList']/li")

            logger.debug(f"找到 {len(course_ele)} 个课程元素")

            for item in course_ele:
                try:
                    ifOpen = True
                    not_open = item.xpath(".//a[contains(@class, 'not-open-tip')]") or item.xpath(".//div[contains(@class, 'not-open-tip')]")
                    if not_open:
                        ifOpen = False

                    course_id = xpath_first(item, ".//input[contains(@class, 'courseId')]/@value")
                    if not course_id:
                        course_id = xpath_first(item, ".//input[@class='courseId']/@value")
                    clazz_id = xpath_first(item, ".//input[contains(@class, 'clazzId')]/@value")
                    if not clazz_id:
                        clazz_id = xpath_first(item, ".//input[@class='clazzId']/@value")

                    a_ele = item.xpath(".//a")
                    tmp_url = ""
                    if a_ele:
                        tmp_url = a_ele[0].get("href", "")

                    cpi = ""
                    if tmp_url and "cpi=" in tmp_url:
                        cpi_match = re.findall(r"cpi=(.*?)(?:&|$)", tmp_url)
                        if cpi_match:
                            cpi = cpi_match[0]

                    title = xpath_first(item, ".//span[contains(@class, 'course-name')]/@title")
                    if not title:
                        title = xpath_first(item, ".//h3/a/span/@title")
                    if not title:
                        h3_span = item.xpath(".//h3/a/span")
                        if h3_span:
                            title = h3_span[0].text or ""

                    teacher = xpath_first(item, ".//p[contains(@class, 'color3')]/@title")
                    if not teacher:
                        teacher = xpath_first(item, ".//p[@class='line2 color3']/@title")
                    if not teacher:
                        p_color3 = item.xpath(".//p[contains(@class, 'color3')]")
                        if p_color3:
                            teacher = p_color3[0].text or ""

                    if course_id and clazz_id and title:
                        course = Course(
                            course_id,
                            clazz_id,
                            tmp_url,
                            title,
                            teacher,
                            cpi=cpi,
                            headers=self.headers,
                            ifOpen=ifOpen
                        )
                        logger.debug(f"Add course: {title} (courseId={course_id}, clazzId={clazz_id})")
                        self.course_list.append(course)
                    elif not title:
                        logger.debug(f"跳过空课程名称: courseId={course_id}, clazzId={clazz_id}")
                except Exception as e:
                    logger.warning(f"解析课程信息失败: {e}")

            try:
                interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
                interaction_html = doGet(url=interaction_url, headers=self.headers)
                if interaction_html:
                    interaction_ele = etree.HTML(interaction_html)
                    if interaction_ele is not None:
                        folder_ele = interaction_ele.xpath("//ul[@class='file-list']/li")
                        for folder in folder_ele:
                            folder_id = folder.get("fileid", "")
                            if folder_id:
                                folder_data = {
                                    "courseType": 1,
                                    "courseFolderId": folder_id,
                                    "query": "",
                                    "superstarClass": 0,
                                }
                                folder_html = doPost(url=url, headers=headers, data=folder_data)
                                if folder_html:
                                    folder_ele_list = etree.HTML(folder_html)
                                    if folder_ele_list is not None:
                                        folder_courses = folder_ele_list.xpath("//div[@class='course']")
                                        for item in folder_courses:
                                            try:
                                                ifOpen = True
                                                not_open = item.xpath(".//a[@class='not-open-tip']") or item.xpath(".//div[@class='not-open-tip']")
                                                if not_open:
                                                    ifOpen = False

                                                course_id = xpath_first(item, ".//input[@class='courseId']/@value")
                                                clazz_id = xpath_first(item, ".//input[@class='clazzId']/@value")

                                                a_ele = item.xpath(".//a")
                                                tmp_url = ""
                                                if a_ele:
                                                    tmp_url = a_ele[0].get("href", "")

                                                cpi = ""
                                                if tmp_url and "cpi=" in tmp_url:
                                                    cpi_match = re.findall(r"cpi=(.*?)&", tmp_url)
                                                    if cpi_match:
                                                        cpi = cpi_match[0]

                                                title = xpath_first(item, ".//span[@class='course-name']/@title")
                                                if not title:
                                                    title = xpath_first(item, ".//h3/a/span/@title")

                                                teacher = xpath_first(item, ".//p[@class='color3']/@title")
                                                if not teacher:
                                                    teacher = xpath_first(item, ".//p[@class='line2 color3']/@title")

                                                if course_id and clazz_id:
                                                    course = Course(
                                                        course_id,
                                                        clazz_id,
                                                        tmp_url,
                                                        title,
                                                        teacher,
                                                        cpi=cpi,
                                                        headers=self.headers,
                                                        ifOpen=ifOpen
                                                    )
                                                    logger.debug(f"Add course from folder: {title} (courseId={course_id}, clazzId={clazz_id})")
                                                    self.course_list.append(course)
                                            except Exception as e:
                                                logger.warning(f"解析文件夹课程信息失败: {e}")
            except Exception as e:
                logger.debug(f"获取二级课程列表失败: {e}")

            logger.info(f"成功获取 {len(self.course_list)} 门课程")

        except Exception as e:
            logger.error(f"获取课程列表异常: {e}")
