#!/usr/bin/env python3
"""SOUL V6 奇遇铃「稍后再聊」批处理试验版。

默认只读；--live 明确授权后才点击并调用现有单人安全发送模块。
支持 --all 处理列表内所有可访问且没有发送记录的对象，逐页查找、逐人返回，最后返回主聊天页。
需要与 soul_v6_bell_handler.py、soul_v6_greeting_handler_v2.py 放在同一目录。
"""
import argparse
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import soul_v6_bell_handler as bell
import soul_v6_greeting_handler_v2 as h
from soul_v7_system_ai import is_system_ai, log_skip

BASE = Path(__file__).resolve().parent
HANDLER = BASE / 'soul_v6_bell_handler.py'
TITLE = '奇遇铃-稍后再聊'


class ExistingConversation(Exception):
    """Correct chat has visible history; do not send an introductory message."""



def is_bell_list(root):
    """区分列表页与主消息页的同名入口；必须有列表页返回按钮。"""
    titles = [n for n in root.iter('node') if h.text(n) == TITLE]
    back = [n for n in bell.controls(root, 'ibtn_back')
            if n.attrib.get('clickable') == 'true']
    return (len(titles) == 1 and len(back) == 1
            and len(bell.controls(root, 'recycler_view')) == 1
            and not bell.controls(root, 'main_tab_msg_text')
            and not bell.controls(root, 'et_sendmessage'))


def rows_on_screen(root):
    if not is_bell_list(root):
        raise RuntimeError('当前不是已验证的「奇遇铃-稍后再聊」列表；安全停止')
    list_node = bell.controls(root, 'recycler_view')[0]
    rows = []
    for node in list_node.iter('node'):
        if h.rid(node) != 'content_layout':
            continue
        names = [h.text(x) for x in node.iter('node')
                 if h.rid(x) == 'tv_name' and h.text(x)]
        # 页面可能有装饰性 content_layout，不属于人物行。
        if not names:
            if node.attrib.get('clickable') == 'true':
                raise RuntimeError('存在可点击但无昵称的行，安全停止')
            continue
        if len(names) != 1:
            raise RuntimeError('列表存在昵称不唯一的用户行，安全停止')
        if node.attrib.get('clickable') != 'true' or node.attrib.get('enabled') != 'true':
            raise RuntimeError(f'{names[0]} 所在列表行不可点击，停止')
        point = h.center(node)
        if point is None:
            raise RuntimeError(f'{names[0]} 所在行坐标无效，停止')
        rows.append({'name': names[0], 'xy': point})
    if len(set(row['name'] for row in rows)) != len(rows):
        raise RuntimeError('当前列表有重复昵称，无法安全区分，停止')
    return rows


def handled(name):
    pending, sent = bell.state_paths(name)
    return pending.exists() or sent.exists()


def inspect_detail(expected_name):
    root = h.read_page_from_adb()
    detail = bell.bell_detail(root)
    if detail['name'] != expected_name or h.chat_title(root) != expected_name:
        raise RuntimeError(f'聊天昵称不一致：期望 {expected_name}，实际 {detail["name"]}')
    # AI 单人模块也会再次检查已有聊天消息；这里先做前置保护。
    if bell.controls(root, 'content_text') or bell.controls(root, 'voice_bubble'):
        raise ExistingConversation('当前聊天已存在可见消息，跳过首次开场白')
    return root


def return_to_bell_list(expected_name):
    root = h.read_page_from_adb()
    if h.chat_title(root) != expected_name:
        raise RuntimeError('返回前聊天对象不匹配，停止')
    buttons = [n for n in bell.controls(root, 'item_left_back')
               if n.attrib.get('clickable') == 'true'
               and n.attrib.get('enabled') == 'true']
    if len(buttons) != 1:
        raise RuntimeError('私聊返回按钮不唯一，停止')
    h.adb_action('shell', 'input', 'tap', *h.center(buttons[0]))
    time.sleep(1.3)
    new_root = h.read_page_from_adb()
    if not is_bell_list(new_root):
        raise RuntimeError('已返回一次，但未能确认回到「奇遇铃-稍后再聊」列表；停止')
    print('✅ 已确认返回奇遇铃列表')



