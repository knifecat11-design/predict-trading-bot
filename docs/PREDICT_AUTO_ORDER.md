# Predict.fun 自动挂单脚本 - 浏览器 F12 版本

本文档提供两个 JavaScript 脚本，用于在 Predict.fun 网站上实现自动挂单功能。

## 📋 目录

1. [页面分析工具](#页面分析工具) - 先运行此脚本分析页面结构
2. [自动挂单脚本](#自动挂单脚本) - 根据分析结果调整后使用

---

## 🔍 页面分析工具

### 使用方法

1. 打开 Predict.fun 市场：https://predict.fun/markets/
2. 按 **F12** 打开开发者工具
3. 切换到 **Console** 标签
4. 复制下面的脚本并运行
5. 查看输出的页面结构信息

### 脚本代码

```javascript
/**
 * Predict.fun 页面分析工具
 *
 * 使用方法：
 * 1. 打开 Predict.fun 市场：https://predict.fun/markets/
 * 2. 按 F12 打开开发者工具
 * 3. 切换到 Console 标签
 * 4. 复制粘贴本脚本并运行
 * 5. 查看输出的页面结构和 API 信息
 */

(function() {
    console.log(`🔍 开始分析 Predict.fun 页面...`);

    // 1. 分析页面结构
    console.log(`\n📊 === 页面结构分析 ===\n`);

    // 查找价格相关元素
    console.log(`💰 价格元素：`);
    const priceElements = document.querySelectorAll('*');
    const priceMatches = [];
    priceElements.forEach(el => {
        const text = el.textContent?.trim();
        if (text && /^\d+(\.\d+)?%/.test(text)) {
            priceMatches.push({
                tag: el.tagName,
                class: el.className,
                text: text,
                id: el.id,
                parent: el.parentElement?.className
            });
        }
    });

    // 显示前 10 个价格元素
    priceMatches.slice(0, 10).forEach((match, i) => {
        console.log(`${i + 1}. <${match.tag} class="${match.class}"> ${match.text}`);
    });

    // 2. 查找按钮
    console.log(`\n🔘 按钮元素：`);
    const buttons = document.querySelectorAll('button, [role="button"]');
    const buyButtons = [];
    const sellButtons = [];
    const orderButtons = [];

    buttons.forEach(btn => {
        const text = btn.textContent?.trim().toLowerCase();
        const classList = Array.from(btn.classList || []).join(' ');

        if (text.includes('buy') || text.includes('yes') || classList.includes('buy')) {
            buyButtons.push({ text: btn.textContent?.trim(), class: classList });
        } else if (text.includes('sell') || text.includes('no') || classList.includes('sell')) {
            sellButtons.push({ text: btn.textContent?.trim(), class: classList });
        } else if (text.includes('order') || classList.includes('order')) {
            orderButtons.push({ text: btn.textContent?.trim(), class: classList });
        }
    });

    console.log(`买入按钮: ${buyButtons.length}`);
    buyButtons.slice(0, 3).forEach((btn, i) => console.log(`  ${i + 1}. "${btn.text}"`));

    console.log(`卖出按钮: ${sellButtons.length}`);
    sellButtons.slice(0, 3).forEach((btn, i) => console.log(`  ${i + 1}. "${btn.text}"`));

    console.log(`订单按钮: ${orderButtons.length}`);
    orderButtons.slice(0, 3).forEach((btn, i) => console.log(`  ${i + 1}. "${btn.text}"`));

    // 3. 分析网络请求（需要先开启网络监听）
    console.log(`\n🌐 === API 分析 ===\n`);
    console.log(`💡 提示：切换到 Network 标签，刷新页面，然后执行交易操作`);
    console.log(`💡 查找包含 "order", "trade", "market" 的请求`);

    // 4. 查找 React/Vue 状态
    console.log(`\n⚛️  === 应用状态分析 ===\n`);

    // 尝试获取 React 内部状态
    const rootElement = document.querySelector('#root, #__next, [data-reactroot]');
    if (rootElement) {
        console.log(`✅ 找到根元素:`, rootElement);

        // 尝试获取 React fiber
        const fiberKey = Object.keys(rootElement).find(key => key.startsWith('__reactFiber'));
        if (fiberKey) {
            console.log(`✅ 找到 React Fiber: ${fiberKey}`);
            console.log(`💡 可以访问: rootElement['${fiberKey}']`);
        }
    } else {
        console.log(`⚠️ 未找到 React 根元素`);
    }

    // 查找全局状态对象
    const possibleStores = ['__state__', '__store__', 'store', 'state'];
    possibleStores.forEach(key => {
        if (window[key]) {
            console.log(`✅ 找到全局状态: window.${key}`);
        }
    });

    // 5. 查找输入框
    console.log(`\n📝 === 输入框分析 ===\n`);
    const inputs = document.querySelectorAll('input[type="number"], input[type="text"]');
    console.log(`找到 ${inputs.length} 个输入框：`);
    inputs.slice(0, 5).forEach((input, i) => {
        console.log(`${i + 1}. ${input.placeholder || input.name || '无名称'} - ${input.type}`);
    });

    // 6. 创建监控函数
    window.monitorPredict = () => {
        console.log(`🎯 开始监控 Predict.fun 页面...`);
        console.log(`💡 按 Ctrl+C 停止监控`);

        let count = 0;
        const interval = setInterval(() => {
            count++;
            const priceElement = document.querySelector('[class*="price"], [class*="Price"]');
            if (priceElement) {
                const text = priceElement.textContent?.trim();
                console.log(`[${new Date().toLocaleTimeString()}] 价格: ${text}`);
            }
            if (count > 100) clearInterval(interval);
        }, 2000);

        console.log(`✅ 监控已启动，每 2 秒更新一次`);
        return interval;
    };

    console.log(`\n✅ 分析完成！\n`);
    console.log(`💡 可用命令：`);
    console.log(`   - monitorPredict() - 开始监控价格变化`);

})();
```

### 预期输出

脚本会输出：
- 💰 价格元素的 class 名称
- 🔘 买入/卖出按钮的选择器
- ⚛️ React/Vue 应用结构
- 📝 输入框信息

---

## 🤖 自动挂单脚本

### 使用方法

**重要**: 首先需要运行上面的分析工具，获取正确的页面元素选择器，然后调整下面的脚本。

### 基础版本

```javascript
/**
 * Predict.fun 自动挂单脚本 - 基础版本
 *
 * ⚠️ 注意：这是一个模板脚本，需要根据实际页面结构调整
 */

class PredictAutoTrader {
    constructor(config = {}) {
        this.config = {
            // 挂单策略
            spreadPercent: config.spreadPercent || 6,  // 挂单范围 ±6%
            maxOrders: config.maxOrders || 3,          // 每侧最大挂单数
            orderSize: config.orderSize || 10,         // 每单大小

            // 风险控制
            cancelThreshold: config.cancelThreshold || 0.5,  // 撤单阈值
            maxExposure: config.maxExposure || 100,    // 最大风险敞口

            // 运行模式
            dryRun: config.dryRun !== undefined ? config.dryRun : true,  // 默认模拟
            refreshInterval: config.refreshInterval || 5000,  // 刷新间隔

            // TODO: 根据分析工具的结果，填入正确的选择器
            selectors: {
                priceInput: config.priceInput || 'input[type="number"]',
                buyButton: config.buyButton || 'button:has-text("Buy Yes")',
                sellButton: config.sellButton || 'button:has-text("Sell No")',
                priceDisplay: config.priceDisplay || '[class*="price"]'
            }
        };

        this.orders = [];
        this.isRunning = false;
        this.intervalId = null;

        console.log(`🎲 Predict.fun 自动挂单脚本已加载`);
        console.log(`📊 当前模式: ${this.config.dryRun ? '🧪 模拟运行' : '💰 实盘运行'}`);
    }

    /**
     * 获取当前价格
     * TODO: 根据实际页面调整选择器
     */
    getCurrentPrice() {
        const priceElement = document.querySelector(this.config.selectors.priceDisplay);
        if (priceElement) {
            const text = priceElement.textContent?.trim();
            const match = text.match(/(\d+\.?\d*)%/);
            if (match) {
                return parseFloat(match[1]) / 100;
            }
        }
        return null;
    }

    /**
     * 计算挂单价格
     */
    calculateOrderPrices(currentPrice) {
        if (!currentPrice) return null;

        const spread = this.config.spreadPercent / 100;
        return {
            buy: Math.max(0.01, currentPrice - spread),      // 买单价
            sell: Math.min(0.99, currentPrice + spread)      // 卖单价
        };
    }

    /**
     * 模拟下单
     * TODO: 实现真实的下单逻辑
     */
    async placeOrder(side, price, size) {
        const order = {
            id: `order_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
            side,
            price,
            size,
            timestamp: Date.now(),
            status: 'open'
        };

        if (this.config.dryRun) {
            console.log(`🧪 [模拟] ${side.toUpperCase()} ${size} @ ${(price * 100).toFixed(1)}%`);
            return order;
        } else {
            // TODO: 实现真实的下单逻辑
            // 1. 找到价格输入框并输入价格
            // 2. 找到数量输入框并输入数量
            // 3. 点击买入/卖出按钮

            console.log(`💰 [实盘] ${side.toUpperCase()} ${size} @ ${(price * 100).toFixed(1)}%`);
            return order;
        }
    }

    /**
     * 主循环
     */
    async tick() {
        const currentPrice = this.getCurrentPrice();

        if (!currentPrice) {
            console.warn(`⚠️ 无法获取当前价格`);
            return;
        }

        console.log(`📊 当前价格: ${(currentPrice * 100).toFixed(1)}%`);

        // 管理挂单
        await this.manageOrders(currentPrice);

        // 显示当前挂单
        console.log(`📋 当前挂单数: ${this.orders.filter(o => o.status === 'open').length}`);
    }

    /**
     * 管理挂单
     */
    async manageOrders(currentPrice) {
        const prices = this.calculateOrderPrices(currentPrice);
        if (!prices) return;

        // TODO: 实现挂单管理逻辑
        // 1. 检查现有订单是否需要撤单
        // 2. 检查是否需要新挂单
    }

    /**
     * 开始运行
     */
    start() {
        if (this.isRunning) {
            console.warn(`⚠️ 脚本已在运行中`);
            return;
        }

        this.isRunning = true;
        console.log(`🚀 开始自动挂单...`);
        console.log(`🔄 刷新间隔: ${this.config.refreshInterval / 1000} 秒`);

        this.tick();
        this.intervalId = setInterval(() => this.tick(), this.config.refreshInterval);
    }

    /**
     * 停止运行
     */
    stop() {
        if (!this.isRunning) return;

        this.isRunning = false;
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }

        console.log(`🛑 自动挂单已停止`);
    }

    /**
     * 切换实盘/模拟模式
     */
    setDryRun(dryRun) {
        this.config.dryRun = dryRun;
        console.log(`🔄 切换模式: ${dryRun ? '🧪 模拟运行' : '💰 实盘运行'}`);
    }

    /**
     * 获取状态
     */
    getStatus() {
        return {
            isRunning: this.isRunning,
            mode: this.config.dryRun ? '模拟' : '实盘',
            orders: this.orders.filter(o => o.status === 'open'),
            config: this.config
        };
    }
}

