# _*_ coding:utf-8 _*_
import classis.User
from classis.Media.Live import Live
from classis.Media.Video import Video
from classis.SelfException import RequestException
from functions.deal_mission import DealCourse
import random
import time


class DealVideo:

    def __init__(self, user: classis.User.User, course: classis.User.Course, log):
        self.user = user
        self.course = course
        self.log = log

    @staticmethod
    def run_video(video: Video, user, log, all_time: int = 0) -> bool:
        import threading
        thread_name = threading.current_thread().name

        log.info(f"[{thread_name}] 开始处理视频: {video.name}")
        return video.study(all_time=all_time, log=log)

    @staticmethod
    def run_live(live: Live, user, log):
        import threading
        thread_name = threading.current_thread().name

        live_status = live.get_status()
        if live_status:
            duration = live_status.get("temp").get("data").get('duration')
            _headers = {
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'Connection': 'keep-alive',
                'Content-Type': 'application/json',
                'Sec-Fetch-Dest': 'empty',
                'Host': 'mooc1.chaoxing.com',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'Referer': 'https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2023-0203-1904'
            }
            _headers.update(user.headers)
        else:
            raise RequestException("直播状态获取失败")

        time_all = round(duration / 60, 2)
        log.info(f"[{thread_name}] 直播 '{live.name}' 开始刷取，总时长: {time_all} 分钟")

        play_time = 0

        while play_time < duration:
            wait_time = random.uniform(30, 90)
            actual_wait = min(wait_time, duration - play_time)
            play_time = min(play_time + actual_wait, duration)

            time.sleep(actual_wait)

            progress_percent = round(play_time / duration * 100, 1)
            log.info(f"[{thread_name}] '{live.name}' 进度: {progress_percent}% ({int(play_time)}/{duration}秒, 间隔 {int(actual_wait)}秒)")
            live.do_finish()

        log.info(f"[{thread_name}] 直播 '{live.name}' 刷取完成！")

    def get_videos(self):
        """
        获得该课程的所有视频对象
        :return:
        """
        _data = self.course.get_progress_data()
        _video_list = []
        for i in _data:
            for j in i['list']:
                if j['type'] == '视频':
                    _dc = DealCourse(self.user, self.course, self.log)
                    attachments = _dc.deal_chapter({'knowledge_id': j.get("chapterId")})
                    for attach_item in attachments:
                        medias = attach_item.get("attachments")
                        defaults = attach_item.get("defaults")
                        for media in medias:
                            media_type = media.get("type")
                            media_module = media.get('property').get('module')
                            media_name = media.get('property').get('name')
                            if media_type == "video":
                                if media_module == "insertaudio":
                                    pass
                                else:
                                    _video_list.append(Video(media, self.user.headers, defaults, name=media_name,
                                                              course_id=self.course.course_id, class_id=self.course.class_id,
                                                              userid=self.user.uid))
        return _video_list
