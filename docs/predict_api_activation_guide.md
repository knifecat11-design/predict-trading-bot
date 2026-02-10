"""
Predict.fun API 激活诊断工具
帮助排查 API Key 为何未激活
"""

import sys
import io

# UTF-8 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

print("=" * 80)
print("  Predict.fun API 激活诊断工具")
print("=" * 80)
print()

print("📋 问题排查清单：")
print()

print("【1】API Key 信息")
print("  您的 API Key:", "1b0c25d4-8ca6-4aa8-8910-cd72b311e4f6"[:20] + "...")
print()

print("【2】钱包地址检查")
print("  ❓ 请回答以下问题：")
print()
print("  1. 申请 API Key 时，您连接的钱包地址是什么？")
print("     （查看 Predict.fun 申请页面或 Discord 申请记录）")
print()
print("  2. 下单时使用的钱包地址是什么？")
print("     （在 MetaMask/Trust Wallet 中查看当前地址）")
print()
print("  3. 这两个地址是否完全一致？（包括大小写）")
print()

print("【3】交易信息")
print("  ❓ 请检查：")
print("  1. 交易是否成功确认？")
print("  2. 交易是在哪个市场进行的？")
print("  3. 交易金额是多少？")
print("  4. 交易时间是什么时候？")
print()

print("【4】API Key 生成方式")
print("  ❓ 您是如何获取 API Key 的？")
print("  - A) 在网站填写表格直接获得")
print("  - B) 通过 Discord 机器人生成")
print("  - C) 通过钱包连接后生成")
print("  - D) 其他方式")
print()

print("【5】可能的问题】")
print()
print("  🔴 地址不匹配")
print("     → API Key 绑定到地址 A，但您用地址 B 下单")
print("     → 解决：使用申请 API Key 时的同一地址重新下单")
print()
print("  🔴 交易类型错误")
print("     → 可能需要在特定市场或特定金额")
print("     → 解决：联系 Discord 确认正确的激活方式")
print()
print("  🔴 激活延迟")
print("     → 下单后需要等待系统后台处理")
print("     → 解决：等待 15-30 分钟后重试")
print()
print("  🔴 最小金额要求")
print("     → 可能需要超过一定金额的交易")
print("     → 解决：确认最低激活金额要求")
print()

print("【6】建议操作】")
print()
print("  1. 截图发送给 Discord:")
print("     - API Key (前 8 位即可)")
print("     - 申请 API Key 时的钱包地址")
print("     - 下单的交易哈希 (Transaction Hash)")
print("     - 交易金额和市场信息")
print()
print("  2. Discord 联系渠道:")
print("     - 服务器: https://discord.gg/predictdotfun")
print("     - 打开 support ticket")
print("     - 说明：\"API Key 返回 401，已按照指示下单激活\"")
print()

print("  3. 测试方法:")
print("     ↓ 运行诊断脚本")
print("     ↓ 提供 API Key 和钱包地址")
print("     ↓ 等待 Discord 响应")
print("     ↓ 根据反馈调整")
print()

print("=" * 80)
print()

print("📝 需要提供给 Discord 的信息:")
print("-" * 40)
print("1. API Key: 1b0c25d4-8ca6-4aa8-8910-cd72b311e4f6")
print("2. 申请时钱包地址: 0x... (从申请记录中查找)")
print("3. 下单钱包地址: 0x... (当前钱包地址)")
print("4. 交易哈希: 0x... (从区块链浏览器中查找)")
print("5. 错误信息: 401 Unauthorized")
print("6. 请求: 帮助激活 API Key")
print()

print("🔗 查找交易哈希:")
print("   1. 打开 MetaMask")
print("   2. 查看 Activity（活动）")
print("   3. 找到向 Predict.fun 发送的交易")
print("   4. 点击查看详情，复制 Transaction Hash")
print()

print("=" * 80)
