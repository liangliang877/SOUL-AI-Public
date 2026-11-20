#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOUL V6: verified Soul Match entry only.

Default: read current UI XML and print the verified match card / remaining count.
--live: click exactly the verified Soul Match card once, then save resulting page XML.
Never clicks voice match / face match. Does not send a message or perform batch matching.
"""
import argparse
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ADB_ID = 'cn.soulapp.android:id/'
REMOTE = '/sdcard/soul_v6_match_entry.xml'

class MatchError(Exception):
    pass

def adb(*args, timeout=35):
    return subprocess.run(['adb', *map(str, args)], check=True, capture_output=True, text=True, timeout=timeout).stdout

def get_page():
    adb('shell', 'uiautomator', 'dump', REMOTE)
    return adb('shell', 'cat', REMOTE)

def center(bounds):
    m = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds or '')
    if not m:
        raise MatchError(f'坐标格式异常：{bounds!r}')
    x1,y1,x2,y2=map(int,m.groups())
    if x2<=x1 or y2<=y1:
        raise MatchError('控件面积异常')
    return (x1+x2)//2,(y1+y2)//2

def inspect(xml):
    root=ET.fromstring(xml)
    ns=list(root.iter('node'))
    p={child:parent for parent in root.iter() for child in parent}
    titles=[n for n in ns if n.get('resource-id')==ADB_ID+'main_title' and n.get('text','').strip()=='灵魂匹配']
    if len(titles)!=1:raise MatchError(f'灵魂匹配标题应唯一，实际 {len(titles)} 个')
    card=p.get(titles[0])
    if card is None or card.get('resource-id')!=ADB_ID+'ratioContainer':
        raise MatchError('灵魂匹配所属卡片结构变化，拒绝操作')
    if card.get('clickable')!='true' or card.get('enabled')!='true':
        raise MatchError('灵魂匹配卡片不可点击')
    card_ns=list(card.iter('node'))
    matches=lambda rid:[n for n in card_ns if n.get('resource-id')==ADB_ID+rid]
    subtitles=matches('sub_title')
    buttons=[n for n in matches('tv_button_left_bottom') if n.get('text','').strip()=='开始匹配']
    if len(subtitles)!=1 or len(buttons)!=1:raise MatchError('灵魂匹配卡片缺少唯一的剩余次数或开始匹配按钮')
    raw=subtitles[0].get('text','').strip()
    count=re.fullmatch(r'今日剩余\s*(\d+)\s*次',raw)
    if not count:raise MatchError(f'剩余次数文本不符合预期：{raw}')
    remaining=int(count.group(1))
    # Reject any title for a disallowed match type inside the selected subtree.
    if any(n.get('text','').strip() in ('语音匹配','萌面匹配') for n in card_ns):
        raise MatchError('目标卡片混有其他匹配入口')
    return remaining,center(card.get('bounds')),card.get('bounds')

def main():
    arg=argparse.ArgumentParser(description='只识别并选择灵魂匹配入口；默认只读')
    arg.add_argument('--xml',type=Path,help='离线读取页面 XML')
    arg.add_argument('--live',action='store_true',help='真实点击灵魂匹配入口一次')
    arg.add_argument('--after',type=Path,default=Path('/tmp/soul_v6_after_match.xml'))
    args=arg.parse_args()
    if args.live and args.xml:arg.error('--live 和 --xml 不可同时使用')
    try:
        if not args.xml:
            devs=[x for x in adb('devices').splitlines()[1:] if x.strip().endswith('\tdevice')]
            if len(devs)!=1:raise MatchError(f'须有且仅有一台已连接设备，当前 {len(devs)} 台')
        xml=args.xml.read_text(encoding='utf-8') if args.xml else get_page()
        remaining,xy,bounds=inspect(xml)
        print(f'✅ 唯一确认「灵魂匹配」卡片：{bounds}，今日剩余 {remaining} 次')
        print('🔒 语音匹配、萌面匹配未选择')
        if not args.live:
            print('🔒 当前只读，未点击、未消耗匹配次数、未发送消息')
            return 0
        if remaining == 0:
            print('✅ 次数为0，不执行点击')
            return 0
        # Immediately re-read right before clicking; reject stale coordinates or changed screen.
        now_remaining,now_xy,now_bounds=inspect(get_page())
        if (now_remaining,now_xy,now_bounds)!=(remaining,xy,bounds):
            raise MatchError('点击前页面卡片或次数发生变化，安全停止')
        print(f'▶️ 确认入口后点击灵魂匹配卡片一次：{xy}')
        adb('shell','input','tap',*xy)
        time.sleep(2.2)
        after=get_page()
        # Preserve the subsequent interface for integration of the actual match result flow.
        args.after.parent.mkdir(parents=True,exist_ok=True)
        args.after.write_text(after,encoding='utf-8')
        print('✅ 已点击唯一验证的灵魂匹配卡片，后续界面保存：'+str(args.after))
        print('⚠️ 仅验证进入匹配入口；不会自动连续匹配或发送消息')
        return 0
    except (MatchError,ET.ParseError,subprocess.SubprocessError,OSError) as e:
        print('❌ 安全停止：',e,file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
