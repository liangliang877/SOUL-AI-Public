#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOUL 匹配成功后的单人首次问候；默认只检查，--live 才真实发送。
必须处于匹配成功的私聊页；输入后核对完整文本和唯一发送按钮。
发送意图持久化，遇到不确定状态不自动重试。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / 'soul_match_send_state'
ADB = os.environ.get('ADB', 'adb')
DEFAULT_MESSAGE = '你说，咱俩匹配成功，是缘分还是系统偷偷安排的？😂'
# SOUL 新账号默认文案：由 XML text 提供，但不是已输入的草稿。
EMPTY_INPUT_HINTS = {'', '发消息', '发消息或按住说话', '文明聊天，友善交友~'}


def adb(*args, timeout=40):
    p = subprocess.run([ADB, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f'ADB操作失败：{p.stderr.strip() or p.stdout.strip()}')
    return p.stdout


def xml():
    adb('shell', 'uiautomator', 'dump', '/sdcard/soul_match_send_probe.xml')
    return ET.fromstring(adb('shell', 'cat', '/sdcard/soul_match_send_probe.xml'))


def sid(n):
    return n.attrib.get('resource-id','').split('/')[-1]


def find(root, key):
    return [n for n in root.iter('node') if sid(n) == key]


def xy(n):
    m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', n.attrib.get('bounds',''))
    if not m:
        raise RuntimeError('控件坐标无法验证')
    x1,y1,x2,y2=map(int,m.groups())
    if x1>=x2 or y1>=y2:
        raise RuntimeError('控件没有有效点击区域')
    return (x1+x2)//2,(y1+y2)//2


# 顶部的「正在查看你的主页」等动态文案不是聊天对象的稳定昵称。
TRANSIENT_TITLE_WORDS = ('正在查看', '查看你的主页', '查看了你的主页', '浏览你的主页', '主页中')


def title(root):
    found=[]
    for n in find(root,'tv_title'):
        try: x,y=xy(n)
        except RuntimeError: continue
        if y < 260 and n.attrib.get('text','').strip():
            found.append(n.attrib['text'].strip())
    if len(found)!=1:
        raise RuntimeError(f'聊天顶部标题未唯一显示：{found}')
    return found[0]


def transient_title(value):
    return bool(value) and any(word in value for word in TRANSIENT_TITLE_WORDS)


def verified(root, expected, *, allow_title_hidden=False):
    # 页面身份依靠聊天输入框、返回控件及弹窗状态综合确认；
    # 昵称用于检测明确的换人，不能把动态状态文案当成不同用户。
    field=find(root,'et_sendmessage')
    backs=find(root,'item_left_back')
    if len(field)!=1 or field[0].attrib.get('enabled')!='true' or len(backs)!=1:
        raise RuntimeError('没有确认唯一的匹配私聊输入框及返回控件')
    if any(find(root,k) for k in ('tv_love_chat','tv_love_recall')):
        raise RuntimeError('出现同城奇遇弹窗，放弃本轮发送')
    try:
        name=title(root)
    except RuntimeError:
        name=''
    if name and not transient_title(name) and expected and not transient_title(expected) and name!=expected:
        raise RuntimeError(f'聊天顶部真实昵称发生变化：预期 {expected!r}，实际 {name!r}')
    if not name or transient_title(name):
        print('ℹ️ 顶部昵称正在动态切换；已通过私聊页面结构确认，继续核验输入框',flush=True)
    return field[0]


def send_button(root):
    candidates=[n for n in find(root,'btn_send') if n.attrib.get('enabled')=='true' and n.attrib.get('clickable')=='true']
    if len(candidates)!=1:
        raise RuntimeError(f'未确认唯一的发送按钮（{len(candidates)}个）')
    return candidates[0]


def main():
    p=argparse.ArgumentParser(description='匹配成功私聊页首次问候。默认只检查。')
    p.add_argument('--name',default='',help='当前页面识别到的昵称，可为空或动态状态文案')
    p.add_argument('--session-key',default='',help='本次匹配的唯一轮次标识，避免不同对象同叫Souler时发生账本冲突')
    p.add_argument('--message',default=DEFAULT_MESSAGE)
    p.add_argument('--live',action='store_true')
    p.add_argument('--fast-after-click',action='store_true',help='点击发送后不再次抓取XML，直接返回调用方；不代表已确认送达')
    p.add_argument('--xml',type=Path,help='离线 XML 检查，不发送')
    args=p.parse_args()
    if args.xml and args.live:
        p.error('--xml 与 --live 不能同时使用')
    root=ET.parse(args.xml).getroot() if args.xml else xml()
    field=verified(root,args.name)
    print('✅ 已确认匹配私聊结构，当前顶部文字：', args.name or '动态/未显示')
    print(f'✅ 输入框存在，当前内容：{field.attrib.get("text", "")!r}')
    print('注意：匹配成功时可能出现系统推荐问候卡片，必须先点击输入框后再次核对。')
    if not args.live:
        print('🔒 仅检查，未发送。实际发送添加 --live。')
        return 0
    if field.attrib.get('text','').strip() not in EMPTY_INPUT_HINTS:
        raise RuntimeError('输入框已有草稿，拒绝覆盖')
    # 有已知聊天消息时拒绝自动发送“第一次问候”。
    if any(find(root,k) for k in ('content_text','voice_bubble')):
        raise RuntimeError('发现已有消息，交给普通聊天模块，不重复发送开场白')
    STATE.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256(('round:'+args.session_key if args.session_key else 'legacy:'+args.name).encode()).hexdigest()[:24]
    path=STATE/(key+'.json')
    if path.exists():
        raise RuntimeError(f'已有发送/待核实账本 {path}，拒绝自动重复发送；请先核实聊天窗口中是否已发送，不要直接删除')
    # 在触碰输入框前持久化待核实状态，防止中途崩溃重复发送。
    pending={'name':args.name,'match_session_key':args.session_key,'message':args.message,'status':'pending_verification','created':time.time()}
    with path.open('x',encoding='utf-8') as f:
        json.dump(pending,f,ensure_ascii=False,indent=2)
        f.flush();os.fsync(f.fileno())
    try:
        x,y=xy(field)
        adb('shell','input','tap',x,y)
        time.sleep(0.8)
        root=xml(); field=verified(root,args.name,allow_title_hidden=True)
        if field.attrib.get('focused')!='true':
            raise RuntimeError('输入框未获得焦点，停止')
        if field.attrib.get('text','').strip() not in EMPTY_INPUT_HINTS:
            raise RuntimeError('输入框已存在文字，停止')
        # 使用现有项目中的 ADB Keyboard IME 广播。这里与旧稳定版一致。
        adb('shell','am','broadcast','-a','ADB_INPUT_TEXT','--es','msg',args.message)
        time.sleep(1.0)
        root=xml(); field=verified(root,args.name,allow_title_hidden=True)
        actual=field.attrib.get('text','')
        if actual!=args.message:
            raise RuntimeError(f'草稿未完全匹配（读取到 {actual!r}），未点击发送')
        button=send_button(root)
        x,y=xy(button)
        adb('shell','input','tap',x,y)
        # 简洁模式：点发送后不再重新扫描私聊页面。
        # 此时只能确认点击命令已成功下发，不声称消息送达。
        if not args.fast_after_click:
            time.sleep(0.5)
            root=xml(); field=verified(root,args.name,allow_title_hidden=True)
            if field.attrib.get('text','')==args.message:
                raise RuntimeError('点击后草稿仍在输入框，发送结果不确定')
        pending['status']='clicked_send_delivery_unverified'
        path.write_text(json.dumps(pending,ensure_ascii=False,indent=2),encoding='utf-8')
        print('✅ 已执行发送按钮点击；不再核对发送后的聊天页')
        print(f'🛡️ 防重复发送记录：{path}')
    except Exception:
        print(f'🛑 保留待核实记录：{path}；请人工确认实际发送状态后处理。',file=sys.stderr)
        raise
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:
        print(f'❌ 安全停止：{e}',file=sys.stderr)
        sys.exit(40)