def is_main_message_page(root):
    """用已观察到的主消息 Tab + 消息行判断；同名入口出现不影响判断。"""
    if bell.controls(root, 'et_sendmessage'):
        return False
    tabs = [n for n in bell.controls(root, 'main_tab_msg_text')
            if h.text(n) == '聊天']
    rows = bell.controls(root, 'list_itease_layout')
    return len(tabs) == 1 and bool(rows)


def return_to_main_message_page():
    root = h.read_page_from_adb()
    if not is_bell_list(root):
        raise RuntimeError('返回主消息页前已不在奇遇铃列表，拒绝点击')
    buttons = [n for n in bell.controls(root, 'ibtn_back')
               if n.attrib.get('clickable') == 'true'
               and n.attrib.get('enabled') == 'true']
    if len(buttons) != 1 or h.center(buttons[0]) is None:
        raise RuntimeError('奇遇铃列表返回按钮不唯一或坐标无效')
    print('↩️ 点击一次奇遇铃列表返回按钮')
    h.adb_action('shell', 'input', 'tap', *h.center(buttons[0]))
    time.sleep(1.5)
    root = h.read_page_from_adb()
    if not is_main_message_page(root):
        raise RuntimeError('已点击返回，但未确认 SOUL 主消息页面；停止，不再点击')
    print('✅ 已确认回到 SOUL 主消息页面')

