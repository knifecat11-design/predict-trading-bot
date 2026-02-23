# Predict.fun 自动挂单脚本 - 浏览器 F12 版本

本文档提供两个 JavaScript 脚本，用于在 Predict.fun 网站上实现自动挂单功能；并与本仓库的后端监控系统互补使用。

---

## 📌 与项目结构的关系

本仓库 [predict-trading-bot](https://github.com/knifecat11-design/predict-trading-bot) 主要包含：

| 部分 | 说明 |
|------|------|
| **arbitrage_main.py** | 套利监控主程序，通过 API 监控 Polymarket ↔ Predict.fun 价差并推送 Telegram |
| **src/api_client.py** | Predict.fun API 客户端（需 API Key），用于获取市场与订单簿数据 |
| **config.yaml** | 策略参数：`spread_percent`、`cancel_threshold`、`max_orders_per_side` 等 |

本文档中的**浏览器脚本**不依赖后端服务，在**已打开的 Predict.fun 市场页面**内运行，用于：

- **页面分析工具**：分析当前页面的 DOM 结构，得到价格、按钮、输入框的选择器，供自动挂单脚本使用。
- **自动挂单脚本**：根据当前价格与配置的价差，在浏览器端模拟或执行「挂买单 / 挂卖单」逻辑（实盘需根据分析结果自行对接 DOM 或 API）。

建议先在后端或本地用 `config.yaml` 理解 `spread_percent`、`cancel_threshold` 等含义，再在浏览器脚本中使用一致参数。

---

## 📋 目录

1. [页面分析工具](#-页面分析工具) - 先运行此脚本分析页面结构
2. [自动挂单脚本](#-自动挂单脚本) - 根据分析结果调整选择器后使用
3. [使用步骤](#-使用步骤)
4. [实盘下单的 DOM 操作说明](#-实盘下单的-dom-操作说明)
5. [重要注意事项](#-重要注意事项)

---

## 🔍 页面分析工具

### 使用方法

1. 打开 Predict.fun 市场：https://predict.fun/markets/
2. 按 **F12** 打开开发者工具
3. 切换到 **Console** 标签
4. 复制下面的脚本并运行
5. 查看输出的页面结构信息，并记录「价格元素、买入/卖出按钮、输入框」的选择器或 class 名称

### 脚本代码（含详细注释）

```javascript
/**
 * Predict.fun 页面分析工具
 *
 * 用途：在未公开 DOM 规范的情况下，自动探测当前页面中与「价格、买卖按钮、输入框」相关的元素，
 *       便于后续自动挂单脚本使用正确的选择器。
 *
 * 使用步骤：
 * 1. 打开 Predict.fun 市场：https://predict.fun/markets/
 * 2. 按 F12 打开开发者工具 → Console
 * 3. 粘贴本脚本并执行
 * 4. 根据输出结果，将「价格、买入/卖出按钮、输入框」的 class 或选择器填入自动挂单脚本的 selectors 配置
 */
(function() {
    console.log('🔍 开始分析 Predict.fun 页面...');

    // ========== 1. 价格元素分析 ==========
    // 预测市场通常用百分比显示价格（如 45%），这里用正则匹配「数字 + 可选的小数 + %」的文本，
    // 并收集其所在元素的标签、class、父级 class，用于后续构造 CSS 选择器。
    console.log('\n📊 === 页面结构分析 ===\n');
    console.log('💰 价格元素：');

    const priceElements = document.querySelectorAll('*');
    const priceMatches = [];
    priceElements.forEach(el => {
        const text = el.textContent?.trim();
        // 匹配纯数字开头且带 % 的文本，避免匹配到长段落中的数字
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

    // 只展示前 10 个，避免刷屏；通常第一个或前几个是「当前市场价」或订单簿价格
    priceMatches.slice(0, 10).forEach((match, i) => {
        console.log(`${i + 1}. <${match.tag} class="${match.class}"> ${match.text}`);
    });
    if (priceMatches.length > 10) {
        console.log(`   ... 共 ${priceMatches.length} 个匹配，仅显示前 10 个`);
    }

    // ========== 2. 按钮元素分析 ==========
    // 找出所有 button 或 role="button" 的元素，按文案/class 分为「买入 / 卖出 / 订单」三类，
    // 便于确定自动挂单时应点击的按钮选择器。
    console.log('\n🔘 按钮元素：');
    const buttons = document.querySelectorAll('button, [role="button"]');
    const buyButtons = [];
    const sellButtons = [];
    const orderButtons = [];

    buttons.forEach(btn => {
        const text = (btn.textContent || '').trim().toLowerCase();
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
    buyButtons.slice(0, 3).forEach((btn, i) => console.log(`  ${i + 1}. "${btn.text}" class="${btn.class}"`));
    console.log(`卖出按钮: ${sellButtons.length}`);
    sellButtons.slice(0, 3).forEach((btn, i) => console.log(`  ${i + 1}. "${btn.text}" class="${btn.class}"`));
    console.log(`订单按钮: ${orderButtons.length}`);
    orderButtons.slice(0, 3).forEach((btn, i) => console.log(`  ${i + 1}. "${btn.text}" class="${btn.class}"`));

    // ========== 3. API 分析提示 ==========
    // 若网站通过 XHR/fetch 提交订单，可在 Network 面板中筛选 order/trade/market 等关键词，
    // 找到接口后可用 fetch 直接调用，避免依赖 DOM。
    console.log('\n🌐 === API 分析 ===\n');
    console.log('💡 提示：切换到 Network 标签，刷新页面并执行一次交易，查找包含 "order", "trade", "market" 的请求');
    console.log('💡 若找到下单 API，可在自动挂单脚本中用 fetch 替代 DOM 点击');

    // ========== 4. React / 应用根节点 ==========
    // 许多前端使用 #root 或 #__next 作为根节点；React 会在 DOM 上挂载 __reactFiber$ 等属性，
    // 高级用法可据此遍历组件状态，本脚本仅做探测提示。
    console.log('\n⚛️ === 应用状态分析 ===\n');
    const rootElement = document.querySelector('#root, #__next, [data-reactroot]');
    if (rootElement) {
        console.log('✅ 找到根元素:', rootElement.tagName, rootElement.id ? `#${rootElement.id}` : '');
        const fiberKey = Object.keys(rootElement).find(key => key.startsWith('__reactFiber'));
        if (fiberKey) {
            console.log(`✅ 找到 React Fiber: ${fiberKey}`);
        }
    } else {
        console.log('⚠️ 未找到常见 React 根元素');
    }

    const possibleStores = ['__state__', '__store__', 'store', 'state'];
    possibleStores.forEach(key => {
        if (window[key]) console.log(`✅ 找到全局状态: window.${key}`);
    });

    // ========== 5. 输入框分析 ==========
    // 挂单需要填写「价格」和「数量」，通常对应 number 或 text 类型的 input，
    // 记录 placeholder/name 便于区分哪个是价格、哪个是数量。
    console.log('\n📝 === 输入框分析 ===\n');
    const inputs = document.querySelectorAll('input[type="number"], input[type="text"]');
    console.log(`找到 ${inputs.length} 个输入框：`);
    inputs.slice(0, 8).forEach((input, i) => {
        const placeholder = input.placeholder || input.name || input.getAttribute('aria-label') || '无名称';
        console.log(`${i + 1}. ${placeholder} (type=${input.type}, name=${input.name || '-'})`);
    });

    // ========== 6. 可选：data-* / aria-* 属性 ==========
    // 部分站点会使用 data-price、aria-label 等，便于自动化；若有输出可优先用作选择器。
    const dataPriceEls = document.querySelectorAll('[data-price], [data-value]');
    if (dataPriceEls.length > 0) {
        console.log('\n📌 带 data-price / data-value 的元素:', dataPriceEls.length);
        dataPriceEls.forEach((el, i) => {
            if (i < 3) console.log(`  ${el.tagName}`, el.getAttribute('data-price') ?? el.getAttribute('data-value'));
        });
    }

    // ========== 7. 价格监控函数（调试用）==========
    // 每 2 秒读取一次「第一个匹配到的价格元素」，用于确认选择器是否稳定。
    window.monitorPredict = function() {
        console.log('🎯 开始监控 Predict.fun 页面价格（每 2 秒）...');
        let count = 0;
        const interval = setInterval(() => {
            count++;
            const el = document.querySelector('[class*="price"], [class*="Price"], [data-price]');
            if (el) console.log(`[${new Date().toLocaleTimeString()}] 价格: ${el.textContent?.trim()}`);
            if (count > 100) clearInterval(interval);
        }, 2000);
        return interval;
    };

    console.log('\n✅ 分析完成。');
    console.log('💡 可用命令: monitorPredict() — 开始监控价格变化');
})();
```

### 预期输出

- **💰 价格元素**：带百分比的元素及其 `tag`、`class`，用于配置 `priceDisplay` 选择器。
- **🔘 按钮**：买入/卖出/订单按钮的文案与 class，用于配置 `buyButton`、`sellButton`。
- **📝 输入框**：价格、数量输入框的 placeholder/name，用于配置 `priceInput`、`sizeInput`。
- **📌 data-***：若有，可优先用作更稳定的选择器。

---

## 🤖 自动挂单脚本

### 使用方法

**重要**：请先运行「页面分析工具」，将得到的**价格、按钮、输入框**对应的选择器填入下方脚本的 `selectors` 配置中，再在控制台创建实例并 `start()`。

### 脚本代码（含详细注释）

```javascript
/**
 * Predict.fun 自动挂单脚本 - 浏览器控制台版
 *
 * 策略说明（与 config.yaml 中的 strategy / risk 对齐）：
 * - 在当前价格 ± spreadPercent 的范围内挂单（买在 currentPrice - spread，卖在 currentPrice + spread）。
 * - 当市价接近某笔挂单（距离 < cancelThreshold%）时，视为「接近成交」，应撤单并重新挂出，避免被动成交后敞口过大。
 * - 每侧（买/卖）最多 maxOrders 笔挂单；每笔大小为 orderSize。
 *
 * 模式：
 * - dryRun = true：仅模拟，不操作 DOM，不请求 API。
 * - dryRun = false：根据 selectors 操作页面或调用 API（需自行实现 placeOrder 内的实盘逻辑）。
 */
class PredictAutoTrader {
    constructor(config = {}) {
        // ---------- 策略参数 ----------
        this.config = {
            // 挂单范围：当前价 ± spreadPercent（%），例如 6 表示 ±6%
            spreadPercent: config.spreadPercent ?? 6,
            // 每侧（买/卖）最大挂单笔数
            maxOrders: config.maxOrders ?? 3,
            // 每笔挂单的数量（张数/份额）
            orderSize: config.orderSize ?? 10,

            // 撤单阈值（%）：当市价与某笔挂单价格差距小于此值时，逻辑上撤单并重挂
            cancelThreshold: config.cancelThreshold ?? 0.5,
            // 最大风险敞口（金额或数量上限，脚本内仅做简单校验用）
            maxExposure: config.maxExposure ?? 100,

            // 是否模拟运行（true = 不实际操作 DOM/API）
            dryRun: config.dryRun !== undefined ? config.dryRun : true,
            // 主循环间隔（毫秒）
            refreshInterval: config.refreshInterval ?? 5000,

            // 价格限制（与 config.yaml risk 一致）
            minPrice: config.minPrice ?? 0.01,
            maxPrice: config.maxPrice ?? 0.99,

            // 选择器：请根据「页面分析工具」的输出修改；支持字符串或字符串数组（多选一）
            selectors: {
                priceDisplay: config.selectors?.priceDisplay ?? '[class*="price"], [class*="Price"]',
                priceInput: config.selectors?.priceInput ?? 'input[type="number"]',
                sizeInput: config.selectors?.sizeInput ?? null, // 若与 priceInput 不同，可单独指定
                buyButton: config.selectors?.buyButton ?? 'button',
                sellButton: config.selectors?.sellButton ?? 'button'
            }
        };

        // 内存中的挂单列表（仅脚本内部使用；实盘需以网站订单簿为准）
        this.orders = [];
        this.isRunning = false;
        this.intervalId = null;

        console.log('🎲 Predict.fun 自动挂单脚本已加载');
        console.log('📊 模式:', this.config.dryRun ? '🧪 模拟' : '💰 实盘');
    }

    /**
     * 解析选择器：若为数组则依次尝试，返回第一个匹配到的元素；否则直接 querySelector。
     */
    _querySelector(selectorKey) {
        const sel = this.config.selectors[selectorKey];
        if (!sel) return null;
        const list = Array.isArray(sel) ? sel : [sel];
        for (const s of list) {
            const el = document.querySelector(s);
            if (el) return el;
        }
        return null;
    }

    /**
     * 获取当前市场价格（0~1 小数）。
     * 从 priceDisplay 元素中提取数字+% 并转为小数，例如 "45.5%" -> 0.455。
     */
    getCurrentPrice() {
        const el = this._querySelector('priceDisplay');
        if (!el) return null;
        const text = (el.textContent || '').trim();
        const match = text.match(/(\d+\.?\d*)\s*%?/);
        if (match) return Math.max(0, Math.min(1, parseFloat(match[1]) / 100));
        return null;
    }

    /**
     * 根据当前价与 spread 计算「建议的买一价」和「卖一价」。
     * 买单价 = currentPrice - spread，卖单价 = currentPrice + spread，并夹在 minPrice~maxPrice 之间。
     */
    calculateOrderPrices(currentPrice) {
        if (currentPrice == null || currentPrice === undefined) return null;
        const spread = this.config.spreadPercent / 100;
        return {
            buy: Math.max(this.config.minPrice, currentPrice - spread),
            sell: Math.min(this.config.maxPrice, currentPrice + spread)
        };
    }

    /**
     * 检查单笔订单是否应被「逻辑撤单」：当市价与挂单价格差距小于 cancelThreshold% 时撤单。
     * 返回 true 表示应撤单。
     */
    _shouldCancelOrder(order, currentPrice) {
        const threshold = this.config.cancelThreshold / 100;
        const dist = Math.abs(order.price - currentPrice);
        return dist < threshold;
    }

    /**
     * 管理挂单：先根据当前价撤掉「过近」的订单，再在买卖两侧补足到 maxOrders 笔。
     */
    async manageOrders(currentPrice) {
        const prices = this.calculateOrderPrices(currentPrice);
        if (!prices) return;

        const threshold = this.config.cancelThreshold / 100;

        // ---------- 1. 撤掉距离市价过近的挂单（逻辑撤单：从内存中移除） ----------
        const openOrders = this.orders.filter(o => o.status === 'open');
        for (const order of openOrders) {
            if (this._shouldCancelOrder(order, currentPrice)) {
                order.status = 'cancelled';
                console.log(`🔄 [撤单] ${order.side} @ ${(order.price * 100).toFixed(1)}% (距市价 < ${this.config.cancelThreshold}%)`);
                // 实盘时可在此调用撤单 API 或点击页面上的撤单按钮
            }
        }

        const stillOpen = this.orders.filter(o => o.status === 'open');
        const buyOrders = stillOpen.filter(o => o.side === 'buy');
        const sellOrders = stillOpen.filter(o => o.side === 'sell');

        // ---------- 2. 买侧：若不足 maxOrders 笔，则在 prices.buy 挂新单 ----------
        while (buyOrders.length < this.config.maxOrders) {
            const order = await this.placeOrder('buy', prices.buy, this.config.orderSize);
            if (order) buyOrders.push(order);
            break; // 每轮只补一笔，避免单次 tick 挂太多
        }

        // ---------- 3. 卖侧：若不足 maxOrders 笔，则在 prices.sell 挂新单 ----------
        while (sellOrders.length < this.config.maxOrders) {
            const order = await this.placeOrder('sell', prices.sell, this.config.orderSize);
            if (order) sellOrders.push(order);
            break;
        }
    }

    /**
     * 下单：模拟模式下只生成订单对象并推入 this.orders；实盘模式下需在此处填写 DOM 操作或 API 调用。
     * @param {string} side - 'buy' | 'sell'
     * @param {number} price - 0~1 小数
     * @param {number} size - 数量
     */
    async placeOrder(side, price, size) {
        const order = {
            id: `order_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
            side,
            price,
            size,
            timestamp: Date.now(),
            status: 'open'
        };

        if (this.config.dryRun) {
            console.log(`🧪 [模拟] ${side.toUpperCase()} ${size} @ ${(price * 100).toFixed(1)}%`);
            this.orders.push(order);
            return order;
        }

        // ---------- 实盘：根据页面结构填写价格、数量并点击按钮 ----------
        // 可调用下方的 DOM 辅助方法，或使用 fetch 调用分析工具中找到的 API（见「实盘下单的 DOM 操作说明」）
        try {
            const pricePct = (price * 100).toFixed(1);
            const priceInput = this._querySelector('priceInput');
            const sizeInputSel = this.config.selectors.sizeInput ? this._querySelector('sizeInput') : priceInput;
            if (priceInput) {
                priceInput.focus();
                priceInput.value = pricePct;
                priceInput.dispatchEvent(new Event('input', { bubbles: true }));
                priceInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
            if (sizeInputSel && sizeInputSel !== priceInput) {
                sizeInputSel.value = String(size);
                sizeInputSel.dispatchEvent(new Event('input', { bubbles: true }));
            }
            const btn = this._querySelector(side === 'buy' ? 'buyButton' : 'sellButton');
            if (btn) {
                btn.click();
            }
            console.log(`💰 [实盘] ${side.toUpperCase()} ${size} @ ${pricePct}%`);
            this.orders.push(order);
            return order;
        } catch (e) {
            console.warn('实盘下单失败:', e);
            return null;
        }
    }

    /**
     * 主循环：取价 → 管理挂单 → 输出状态。
     */
    async tick() {
        const currentPrice = this.getCurrentPrice();
        if (currentPrice == null) {
            console.warn('⚠️ 无法获取当前价格，请检查 selectors.priceDisplay 是否与页面一致');
            return;
        }
        console.log(`📊 当前价格: ${(currentPrice * 100).toFixed(1)}%`);
        await this.manageOrders(currentPrice);
        const openCount = this.orders.filter(o => o.status === 'open').length;
        console.log(`📋 当前挂单数: ${openCount}`);
    }

    /** 开始定时执行 tick */
    start() {
        if (this.isRunning) {
            console.warn('⚠️ 已在运行中');
            return;
        }
        this.isRunning = true;
        console.log('🚀 自动挂单已启动，间隔 ' + (this.config.refreshInterval / 1000) + ' 秒');
        this.tick();
        this.intervalId = setInterval(() => this.tick(), this.config.refreshInterval);
    }

    /** 停止定时器 */
    stop() {
        if (!this.isRunning) return;
        this.isRunning = false;
        if (this.intervalId) {
            clearInterval(this.intervalId);
            this.intervalId = null;
        }
        console.log('🛑 自动挂单已停止');
    }

    /** 切换模拟/实盘 */
    setDryRun(dryRun) {
        this.config.dryRun = dryRun;
        console.log('🔄 模式: ' + (dryRun ? '🧪 模拟' : '💰 实盘'));
    }

    /** 返回当前运行状态与配置摘要 */
    getStatus() {
        const openOrders = this.orders.filter(o => o.status === 'open');
        const currentPrice = this.getCurrentPrice();
        return {
            isRunning: this.isRunning,
            mode: this.config.dryRun ? '模拟' : '实盘',
            currentPrice: currentPrice != null ? (currentPrice * 100).toFixed(1) + '%' : null,
            openOrderCount: openOrders.length,
            openOrders: openOrders.map(o => ({ side: o.side, price: (o.price * 100).toFixed(1) + '%', size: o.size })),
            config: {
                spreadPercent: this.config.spreadPercent,
                maxOrders: this.config.maxOrders,
                cancelThreshold: this.config.cancelThreshold,
                refreshInterval: this.config.refreshInterval
            }
        };
    }
}

// ---------- 控制台使用说明 ----------
console.log(`
╔════════════════════════════════════════════════════════════╗
║     🎲 Predict.fun 自动挂单脚本 v1.1（浏览器 F12 版）       ║
╚════════════════════════════════════════════════════════════╝

📖 用法：

1. 创建实例（默认模拟）：
   const trader = new PredictAutoTrader();

2. 自定义参数（与 config.yaml 对齐）：
   const trader = new PredictAutoTrader({
       spreadPercent: 6,
       maxOrders: 3,
       orderSize: 10,
       cancelThreshold: 0.5,
       dryRun: true,
       refreshInterval: 5000,
       selectors: {
           priceDisplay: '[class*="price"]',
           priceInput: 'input[name="price"]',
           buyButton: 'button[class*="buy"]',
           sellButton: 'button[class*="sell"]'
       }
   });

3. 启动 / 停止 / 状态：
   trader.start();
   trader.stop();
   trader.getStatus();

4. 切实盘（务必先确认选择器正确）：
   trader.setDryRun(false);
`);

window.PredictAutoTrader = PredictAutoTrader;
```

---

## 📝 使用步骤

### 第一步：运行分析工具

1. 打开 https://predict.fun/markets/ 并进入要挂单的市场页。
2. F12 → Console，粘贴**页面分析工具**脚本并执行。
3. 记录控制台中的「价格元素、买入/卖出按钮、输入框」的 class 或可用的 CSS 选择器。

### 第二步：配置并运行自动挂单脚本

1. 粘贴**自动挂单脚本**到控制台并执行（会注册 `PredictAutoTrader`）。
2. 根据分析结果，在 `new PredictAutoTrader({ selectors: { ... } })` 中填入正确的选择器。
3. 先用**模拟模式**测试：
   ```javascript
   const trader = new PredictAutoTrader({ dryRun: true, spreadPercent: 6, maxOrders: 3, orderSize: 10 });
   trader.start();
   ```
4. 观察 `getCurrentPrice()`、`calculateOrderPrices()` 和挂单/撤单日志是否符合预期。

### 第三步：实盘（谨慎）

在确认选择器与逻辑无误后，再切换实盘并小额测试：

```javascript
trader.setDryRun(false);
```

---

## 🔧 实盘下单的 DOM 操作说明

当 `dryRun: false` 时，脚本会在 `placeOrder` 内尝试：

1. **价格输入框**：`priceInput.value = (price * 100).toFixed(1)`，并触发 `input`、`change` 事件，以便 React/Vue 等框架更新状态。
2. **数量输入框**：若有 `sizeInput`，则设置 `value = size` 并触发事件。
3. **按钮**：根据 `side` 选择 `buyButton` 或 `sellButton` 并执行 `click()`。

若页面结构复杂（例如价格在弹窗内、有多步确认），可只保留「模拟下单」逻辑，实盘改为手动按通知操作；或根据分析工具在 Network 中找到的下单 API，在 `placeOrder` 内用 `fetch` 调用，例如：

```javascript
// 示例：若分析得到下单接口为 POST /api/orders（需替换为实际 URL 与参数）
async placeOrder(side, price, size) {
    if (this.config.dryRun) { /* 模拟... */ return order; }
    const res = await fetch('https://api.predict.fun/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + YOUR_TOKEN },
        body: JSON.stringify({ side, price, size })
    });
    const data = await res.json();
    // 将 data 转为 order 对象并 push 到 this.orders...
}
```

---

## ⚠️ 重要注意事项

### 风险与限制

- **务必先在模拟模式验证**逻辑与选择器。
- **实盘前小额测试**，并确认选择器在当前页面版本下有效。
- 页面改版后 DOM 可能变化，选择器需重新用分析工具核对。
- 需保持该标签页打开；长时间无人值守有风险。
- 频繁请求或自动化可能触发网站风控，请谨慎使用。

### 与后端配合建议

- 使用本仓库的 **Railway / arbitrage_main.py** 做套利监控与 Telegram 通知。
- 浏览器脚本作为**单市场、单页面的挂单辅助**，与后端配置（如 `spread_percent`、`cancel_threshold`）保持一致即可。

---

## 📚 相关文档

- [API申请指南.md](API申请指南.md)
- [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

---

**创建日期**: 2026-02-05  
**更新日期**: 2026-02-19  
**版本**: v1.1  

⚠️ **免责声明**: 本脚本仅供学习与研究使用，使用本脚本进行实际交易的风险由使用者自行承担。
