#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V7 单入口：默认仅检查。--live 将实际发送消息。"""
import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
MATCH = BASE / 'soul_v6_5_10_match_continue.py'
CHAT = BASE / 'soul_v6_unified_integrated_v4_2_keep_going.py'

def main():
    p=argparse.ArgumentParser(description='SOUL V7 单入口：灵魂匹配 + 主动聊天 + 双屏处理 + 新招呼 + 奇遇铃')
    p.add_argument('--live',action='store_true',help='实际触摸屏幕和发送消息，默认仅做预检查')
    p.add_argument('--max',type=int,default=10,help='单批灵魂匹配次数，1-10')
    p.add_argument('--chat-only',action='store_true',help='仅运行主聊天双屏逻辑，手机需已在主聊天列表')
    a=p.parse_args()
    if not 1<=a.max<=10:p.error('--max 必须在 1 到 10 之间')
    for f in (MATCH,CHAT):
        if not f.is_file():p.error('缺少依赖文件：'+f.name)
    if not a.live:
        for f in BASE.glob('*.py'):
            compile(f.read_text(encoding='utf-8-sig'),str(f),'exec')
        print('✅ V7 源码语法检查通过。未连接手机、未发送消息。')
        print('完整运行：python3 soul_v7.py --live --max 10')
        print('只处理聊天：python3 soul_v7.py --live --chat-only')
        return 0
    if a.chat_only:
        # 注意：不调用匹配脚本的 --chat-only，避免页面已在消息列表时被误判为主页
        return subprocess.call([sys.executable,'-u',str(CHAT),'--live','--special-live','--special-max','5'],cwd=BASE)
    return subprocess.call([sys.executable,'-u',str(MATCH),'--live','--max',str(a.max),'--chat','--special-live'],cwd=BASE)

if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:
        print('\n用户主动停止');raise SystemExit(130)
