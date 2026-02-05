#!/usr/bin/env python3
"""
本地监控脚本 - 查看套利监控系统的运行状态
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta


def tail_log(log_file: str = 'logs/trading_arbitrage.log', lines: int = 50):
    """显示日志文件最后 N 行"""
    if not os.path.exists(log_file):
        print(f"日志文件不存在: {log_file}")
        return

    with open(log_file, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
        recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        print("=" * 80)
        print(f"日志文件: {log_file}")
        print(f"显示最后 {len(recent_lines)} 行")
        print("=" * 80)
        for line in recent_lines:
            print(line.rstrip())
        print("=" * 80)


def show_stats():
    """显示统计信息"""
    log_file = 'logs/trading_arbitrage.log'

    if not os.path.exists(log_file):
        print(f"日志文件不存在: {log_file}")
        return

    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 统计信息
    total_scans = 0
    opportunities_found = 0
    errors = 0
    telegram_success = 0

    for line in lines:
        if '总扫描' in line:
            try:
                total_scans = int(line.split('总扫描')[1].split('次')[0].strip())
            except:
                pass
        elif '发现机会' in line:
            try:
                opportunities_found = int(line.split('发现机会')[1].split('次')[0].strip())
            except:
                pass
        elif '[ERROR]' in line or '错误' in line:
            errors += 1
        elif 'Telegram 推送成功' in line:
            telegram_success += 1

    # 获取最后修改时间
    mtime = os.path.getmtime(log_file)
    last_modified = datetime.fromtimestamp(mtime)
    time_since_update = datetime.now() - last_modified

    print("=" * 80)
    print("套利监控系统统计")
    print("=" * 80)
    print(f"最后更新: {last_modified.strftime('%Y-%m-%d %H:%M:%S')} ({int(time_since_update.total_seconds())}秒前)")
    print(f"总扫描次数: {total_scans}")
    print(f"发现机会数: {opportunities_found}")
    print(f"Telegram 推送成功: {telegram_success} 次")
    print(f"错误数: {errors}")
    print(f"日志总行数: {len(lines)}")
    print("=" * 80)


def show_recent_errors(log_file: str = 'logs/trading_arbitrage.log', lines: int = 20):
    """显示最近的错误"""
    if not os.path.exists(log_file):
        print(f"日志文件不存在: {log_file}")
        return

    with open(log_file, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()

    # 找出所有错误行
    error_lines = []
    for i, line in enumerate(all_lines):
        if '[ERROR]' in line or 'Exception' in line or '错误' in line:
            # 获取上下文（前后各2行）
            start = max(0, i - 2)
            end = min(len(all_lines), i + 3)
            context = all_lines[start:end]
            error_lines.append((i + 1, context))

    if not error_lines:
        print("没有发现错误 ✓")
        return

    print("=" * 80)
    print(f"最近的错误 (显示最后 {min(lines, len(error_lines))} 个)")
    print("=" * 80)

    for line_num, context in error_lines[-lines:]:
        print(f"\n[行 {line_num}]")
        for ctx_line in context:
            print(f"  {ctx_line.rstrip()}")

    print("=" * 80)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='套利监控系统监控工具')
    parser.add_argument('command', nargs='?', default='stats',
                       choices=['stats', 'log', 'errors'],
                       help='命令: stats(统计), log(日志), errors(错误)')
    parser.add_argument('--lines', type=int, default=50,
                       help='显示的行数 (默认: 50)')

    args = parser.parse_args()

    if args.command == 'stats':
        show_stats()
    elif args.command == 'log':
        tail_log(lines=args.lines)
    elif args.command == 'errors':
        show_recent_errors(lines=args.lines)


if __name__ == '__main__':
    main()
