#!/usr/bin/env python3
"""SOUL V6 新招呼批处理调度器。

默认只读；--live 明确授权后才能点击、调用现有单次发送模块。
每次返回必须确认仍在「收到的新招呼」列表，否则安全停止。
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent
HANDLER = BASE / 'soul_v6_greeting_handler_v2.py'

# 与已验证的单次发送程序共享页面解析和 ADB 功能，不复制发送逻辑。
import soul_v6_greeting_handler_v2 as h
from soul_v7_system_ai import is_system_ai, log_skip


def greeting_list(root):
    lists = [n for n in root.iter('node') if h.rid(n) == 'rv_new_greeting_list']
    titles = [h.text(n) for n in root.iter('node') if h.rid(n) == 'tv_title']
    if len(lists) != 1 or '收到的新招呼' not in titles:
        raise RuntimeError('当前不是已验证的「收到的新招呼」列表，停止')
    rows = []
    for n in lists[0].iter('node'):
        if h.rid(n) != 'item_content_root':
            continue
        names = [h.text(c) for c in n.iter('node') if h.rid(c) == 'name' and h.text(c)]
        if len(names) != 1:
            continue
        coords = h.center(n)
        rows.append({'name': names[0], 'xy': coords})
    if len({x['name'] for x in rows}) != len(rows):
        raise RuntimeError('出现重复昵称，无法可靠区分用户，停止')
    return rows


def record_files(name):
    uid = hashlib.sha256(name.encode('utf-8')).hexdigest()[:20]
    state = BASE / 'soul_greeting_send_state'
    return state / (uid + '.pending.json'), state / (uid + '.sent.json')


def already_handled(name):
    pending, sent = record_files(name)
    return pending.exists() or sent.exists()


def verify_detail(name):
    root = h.read_page_from_adb()
    if h.chat_title(root) != name:
        raise RuntimeError(f'点击后昵称不匹配：预期{name}，实际{h.chat_title(root)}')
    detail = h.read_detail(root)
    if detail['name'] != name or not detail['is_greeting']:
        raise RuntimeError('不是目标用户的新招呼详情页，拒绝发送')
    return root


def back_to_greetings(name):
    root = h.read_page_from_adb()
    if h.chat_title(root) != name:
        raise RuntimeError('准备返回时聊天对象已变化，安全停止')
    back = [n for n in root.iter('node') if h.rid(n) == 'item_left_back'
            and n.attrib.get('clickable') == 'true']
    if len(back) != 1:
        raise RuntimeError('未能唯一确认私聊返回按钮，停止')
    h.adb_action('shell', 'input', 'tap', *h.center(back[0]))
    time.sleep(1.2)
    # 不盲目按系统返回键；发完招呼后有可能返回主聊天列表。
    greeting_list(h.read_page_from_adb())
    print('✅ 已确认返回「收到的新招呼」列表')



def return_to_main_if_empty():
    """Only leave greetings when the verified greeting list contains no rows."""
    root = h.read_page_from_adb()
    rows = greeting_list(root)
    if rows:
        print(f'ℹ️ 新招呼列表仍有 {len(rows)} 条；保留当前页面，不返回主消息列表')
        return False

    back_buttons = [
        n for n in root.iter('node')
        if h.rid(n) == 'ibtn_back'
        and n.attrib.get('clickable') == 'true'
        and n.attrib.get('enabled') == 'true'
        and h.center(n) is not None
    ]
    if len(back_buttons) != 1:
        raise RuntimeError('招呼列表为空，但无法唯一定位返回按钮；未点击')

    print('✅ 新招呼列表为空，尝试返回主消息列表')
    h.adb_action('shell', 'input', 'tap', *h.center(back_buttons[0]))
    time.sleep(1.7)
    result = h.read_page_from_adb()
    texts = [h.text(n) for n in result.iter('node')]
    has_chat_rows = any(h.rid(n) == 'list_itease_layout' for n in result.iter('node'))
    has_input = any(h.rid(n) == 'et_sendmessage' for n in result.iter('node'))
    still_greeting = '收到的新招呼' in texts
    if has_chat_rows and not has_input and not still_greeting:
        print('✅ 已确认返回 SOUL 主消息列表')
        return True
    raise RuntimeError('已点击一次返回，但未确认主消息列表；停止，不继续点击')


def main():
    parser = argparse.ArgumentParser(description='SOUL 新招呼逐个处理：默认只读，--live 才发送')
    parser.add_argument('--live', action='store_true', help='允许真实点击及发送')
    parser.add_argument('--return-empty', action='store_true', help='仅在新招呼列表为空时返回主消息页面；不发送')
    parser.add_argument('--max', type=int, default=1, help='本次最多处理几位新招呼；默认1')
    parser.add_argument('--xml', help='使用本地新招呼列表 XML 静态测试（只读）')
    args = parser.parse_args()
    if args.max < 1 or args.max > 20:
        parser.error('--max 必须为1到20')
    if args.live and args.xml:
        parser.error('--live 不允许与 --xml 联用')
    if args.return_empty and (args.live or args.xml):
        parser.error('--return-empty 不允许与 --live 或 --xml 同时使用')
    if not HANDLER.exists():
        raise RuntimeError(f'缺少发送模块：{HANDLER.name}')

    first_root = ET.parse(args.xml).getroot() if args.xml else h.read_page_from_adb()
    initial = greeting_list(first_root)
    print(f'✅ 新招呼列表识别：{len(initial)}条')
    if args.return_empty:
        return_to_main_if_empty()
        return
    for index, row in enumerate(initial, 1):
        flag = '已发送/待核实，跳过' if already_handled(row['name']) else '待处理'
        print(f"{index}. {row['name']} — {flag}")
    if not args.live:
        print('🔒 DRY-RUN：未点击、未调用AI、未发送')
        return

    print('⚠️ LIVE：将向未处理的新招呼真实发送消息')
    processed = set()
    successes = 0
    while successes < args.max:
        rows = greeting_list(h.read_page_from_adb())
        candidates = [row for row in rows if row['name'] not in processed and
                      not already_handled(row['name'])]
        if not candidates:
            if not rows:
                return_to_main_if_empty()
            else:
                print('✅ 当前屏幕没有未处理的新招呼，但列表尚有记录；保留当前页面')
            break
        row = candidates[0]
        name = row['name']
        print(f'▶ 进入：{name}')
        # 只点 XML 确认的行中央，不复用旧坐标；进入后必须核对标题和招呼标记。
        h.adb_action('shell', 'input', 'tap', *row['xy'])
        time.sleep(1.5)
        # V7.6：先判断是否为平台 AI 会话。即使没有普通“新招呼”标记，
        # 只要顶部确认为目标且存在系统 AI 免责声明，也直接跳过返回。
        try:
            current = h.read_page_from_adb()
            actual = h.chat_title(current)
            if actual != name:
                raise RuntimeError(f'点击后目标不一致：期望{name}，实际{actual}；不继续点击')
            if is_system_ai(current):
                log_skip('greeting', name, current, BASE)
                processed.add(name)
                back_to_greetings(name)
                continue
            verify_detail(name)
        except RuntimeError as exc:
            # 确实进入预期用户私聊，却没有“新招呼”控件：跳过而不是中断。
            try:
                check = h.read_page_from_adb()
                if h.chat_title(check) != name:
                    raise RuntimeError('当前对象不匹配，无法安全返回')
                print(f'⏭️ {name} 详情识别失败，放弃发送：{exc}')
                processed.add(name)
                back_to_greetings(name)
                continue
            except RuntimeError as back_exc:
                raise RuntimeError(f'无法核对及恢复新招呼列表：{back_exc}') from exc
        print(f'✅ 已核对目标：{name}；启动现有单次发送模块')
        result = subprocess.run([sys.executable, '-u', str(HANDLER), '--send', '--username', name],
                                cwd=BASE, check=False)
        if result.returncode != 0:
            print(f'⏭️ {name} 发送失败({result.returncode})，不重发；尝试回新招呼列表')
            processed.add(name)
            back_to_greetings(name)  # 只有核对仍是目标聊天后才返回
            continue
        pending, sent = record_files(name)
        if not sent.exists() or pending.exists():
            raise RuntimeError('未找到唯一成功发送记录；保留现场，停止')
        successes += 1
        processed.add(name)
        print(f'✅ 本轮已确认处理 {successes}/{args.max} 位')
        back_to_greetings(name)
    # Re-check after reaching --max as well: sending the final greeting may empty the list.
    if successes >= args.max:
        return_to_main_if_empty()
    print(f'✅ 批处理结束；本次确认发送 {successes} 位')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        print(f'❌ 安全停止：{exc}', file=sys.stderr)
        sys.exit(40)