def main():
    parser = argparse.ArgumentParser(description='奇遇铃稍后再聊批处理：默认只读；--live 实际发送')
    parser.add_argument('--live', action='store_true', help='真实点击和发送')
    parser.add_argument('--max', type=int, default=1, help='本次最多处理人数，默认1')
    parser.add_argument('--all', action='store_true', help='处理奇遇铃列表内所有未处理用户，逐页扫描后返回')
    parser.add_argument('--xml', help='离线 XML 静态检测；不能与 --live 同用')
    parser.add_argument('--return-only', action='store_true', help='仅从已确认的奇遇铃列表返回主消息页，不发送')
    parser.add_argument('--return-main', action='store_true', help='LIVE处理结束后返回主消息页（包括达到 --max 时）')
    args = parser.parse_args()
    if not args.all and not 1 <= args.max <= 20:
        parser.error('--max 只能为1到20')
    if args.all and not args.live:
        parser.error('--all 必须同时指定 --live')
    if args.all and not args.return_main:
        parser.error('--all 必须同时指定 --return-main，确保结束时返回主消息页')
    if args.live and args.xml:
        parser.error('禁止同时使用 --live 与 --xml')
    if args.return_only and (args.live or args.xml):
        parser.error('--return-only 不能与 --live 或 --xml 同用')
    if not HANDLER.is_file():
        raise RuntimeError(f'找不到单人发送模块：{HANDLER.name}')

    if args.return_only:
        return_to_main_message_page()
        return

    root = ET.parse(args.xml).getroot() if args.xml else h.read_page_from_adb()
    rows = rows_on_screen(root)
    print(f'✅ 奇遇铃列表：当前可见 {len(rows)} 位')
    for i, row in enumerate(rows, 1):
        print(f'{i}. {row["name"]} — {"已发送/待核实（跳过）" if handled(row["name"]) else "待处理"}')
    if not args.live:
        print('🔒 只读检查完成：未点击、未调用 AI、未发送')
        return

    print('⚠️ LIVE模式：将对当前可见、无发送记录的用户执行真实开场白发送')
    tried = set()
    successes = 0
    skipped_draft_failures = 0
    skipped_existing_conversations = 0
    unchanged = 0
    previous_names = None
    swipes = 0
    while args.all or successes < args.max:
        rows = rows_on_screen(h.read_page_from_adb())
        choices = [r for r in rows if r['name'] not in tried and not handled(r['name'])]
        if not choices:
            if not args.all:
                print('✅ 当前可见列表已无待处理对象，结束')
                break
            names = tuple(row['name'] for row in rows)
            # 只有列表保持不变两次，才能认定到底；避免单次滑动无效。
            if names == previous_names:
                unchanged += 1
            else:
                unchanged = 0
            if unchanged >= 2:
                print('✅ 列表连续两次无变化，全部可访问用户已检查完毕')
                break
            if swipes >= 50:
                raise RuntimeError('列表滑动已达到50次仍未确认到底；安全停止')
            previous_names = names
            print(f'↕️ 本屏没有待处理对象，滑动检查下一屏（{swipes + 1}）')
            h.adb_action('shell', 'input', 'swipe', 540, 1850, 540, 650, 450)
            swipes += 1
            time.sleep(.7)
            if not is_bell_list(h.read_page_from_adb()):
                raise RuntimeError('翻页后不在奇遇铃列表；安全停止')
            continue
        unchanged = 0
        previous_names = None
        row = choices[0]
        name = row['name']
        tried.add(name)
        print('▶ 进入：', name)
        h.adb_action('shell', 'input', 'tap', *row['xy'])
        time.sleep(1.5)
        try:
            check = h.read_page_from_adb()
            if is_system_ai(check):
                if h.chat_title(check) != name:
                    raise RuntimeError('系统AI页昵称不匹配，未执行返回')
                log_skip('bell', name, check, BASE)
                return_to_bell_list(name)
                continue
            inspect_detail(name)
        except ExistingConversation:
            # Already chatting: no AI request, no send, no changes to message-history state.
            # The verified title check still runs before this branch can be entered.
            print(f'⏭️ {name} 已有可见聊天消息：停留片刻后返回奇遇铃列表，不发送首次开场白', flush=True)
            time.sleep(1.2)
            return_to_bell_list(name)
            skipped_existing_conversations += 1
            continue
        print(f'✅ 目标昵称核对通过，调用单人发送模块：{name}')
        # AI 草稿阶段无输出是可恢复故障；发送阶段及未知故障仍必须停止。
        # 捕获子进程输出并原样显示，避免误把发送异常当成可重试故障。
        draft_failed = False
        for attempt in (1, 2):
            result = subprocess.run(
                [sys.executable, '-u', str(HANDLER), '--send', '--username', name],
                cwd=BASE, check=False, timeout=180,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            child_output = result.stdout or ''
            print(child_output, end='' if child_output.endswith('\n') else '\n', flush=True)
            if result.returncode == 0:
                break

            pending, sent = bell.state_paths(name)
            # 只有模型明确在生成草稿之前失败，且界面无任何聊天消息、
            # 本地既没有 pending 也没有 sent 记录时，才允许重试/跳过。
            known_draft_failure = (
                '模型未输出可见草稿，响应状态：incomplete' in child_output
                and '🧠 本次开场白：' not in child_output
                and '已验证发送给' not in child_output
                and not pending.exists() and not sent.exists()
            )
            if not known_draft_failure:
                raise RuntimeError(
                    f'{name} 单人发送返回码 {result.returncode}；故障阶段不明确，保留现场，不重试')
            # 再次确认仍停留在该私聊且无消息；若有草稿、消息或页面异常，停止。
            checked_root = inspect_detail(name)
            input_nodes = bell.controls(checked_root, 'et_sendmessage')
            if len(input_nodes) != 1 or h.text(input_nodes[0]).strip():
                raise RuntimeError(f'{name} 输入框有内容，禁止自动重试或跳过')
            if attempt == 1:
                print(f'⚠️ {name} 模型草稿 incomplete，确认未发送，重试一次', flush=True)
                time.sleep(1.5)
            else:
                print(f'⏭️ {name} 模型连续两次 incomplete，确认未发送；本轮跳过继续', flush=True)
                draft_failed = True
                skipped_draft_failures += 1

        if draft_failed:
            return_to_bell_list(name)
            continue
        pending, sent = bell.state_paths(name)
        if pending.exists() or not sent.exists():
            raise RuntimeError(f'{name} 发送记录未唯一确认；停止，不重发')
        successes += 1
        print(f'✅ 已确认完成 {successes} 位' + ('' if args.all else f'/{args.max} 位'))
        return_to_bell_list(name)
    if args.return_main:
        return_to_main_message_page()
        print(f'✅ 本轮确认发送 {successes} 位；模型失败跳过 {skipped_draft_failures} 位；已有聊天跳过 {skipped_existing_conversations} 位；已返回主消息页')
    else:
        print(f'✅ 本轮确认发送 {successes} 位；当前在奇遇铃列表')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, ET.ParseError, OSError, subprocess.TimeoutExpired) as exc:
        print(f'❌ 安全停止：{exc}', file=sys.stderr)
        sys.exit(40)
