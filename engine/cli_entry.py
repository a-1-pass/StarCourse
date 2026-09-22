# -*- coding: utf-8 -*-
"""StarCourse CLI entry — interactive command-line interface."""
import yaml
import os
from loguru import logger
import sys
import importlib

from classis.SelfException import LoginException
from config import GloConfig
from classis.User import User
from classis.UserLogger import UserLogger
from utils import *

logger.remove()
log = UserLogger()


def sign_in(infoStr: dict, auto_login: bool = True) -> User:
    sign_status = False
    sign_mode = ''
    clear_console()
    if auto_login and GloConfig.data.get("UserData").get("auto-sign") and GloConfig.data.get("UserData").get("cookie"):
        log.info("您已开启自动登录模式且本地已检测到账号Cookie,即将校验登录...")
        try:
            _user = User(cookieStr=GloConfig.data.get("UserData").get("cookie"))
            sign_status = True
        except LoginException:
            log.error("本地Cookie错误，即将进入常规登录...")
    else:
        sign_mode = input(infoStr.get("signStr"))
        while sign_mode not in ["1", "2", ""]:
            print("\n" + infoStr.get("errSignMode"))
            sign_mode = input(infoStr.get("signStr"))
    while not sign_status:
        try:
            if sign_mode == '' or sign_mode == "1":
                username = input("\n请输入您的用户名(手机号):")
                password = input("请输入您的密码:")
                _user = User(username, password)
            else:
                cookie = input("请输入账号的Cookie:")
                _user = User(cookieStr=cookie)
            sign_status = True
        except Exception as e:
            log.error(e)
    logger.debug(_user)
    log.success("恭喜你 %s,登录成功" % _user.name)
    return _user


def get_course(_user: User):
    log.info("运行读取课程任务")
    _user.getCourse()
    log.success("课程读取成功")
    if not _user.course_list:
        log.info("读取到您没有可执行的课程，如果与您的实际情况不符请提交issue")
        exit()


def get_config():
    sys.path.append(".")
    GloConfig.init_yaml_data()
    _config = GloConfig.data
    config_debug = _config.get("GloConfig").get("debug")
    if config_debug.get("enable"):
        logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
                   filter=lambda record: record["extra"].get("name") != "a",
                   level=config_debug.get("level"))
    return _config


if __name__ == '__main__':
    config = get_config()
    clear_console()
    print(config.get("InfoStr").get("instructions"))
    input("\n回车确认后正式使用本软件:")
    user = sign_in(config.get("InfoStr"))
    get_course(user)

    functions = []
    path_list = os.listdir('./functions')
    for module_path in path_list:
        logger.debug("Loading function %s" % module_path)
        functions.append(importlib.import_module("functions.%s" % module_path))
        logger.success("Loaded %s successfully" % module_path)

    menu_str = "菜单"
    for fun_ind in range(len(functions)):
        menu_str += f"\n{fun_ind + 1}.{functions[fun_ind].__disName__}"

    # additional menu
    menu_str += f"\n{len(functions) + 1}. 🔃 退出当前已登录账号" + \
                f"\n{len(functions) + 2}. 💾 保存设置并退出本程序"

    while True:
        try:
            print(menu_str)
            fun_i = int(input("\n请输入您要使用功能的序号:"))
            if fun_i == len(functions) + 1:
                clear_console()
                user = sign_in(config.get("InfoStr"), False)
                get_course(user)
                continue
            elif fun_i == len(functions) + 2:
                if config['UserData']['auto-sign']:
                    config['UserData']['cookie'] = user.cookieStr
                GloConfig.release_yaml_data()
                exit(0)
            else:
                func = functions[fun_i - 1]
                clear_console()
                print("\n".join([f"功能名称: {func.__disName__}",
                                 f"作者: {func.__author__}",
                                 f"使用须知: {func.__description__}"])
                      )
                if input("\n回车确认使用本功能，其他输入则会退回主菜单:") == "":
                    func.run(user, log)
                else:
                    clear_console()
                    continue

        except Exception as e:
            log.error("功能调用失败，请检查输入的功能序号")
            logger.error(e)
