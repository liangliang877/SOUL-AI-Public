# SOUL AI Automation

基于 Python、ADB 和大语言模型的 Android 自动化实验项目。

## 项目简介
探索 AI 辅助编程、Android 界面自动化和聊天上下文处理。

## 主要功能
- ADB 无线连接安卓设备
- SOUL 灵魂匹配页面识别
- 匹配成功后的消息处理
- 聊天消息读取与 AI 回复
- 打招呼及奇遇铃页面处理
- 系统 AI 账号过滤
- 部分异常页面自动返回

## 当前版本
V7.6 Experimental（实验版）

## 安装依赖
python3 -m pip install -r requirements.txt

## 注意事项
项目尚未经过长期稳定性测试。请勿公开 API Key、个人资料及真实聊天记录。使用时请遵守平台规则并尊重其他用户。

## 环境准备

1. 安装 Python 3。
2. 安装 Android Platform Tools（ADB）。
3. 在安卓手机开发者选项中开启无线调试。
4. 安装 Python 依赖：python3 -m pip install -r requirements.txt
5. 设置 ARK_API_KEY 环境变量。
6. 将 soul_profile.example.json 复制为 soul_profile.json，并填写自己的资料。

## 启动方式

查看帮助：python3 soul_v7.py --help

预检查：python3 soul_v7.py

指定匹配次数（预检查）：python3 soul_v7.py --max 8

实际运行：python3 soul_v7.py --live --max 8

仅运行聊天模块：python3 soul_v7.py --live --chat-only

注意：--live 会执行实际手机操作及消息发送。请先确认设备连接与程序行为，并遵守平台规则。

## Android 设备兼容性

当前 V7.6 为实验版本，主要基于开发者使用的安卓设备进行测试。

- 大部分点击操作使用 UIAutomator 控件边界动态定位。
- 部分聊天列表和奇遇铃滑动操作仍采用固定坐标。
- 不同屏幕分辨率、系统版本及 SOUL 应用版本可能导致识别失败。
- 尚未完成多机型兼容性测试。
- 建议先使用默认预检查模式，不要直接开启 --live。

本项目不保证在所有 Android 设备上正常运行。