// 使用示例
console.log(`
╔════════════════════════════════════════════════════════════╗
║     🎲 Predict.fun 自动挂单脚本 v1.0                       ║
║     浏览器 F12 控制台版本                                   ║
╚════════════════════════════════════════════════════════════╝

📖 使用方法：

1. 创建实例（默认模拟模式）：
   const trader = new PredictAutoTrader();

2. 自定义配置：
   const trader = new PredictAutoTrader({
       spreadPercent: 8,      // 挂单范围 ±8%
       maxOrders: 5,          // 每侧最多 5 个单
       orderSize: 20,         // 每单 20 个
       dryRun: true           // 模拟模式
   });

3. 开始运行：
   trader.start();

4. 停止运行：
   trader.stop();

5. 查看状态：
   trader.getStatus();

6. 切换到实盘（谨慎！）：
   trader.setDryRun(false);

⚠️  重要提示：
- 默认是模拟模式，不会实际下单
- 切换到实盘前请先测试
- 建议从小额开始
- 确保页面保持打开状态
`);

// 导出类
window.PredictAutoTrader = PredictAutoTrader;
```

---

## 📝 使用步骤

### 第一步：运行分析工具

1. 打开 https://predict.fun/markets/
2. F12 → Console
3. 粘贴**页面分析工具**脚本并运行
4. 记录输出的选择器信息

### 第二步：调整自动挂单脚本

根据第一步的分析结果，调整 `PredictAutoTrader` 类中的 `selectors` 配置。

### 第三步：测试运行（模拟模式）

```javascript
const trader = new PredictAutoTrader({
    dryRun: true,  // 模拟模式
    spreadPercent: 6,
    maxOrders: 3,
    orderSize: 10
});

