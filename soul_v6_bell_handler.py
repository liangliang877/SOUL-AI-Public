#!/usr/bin/env python3
"""SOUL V6 奇遇铃·稍后再聊：列表识别 / 单人开场草稿 / 单人安全发送。
默认只读。--draft 消耗 API Token 但不操作手机；--send --username 才允许真实发送。
不执行列表遍历、自动点击对象或自动返回，留待逐步实测。
依赖：同目录 soul_v6_greeting_handler_v2.py（只复用 XML / ADB / AI 的辅助函数）。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET
from datetime import datetime
import soul_v6_greeting_handler_v2 as h
from soul_v7_system_ai import is_system_ai

STATE = Path(__file__).resolve().parent / 'soul_bell_send_state'

def controls(root, name):
    return [n for n in root.iter('node') if h.rid(n) == name]

def bell_rows(root):
    lists = controls(root, 'recycler_view')
    if len(lists) != 1 or controls(root, 'et_sendmessage'):
        raise RuntimeError('未能唯一确认奇遇铃列表，安全停止')
    result = []
    for n in lists[0].iter('node'):
        if h.rid(n) != 'content_layout':
            continue
        names = [h.text(x) for x in n.iter('node') if h.rid(x) == 'tv_name' and h.text(x)]
        if len(names) != 1:
            continue
        hints = [h.text(x) for x in n.iter('node') if h.rid(x) == 'tv_from_label' and h.text(x)]
        result.append({'name': names[0], 'hint': hints[:1]})
    return result

def bell_detail(root):
    if len(controls(root, 'et_sendmessage')) != 1:
        raise RuntimeError('没有唯一识别到聊天输入框')
    name = h.chat_title(root)
    if len(controls(root, 'item_left_back')) != 1:
        raise RuntimeError('没有唯一识别到私聊返回控件')
    # 只从已发现的个性签名和共同点中提取公开资料，不把推荐话术当作对方发言。
    labels = [h.text(n) for n in root.iter('node') if h.text(n)]
    signatures = [t.removeprefix('Ta的签名：') for t in labels if t.startswith('Ta的签名：')]
    interests = [t for t in labels if t in ('创业进行中','失恋期','音乐爱好者','吃货一枚','找兴趣搭子','拓展人际关系')]
    extra = [t for t in labels if t.startswith('你们的共同点：')]
    mbti = [h.text(n) for n in controls(root, 'tv_tag_text') if h.text(n)]
    return {'name': name, 'signature': signatures[0] if signatures else '',
            'traits': (mbti + interests + extra)[:12]}

def state_paths(username):
    uid = hashlib.sha256(username.encode('utf-8')).hexdigest()[:20]
    return STATE / f'{uid}.pending.json', STATE / f'{uid}.sent.json'

def make_draft(detail):
    # 使用现有已连通的模型配置和提示词；避免复制 API key 到新文件。
    return h.draft_intro(detail)

def send_once(username, reply):
    STATE.mkdir(exist_ok=True)
    pending, sent = state_paths(username)
    if pending.exists() or sent.exists():
        raise RuntimeError('此用户存在已发送或待核实记录；禁止自动重复发送')
    root = h.read_page_from_adb()
    if is_system_ai(root):
        raise RuntimeError('已确认系统AI会话：禁止发送奇遇铃开场白')
    detail = bell_detail(root)
    if detail['name'] != username:
        raise RuntimeError('当前私聊对象与指定用户名不符；停止')
    # 奇遇铃页面没有新招呼 tv_say_hello_button，故不能直接调用 greeting.do_one_send。
    # 禁止对已有可见聊天消息的页面再发送自动开场，防止误发到已开始的对话。
    if controls(root, 'content_text') or controls(root, 'voice_bubble'):
        raise RuntimeError('发现已有聊天消息；不把现有会话当作首次开场处理')
    baseline = h.count_visible_reply(root, reply)
    box = h.one_node(root, 'et_sendmessage', enabled=True)
    if box.attrib.get('clickable') != 'true':
        raise RuntimeError('输入框不可点击')
    x, y = h.center(box)
    h.adb_action('shell','input','tap',x,y)
    time.sleep(.4)
    h.adb_action('shell','am','broadcast','-a','ADB_CLEAR_TEXT')
    time.sleep(.2)
    h.adb_action('shell','am','broadcast','-a','ADB_INPUT_TEXT','--es','msg',reply)
    time.sleep(.7)
    root = h.read_page_from_adb()
    if h.chat_title(root) != username or h.text(h.one_node(root,'et_sendmessage')) != reply:
        raise RuntimeError('昵称或草稿验证失败；停止，不点击发送')
    buttons = [n for n in controls(root,'btn_send') if h.text(n) == '发送'
               and n.attrib.get('clickable') == 'true' and n.attrib.get('enabled') == 'true']
    if len(buttons) != 1:
        raise RuntimeError(f'发送按钮不唯一（{len(buttons)}）；保留草稿，不发送')
    sx, sy = h.center(buttons[0])
    payload = {'username':username, 'reply':reply, 'created_at':datetime.now().isoformat(),
               'baseline':baseline, 'status':'about_to_tap'}
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    h.adb_action('shell','input','tap',sx,sy)
    payload['status'] = 'tap_sent_unverified'
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    for _ in range(3):
        time.sleep(1)
        root = h.read_page_from_adb()
        if h.chat_title(root) != username:
            raise RuntimeError('点击后聊天对象变化；待核实记录已保留，禁止自动重发')
        if h.count_visible_reply(root, reply) > baseline:
            sent.write_text(json.dumps(payload | {'status':'verified'},ensure_ascii=False,indent=2),encoding='utf-8')
            pending.unlink()
            print("🔒 运行状态已记录，个人信息已隐藏")
            return
    raise RuntimeError('未确认新气泡；待核实记录已保留，请人工核对，禁止自动重发')

def main():
    parser = argparse.ArgumentParser(description='奇遇铃-稍后再聊 单人安全处理模块')
    parser.add_argument('--xml', help='使用本地 XML 做离线静态识别')
    parser.add_argument('--draft', action='store_true', help='生成草稿，消耗 Token，不发送')
    parser.add_argument('--send', action='store_true', help='真实发送单条开场白（需 --username）')
    parser.add_argument('--username', help='必须与当前私聊顶部昵称完全一致')
    args = parser.parse_args()
    if args.send and (not args.username or args.xml or args.draft):
        parser.error('--send 必须指定 --username，且不能和 --xml / --draft 同时使用')
    root = ET.parse(args.xml).getroot() if args.xml else h.read_page_from_adb()
    if controls(root, 'et_sendmessage'):
        detail = bell_detail(root)
        print('✅ 已识别私聊：', detail['name'])
        print('签名：', detail['signature'] or '无')
        print('其他资料：', '；'.join(detail['traits']) or '无')
        if args.draft:
            print('🧠 开场白草稿（未发送）：',make_draft(detail))
        elif args.send:
            if detail['name'] != args.username:
                raise RuntimeError('用户昵称不匹配，禁止发送')
            pending, sent = state_paths(args.username)
            if pending.exists() or sent.exists():
                raise RuntimeError('用户已有发送/待核实记录；禁止重复发送')
            reply = make_draft(detail)
            print("🔒 运行状态已记录，个人信息已隐藏")
            send_once(args.username,reply)
        else:
            print('🔒 只读模式；未发送')
    else:
        if args.draft or args.send:
            raise RuntimeError('当前是列表，不是私聊；仅允许观察列表')
        rows = bell_rows(root)
        print(f'✅ 奇遇铃列表识别：{len(rows)} 条可见记录')
        for i,row in enumerate(rows,1):
            print(f"{i}. {row['name']} — {'；'.join(row['hint'])}")
        print('🔒 只读模式；未点击、未调用AI、未发送')

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, ET.ParseError, FileNotFoundError) as exc:
        raise SystemExit(f'❌ 安全停止：{exc}')