trader.start();
```

### 第四步：实盘运行（谨慎！）

```javascript
// 确认无误后切换到实盘
trader.setDryRun(false);
```

---

## ⚠️ 重要注意事项

### 风险提示

- 🧪 **务必先在模拟模式测试**
- 💰 **从小额开始**
- 📊 **实时监控运行状态**
- 🔄 **定期检查页面是否正常**
- 🚫 **不要长时间无人值守**

### 技术限制

- ⚠️ 页面结构可能随时变化
- ⚠️ 需要保持浏览器标签页打开
- ⚠️ 可能触发网站风控
- ⚠️ 频繁操作可能导致 IP 被限制

### 建议

- ✅ 使用 Railway 持续监控（更稳定）
- ✅ 浏览器脚本用于辅助手动操作
- ✅ 定期检查页面元素是否有更新

---

## 🔧 进阶功能

### API 直接调用（如果找到 API 端点）

如果通过分析工具找到了 Predict.fun 的 API 端点，可以直接使用：

```javascript
// 示例（需要根据实际 API 调整）
async function placeOrderViaAPI(side, price, size) {
    const response = await fetch('https://api.predict.fun/v1/orders', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer YOUR_API_TOKEN'
        },
        body: JSON.stringify({
            side: side,
            price: price,
            size: size
        })
    });

    return response.json();
}
```

---

## 📚 相关文档

- [API申请指南.md](API申请指南.md)
- [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

---

**创建日期**: 2026-02-05
**版本**: v1.0

⚠️ **免责声明**: 本脚本仅供学习和研究使用。使用本脚本进行实际交易的风险由使用者自行承担。
