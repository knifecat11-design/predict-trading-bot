/**
 * ============================================================
 *  Predict.fun F12 自动挂单脚本 v2.0
 *  在浏览器控制台中粘贴运行，复用登录状态
 * ============================================================
 *
 *  使用方法：
 *  1. 打开 https://predict.fun 并登录
 *  2. 进入你想交易的市场页面
 *  3. 按 F12 → Console（控制台）
 *  4. 复制本脚本全部内容，粘贴到控制台，按回车
 *  5. 按照屏幕上的提示操作
 *
 *  核心功能：
 *  - 自动嗅探 API 密钥和认证信息
 *  - 查看市场数据、订单簿、持仓
 *  - 下单 / 撤单
 *  - 浮动状态面板（实时显示）
 *  - 网格挂单策略
 */

(function () {
  "use strict";

  // ==================== 防重复加载 ====================
  if (window.__PFT_LOADED__) {
    console.log("⚠️ 脚本已加载，请用 pft.help() 查看帮助");
    return;
  }
  window.__PFT_LOADED__ = true;

  // ==================== 配置 ====================
  const CONFIG = {
    // API
    BASE_URL: "https://api.predict.fun",
    // 刷新间隔（毫秒）
    REFRESH_INTERVAL: 5000,
    // 面板
    PANEL_WIDTH: 380,
    // 安全
    DRY_RUN: true, // 默认模拟模式，不会真正下单
  };

  // ==================== 1. 网络嗅探器 ====================
  // 自动从页面请求中捕获 API Key 和 JWT Token

  const sniffer = {
    apiKey: "",
    jwt: "",
    predictAccount: "",
    captured: [],

    start() {
      const self = this;
      const origFetch = window.fetch;

      window.fetch = function (...args) {
        try {
          const [url, opts] = args;
          const urlStr = typeof url === "string" ? url : url?.url || "";

          // 只关注 predict.fun 的请求
          if (urlStr.includes("predict.fun")) {
            const headers = opts?.headers || {};
            const headerObj =
              headers instanceof Headers
                ? Object.fromEntries(headers.entries())
                : headers;

            // 捕获 API Key
            const apiKey =
              headerObj["x-api-key"] ||
              headerObj["X-Api-Key"] ||
              headerObj["X-API-KEY"];
            if (apiKey && !self.apiKey) {
              self.apiKey = apiKey;
              console.log("🔑 已捕获 API Key:", apiKey.slice(0, 8) + "...");
            }

            // 捕获 JWT
            const auth = headerObj["authorization"] || headerObj["Authorization"];
            if (auth && auth.startsWith("Bearer ")) {
              self.jwt = auth;
              console.log("🎫 已捕获 JWT Token");
            }

            // 记录端点
            self.captured.push({
              time: new Date().toLocaleTimeString(),
              method: opts?.method || "GET",
              url: urlStr.replace(CONFIG.BASE_URL, ""),
            });
          }
        } catch (_) {
          /* 忽略解析错误 */
        }

        return origFetch.apply(this, args);
      };

      console.log("📡 网络嗅探器已启动，正在捕获认证信息...");
      console.log("💡 提示：在页面上做任意操作（点击市场等），即可自动捕获");
    },

    // 显示捕获到的信息
    status() {
      console.log("\n=== 🔑 认证信息状态 ===");
      console.log("API Key:", this.apiKey ? "✅ " + this.apiKey.slice(0, 8) + "..." : "❌ 未捕获");
      console.log("JWT:", this.jwt ? "✅ 已捕获" : "❌ 未捕获");
      console.log("捕获的请求数:", this.captured.length);
      if (this.captured.length > 0) {
        console.log("最近请求:");
        this.captured.slice(-5).forEach((r) => {
          console.log(`  ${r.time} ${r.method} ${r.url}`);
        });
      }
      console.log("");
    },

    // 手动设置 API Key（如果自动捕获失败）
    setApiKey(key) {
      this.apiKey = key;
      console.log("✅ API Key 已手动设置");
    },

    // 手动设置 JWT
    setJwt(token) {
      this.jwt = token.startsWith("Bearer ") ? token : "Bearer " + token;
      console.log("✅ JWT 已手动设置");
    },

    // 构建请求头
    getHeaders() {
      const h = { "Content-Type": "application/json" };
      if (this.apiKey) h["x-api-key"] = this.apiKey;
      if (this.jwt) h["Authorization"] = this.jwt;
      return h;
    },
  };

  // ==================== 2. API 客户端 ====================
  // 封装 Predict.fun REST API

  const api = {
    async request(method, path, body) {
      const url = CONFIG.BASE_URL + path;
      const opts = {
        method,
        headers: sniffer.getHeaders(),
      };
      if (body) opts.body = JSON.stringify(body);

      const resp = await fetch(url, opts);
      const data = await resp.json();

      if (!resp.ok) {
        console.error(`❌ API 错误 [${resp.status}]:`, data);
        return null;
      }
      return data;
    },

    // 获取市场列表
    async getMarkets(count = 20) {
      return this.request("GET", `/v1/markets?first=${count}`);
    },

    // 获取单个市场
    async getMarket(marketId) {
      return this.request("GET", `/v1/markets/${marketId}`);
    },

    // 获取订单簿
    async getOrderbook(marketId) {
      return this.request("GET", `/v1/markets/${marketId}/orderbook`);
    },

    // 获取市场统计
    async getMarketStats(marketId) {
      return this.request("GET", `/v1/markets/${marketId}/statistics`);
    },

    // 获取我的持仓
    async getPositions() {
      return this.request("GET", "/v1/positions");
    },

    // 获取我的订单
    async getOrders() {
      return this.request("GET", "/v1/orders");
    },

    // 撤销指定订单（仅链下订单簿）
    async cancelOrders(orderIds) {
      if (!Array.isArray(orderIds)) orderIds = [orderIds];
      return this.request("POST", "/v1/orders/remove", {
        data: { ids: orderIds },
      });
    },
  };

  // ==================== 3. 页面交互器 ====================
  // 通过操作页面元素来下单（最可靠的方式）

  const page = {
    // 从当前 URL 提取市场 ID
    getMarketIdFromUrl() {
      const match = window.location.pathname.match(
        /\/(?:markets|market)\/([^/]+)/
      );
      return match ? match[1] : null;
    },

    // 查找输入框
    findInputs() {
      const inputs = document.querySelectorAll('input[type="number"], input[type="text"], input[inputmode="decimal"]');
      const result = [];
      inputs.forEach((input) => {
        const label =
          input.placeholder ||
          input.ariaLabel ||
          input.name ||
          input.closest("label")?.textContent?.trim() ||
          "";
        result.push({ element: input, label, type: input.type });
      });
      return result;
    },

    // 查找按钮
    findButtons() {
      const buttons = document.querySelectorAll("button");
      const result = [];
      buttons.forEach((btn) => {
        const text = btn.textContent?.trim() || "";
        if (text.length > 0 && text.length < 50) {
          result.push({ element: btn, text });
        }
      });
      return result;
    },

    // 在输入框中设置值（兼容 React）
    setInputValue(input, value) {
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value"
      ).set;
      nativeInputValueSetter.call(input, String(value));
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    },

    // 查找包含指定文字的按钮
    findButtonByText(text) {
      const buttons = document.querySelectorAll("button");
      const textLower = text.toLowerCase();
      for (const btn of buttons) {
        const btnText = btn.textContent?.trim()?.toLowerCase() || "";
        if (btnText.includes(textLower) && !btn.disabled) {
          return btn;
        }
      }
      return null;
    },

    // 分析当前页面的交易界面
    analyze() {
      console.log("\n=== 📄 页面分析结果 ===\n");

      // 市场 ID
      const marketId = this.getMarketIdFromUrl();
      console.log("市场 ID:", marketId || "❌ 未检测到（请进入具体市场页面）");

      // 输入框
      const inputs = this.findInputs();
      console.log(`\n输入框 (${inputs.length} 个):`);
      inputs.forEach((inp, i) => {
        console.log(`  ${i + 1}. [${inp.type}] "${inp.label}"`);
      });

      // 关键按钮
      const buttons = this.findButtons();
      const keywords = ["buy", "sell", "yes", "no", "place", "submit", "confirm", "order", "limit", "market"];
      const matchedButtons = buttons.filter((b) =>
        keywords.some((k) => b.text.toLowerCase().includes(k))
      );
      console.log(`\n交易相关按钮 (${matchedButtons.length} 个):`);
      matchedButtons.forEach((btn, i) => {
        console.log(`  ${i + 1}. "${btn.text}"`);
      });

      // Tab 按钮（Buy/Sell 切换）
      const tabs = document.querySelectorAll('[role="tab"], [role="tablist"] button');
      if (tabs.length > 0) {
        console.log(`\nTab 切换 (${tabs.length} 个):`);
        tabs.forEach((tab, i) => {
          console.log(`  ${i + 1}. "${tab.textContent?.trim()}" ${tab.ariaSelected === "true" ? "← 当前" : ""}`);
        });
      }

      console.log("\n💡 提示：如果看不到交易界面，请先在页面上点击 Yes 或 No");
      console.log("");

      return { marketId, inputs, buttons: matchedButtons };
    },
  };

  // ==================== 4. 交易管理器 ====================

  const trader = {
    _intervalId: null,
    _running: false,
    _orders: [],
    _stats: { placed: 0, cancelled: 0, errors: 0 },

    // 通过 UI 下单
    async placeOrderViaUI(side, price, amount) {
      console.log(`\n📝 准备下单: ${side.toUpperCase()} | 价格: ${price} | 数量: ${amount}`);

      if (CONFIG.DRY_RUN) {
        console.log("🧪 [模拟模式] 不会真正下单");
        console.log(`   ➜ ${side.toUpperCase()} ${amount} 份 @ ${price}`);
        this._stats.placed++;
        return { success: true, dryRun: true };
      }

      try {
        // 1. 切换到对应 Tab (Buy Yes / Buy No)
        const sideText = side.toLowerCase();
        let tabButton = null;

        if (sideText === "yes" || sideText === "buy_yes") {
          tabButton = page.findButtonByText("yes");
        } else if (sideText === "no" || sideText === "buy_no") {
          tabButton = page.findButtonByText("no");
        }

        if (tabButton) {
          tabButton.click();
          await this._sleep(500);
        }

        // 2. 切换到 Limit 模式（如果有）
        const limitTab = page.findButtonByText("limit");
        if (limitTab) {
          limitTab.click();
          await this._sleep(300);
        }

        // 3. 填入价格和数量
        const inputs = page.findInputs();
        if (inputs.length < 2) {
          console.error("❌ 找不到价格/数量输入框，请确保交易界面已打开");
          this._stats.errors++;
          return { success: false, error: "inputs_not_found" };
        }

        // 通常第一个是价格，第二个是数量
        page.setInputValue(inputs[0].element, price);
        await this._sleep(200);
        page.setInputValue(inputs[1].element, amount);
        await this._sleep(200);

        // 4. 点击下单按钮
        const submitBtn =
          page.findButtonByText("place") ||
          page.findButtonByText("submit") ||
          page.findButtonByText("confirm") ||
          page.findButtonByText("buy");

        if (!submitBtn) {
          console.error("❌ 找不到下单按钮");
          this._stats.errors++;
          return { success: false, error: "button_not_found" };
        }

        submitBtn.click();
        this._stats.placed++;
        console.log(`✅ 已点击下单按钮: "${submitBtn.textContent?.trim()}"`);

        // 5. 等待确认弹窗（如果有）
        await this._sleep(1000);
        const confirmBtn = page.findButtonByText("confirm");
        if (confirmBtn && confirmBtn !== submitBtn) {
          confirmBtn.click();
          console.log("✅ 已确认");
        }

        return { success: true };
      } catch (err) {
        console.error("❌ 下单失败:", err.message);
        this._stats.errors++;
        return { success: false, error: err.message };
      }
    },

    // 撤销订单（通过 API）
    async cancelOrder(orderId) {
      if (CONFIG.DRY_RUN) {
        console.log(`🧪 [模拟] 撤单: ${orderId}`);
        this._stats.cancelled++;
        return true;
      }

      const result = await api.cancelOrders([orderId]);
      if (result) {
        console.log(`✅ 撤单成功: ${orderId}`);
        this._stats.cancelled++;
        return true;
      }
      return false;
    },

    // 撤销所有订单
    async cancelAllOrders() {
      const ordersData = await api.getOrders();
      if (!ordersData?.data) {
        console.log("❌ 无法获取订单列表");
        return 0;
      }

      const orders = Array.isArray(ordersData.data) ? ordersData.data : [];
      if (orders.length === 0) {
        console.log("📋 没有挂单");
        return 0;
      }

      const ids = orders.map((o) => o.id || o.orderId).filter(Boolean);
      if (ids.length === 0) return 0;

      if (CONFIG.DRY_RUN) {
        console.log(`🧪 [模拟] 撤销 ${ids.length} 个订单`);
        return ids.length;
      }

      const result = await api.cancelOrders(ids);
      const count = result?.data?.removed?.length || 0;
      console.log(`✅ 已撤销 ${count} 个订单`);
      this._stats.cancelled += count;
      return count;
    },

    // 网格挂单
    async gridOrders(side, centerPrice, spread, levels, amountPerLevel) {
      console.log(`\n📐 网格挂单策略`);
      console.log(`   方向: ${side} | 中心价: ${centerPrice} | 价差: ${spread}`);
      console.log(`   层数: ${levels} | 每层数量: ${amountPerLevel}`);
      console.log("");

      const orders = [];
      for (let i = 0; i < levels; i++) {
        const offset = spread * (i + 1);
        const price =
          side.toLowerCase().includes("buy") || side.toLowerCase() === "yes"
            ? Math.max(0.01, +(centerPrice - offset).toFixed(4))
            : Math.min(0.99, +(centerPrice + offset).toFixed(4));

        console.log(`   [${i + 1}/${levels}] ${side} @ ${price} x ${amountPerLevel}`);
        const result = await this.placeOrderViaUI(side, price, amountPerLevel);
        orders.push({ price, result });
        await this._sleep(2000); // 每单间隔 2 秒
      }

      console.log(`\n📐 网格挂单完成: ${orders.filter((o) => o.result.success).length}/${levels} 成功`);
      return orders;
    },

    // 获取统计
    getStats() {
      return { ...this._stats };
    },

    _sleep(ms) {
      return new Promise((r) => setTimeout(r, ms));
    },
  };

  // ==================== 5. 状态面板 ====================

  const panel = {
    _el: null,
    _intervalId: null,
    _marketId: null,

    show() {
      if (this._el) {
        this._el.style.display = "block";
        return;
      }

      this._el = document.createElement("div");
      this._el.id = "pft-panel";
      this._el.innerHTML = `
        <div style="
          position:fixed; top:10px; right:10px; z-index:99999;
          width:${CONFIG.PANEL_WIDTH}px;
          background:#1a1a2e; color:#e0e0e0;
          border:1px solid #333; border-radius:12px;
          font-family:'Segoe UI',monospace; font-size:13px;
          box-shadow:0 4px 20px rgba(0,0,0,0.5);
          user-select:none;
        ">
          <!-- 标题栏 -->
          <div id="pft-header" style="
            padding:10px 14px; cursor:move;
            background:#16213e; border-radius:12px 12px 0 0;
            display:flex; justify-content:space-between; align-items:center;
          ">
            <span style="font-weight:bold; font-size:14px;">📊 Predict.fun Trader</span>
            <div>
              <span id="pft-mode" style="
                font-size:11px; padding:2px 8px; border-radius:10px;
                background:#ff6b6b; color:#fff;
              ">模拟</span>
              <button id="pft-close" style="
                margin-left:8px; background:none; border:none;
                color:#888; cursor:pointer; font-size:16px;
              ">✕</button>
            </div>
          </div>
          <!-- 内容区 -->
          <div id="pft-body" style="padding:12px 14px; max-height:500px; overflow-y:auto;">
            <div id="pft-content">正在加载...</div>
          </div>
          <!-- 快捷操作 -->
          <div style="padding:8px 14px 12px; border-top:1px solid #333;">
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button class="pft-btn" onclick="pft.refresh()" style="flex:1;">🔄 刷新</button>
              <button class="pft-btn" onclick="pft.toggleMode()" style="flex:1;">🔀 切换模式</button>
              <button class="pft-btn pft-btn-danger" onclick="pft.cancelAll()" style="flex:1;">🗑️ 全部撤单</button>
            </div>
          </div>
        </div>
      `;

      // 按钮样式
      const style = document.createElement("style");
      style.textContent = `
        .pft-btn {
          padding:6px 10px; border:1px solid #444; border-radius:6px;
          background:#2a2a4a; color:#ddd; cursor:pointer; font-size:12px;
          transition: background 0.2s;
        }
        .pft-btn:hover { background:#3a3a5a; }
        .pft-btn-danger { border-color:#ff6b6b; }
        .pft-btn-danger:hover { background:#4a2020; }
        #pft-panel::-webkit-scrollbar { width:6px; }
        #pft-panel::-webkit-scrollbar-thumb { background:#555; border-radius:3px; }
      `;
      document.head.appendChild(style);
      document.body.appendChild(this._el);

      // 拖拽功能
      this._enableDrag();

      // 关闭按钮
      document.getElementById("pft-close").onclick = () => this.hide();

      // 自动刷新
      this.refresh();
      this._intervalId = setInterval(() => this.refresh(), CONFIG.REFRESH_INTERVAL);
    },

    hide() {
      if (this._el) this._el.style.display = "none";
      if (this._intervalId) clearInterval(this._intervalId);
    },

    async refresh() {
      const content = document.getElementById("pft-content");
      if (!content) return;

      // 更新模式标签
      const modeEl = document.getElementById("pft-mode");
      if (modeEl) {
        modeEl.textContent = CONFIG.DRY_RUN ? "模拟" : "实盘";
        modeEl.style.background = CONFIG.DRY_RUN ? "#ff6b6b" : "#2ecc71";
      }

      let html = "";

      // 认证状态
      html += `<div style="margin-bottom:10px; padding:6px 8px; background:#111; border-radius:6px; font-size:11px;">`;
      html += `🔑 API Key: ${sniffer.apiKey ? "✅" : "❌"} | `;
      html += `🎫 JWT: ${sniffer.jwt ? "✅" : "❌"}`;
      html += `</div>`;

      // 市场信息
      const marketId = page.getMarketIdFromUrl();
      if (marketId && marketId !== this._marketId) {
        this._marketId = marketId;
      }

      if (this._marketId && sniffer.apiKey) {
        try {
          // 获取订单簿
          const ob = await api.getOrderbook(this._marketId);
          if (ob?.data) {
            const bids = ob.data.bids || [];
            const asks = ob.data.asks || [];

            html += `<div style="margin-bottom:10px;">`;
            html += `<div style="font-weight:bold; margin-bottom:6px;">📈 订单簿</div>`;
            html += `<div style="display:flex; gap:8px;">`;

            // 买单
            html += `<div style="flex:1;">`;
            html += `<div style="color:#2ecc71; font-size:11px; margin-bottom:4px;">买单 (Bid)</div>`;
            if (bids.length > 0) {
              bids.slice(0, 5).forEach((b) => {
                const price = Array.isArray(b) ? b[0] : b.price;
                const size = Array.isArray(b) ? b[1] : b.size;
                html += `<div style="font-size:11px; color:#2ecc71;">${(+price * 100).toFixed(1)}¢ × ${size}</div>`;
              });
            } else {
              html += `<div style="font-size:11px; color:#666;">无</div>`;
            }
            html += `</div>`;

            // 卖单
            html += `<div style="flex:1;">`;
            html += `<div style="color:#e74c3c; font-size:11px; margin-bottom:4px;">卖单 (Ask)</div>`;
            if (asks.length > 0) {
              asks.slice(0, 5).forEach((a) => {
                const price = Array.isArray(a) ? a[0] : a.price;
                const size = Array.isArray(a) ? a[1] : a.size;
                html += `<div style="font-size:11px; color:#e74c3c;">${(+price * 100).toFixed(1)}¢ × ${size}</div>`;
              });
            } else {
              html += `<div style="font-size:11px; color:#666;">无</div>`;
            }
            html += `</div>`;

            html += `</div></div>`;

            // 买一卖一价差
            if (bids.length > 0 && asks.length > 0) {
              const bestBid = +(Array.isArray(bids[0]) ? bids[0][0] : bids[0].price);
              const bestAsk = +(Array.isArray(asks[0]) ? asks[0][0] : asks[0].price);
              const spread = ((bestAsk - bestBid) * 100).toFixed(2);
              html += `<div style="text-align:center; font-size:12px; color:#f39c12; margin-bottom:10px;">`;
              html += `价差: ${spread}¢ | 买一: ${(bestBid * 100).toFixed(1)}¢ | 卖一: ${(bestAsk * 100).toFixed(1)}¢`;
              html += `</div>`;
            }
          }
        } catch (_) {
          html += `<div style="color:#e74c3c; font-size:11px;">❌ 获取订单簿失败</div>`;
        }
      } else if (!sniffer.apiKey) {
        html += `<div style="color:#f39c12; font-size:12px; margin:10px 0;">`;
        html += `⚠️ 请在页面上做任意操作以捕获 API Key<br>`;
        html += `或手动设置: <code>pft.setApiKey("你的key")</code>`;
        html += `</div>`;
      } else {
        html += `<div style="color:#f39c12; font-size:12px; margin:10px 0;">`;
        html += `⚠️ 请进入具体市场页面`;
        html += `</div>`;
      }

      // 统计信息
      const stats = trader.getStats();
      html += `<div style="margin-top:8px; padding:6px 8px; background:#111; border-radius:6px; font-size:11px;">`;
      html += `📊 已下单: ${stats.placed} | 已撤单: ${stats.cancelled} | 错误: ${stats.errors}`;
      html += `</div>`;

      content.innerHTML = html;
    },

    _enableDrag() {
      const header = document.getElementById("pft-header");
      const container = this._el.firstElementChild;
      let isDragging = false;
      let startX, startY, startLeft, startTop;

      header.addEventListener("mousedown", (e) => {
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        const rect = container.getBoundingClientRect();
        startLeft = rect.left;
        startTop = rect.top;
        container.style.position = "fixed";
      });

      document.addEventListener("mousemove", (e) => {
        if (!isDragging) return;
        container.style.left = startLeft + (e.clientX - startX) + "px";
        container.style.top = startTop + (e.clientY - startY) + "px";
        container.style.right = "auto";
      });

      document.addEventListener("mouseup", () => {
        isDragging = false;
      });
    },
  };

  // ==================== 6. 主控制器 ====================

  const pft = {
    // --- 帮助 ---
    help() {
      console.log(`
╔══════════════════════════════════════════════════════════════╗
║           📊 Predict.fun F12 Trader v2.0 - 帮助             ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  🔑 认证设置                                                 ║
║  pft.setApiKey("xxx")     手动设置 API Key                   ║
║  pft.setJwt("xxx")        手动设置 JWT Token                 ║
║  pft.authStatus()         查看认证状态                       ║
║                                                              ║
║  📊 查看数据                                                 ║
║  pft.markets()            查看市场列表                       ║
║  pft.orderbook()          查看当前市场订单簿                 ║
║  pft.positions()          查看我的持仓                       ║
║  pft.orders()             查看我的挂单                       ║
║  pft.analyze()            分析当前页面元素                   ║
║                                                              ║
║  💰 交易操作                                                 ║
║  pft.buy("yes", 0.45, 10) 买 Yes @ 0.45, 数量 10            ║
║  pft.buy("no", 0.55, 10)  买 No @ 0.55, 数量 10             ║
║  pft.cancel("订单ID")     撤销指定订单                       ║
║  pft.cancelAll()          撤销所有挂单                       ║
║                                                              ║
║  📐 网格策略                                                 ║
║  pft.grid("yes",0.40,0.02,3,5)                              ║
║    ↑ 买Yes, 中心价0.40, 价差0.02, 3层, 每层5份              ║
║                                                              ║
║  🎛️ 设置                                                     ║
║  pft.toggleMode()         切换 模拟/实盘 模式                ║
║  pft.showPanel()          显示状态面板                       ║
║  pft.hidePanel()          隐藏状态面板                       ║
║  pft.refresh()            手动刷新面板数据                   ║
║                                                              ║
║  ⚠️  注意: 默认是【模拟模式】, 不会真正下单                  ║
║  ⚠️  切换实盘: pft.toggleMode()                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
      `);
    },

    // --- 认证 ---
    setApiKey(key) {
      sniffer.setApiKey(key);
    },
    setJwt(token) {
      sniffer.setJwt(token);
    },
    authStatus() {
      sniffer.status();
    },

    // --- 数据查询 ---
    async markets() {
      const data = await api.getMarkets(20);
      if (!data?.data) {
        console.log("❌ 获取市场失败，请检查 API Key");
        return;
      }
      const markets = Array.isArray(data.data) ? data.data : data.data.markets || [];
      console.log(`\n=== 📋 市场列表 (${markets.length} 个) ===\n`);
      markets.forEach((m, i) => {
        const title = m.title || m.question || "Unknown";
        const status = m.status || "";
        console.log(`${i + 1}. [${m.id}] ${title} (${status})`);
      });
      console.log("");
      return markets;
    },

    async orderbook(marketId) {
      const mid = marketId || page.getMarketIdFromUrl();
      if (!mid) {
        console.log("❌ 请指定市场 ID 或进入市场页面");
        return;
      }
      const data = await api.getOrderbook(mid);
      if (!data?.data) {
        console.log("❌ 获取订单簿失败");
        return;
      }
      const bids = data.data.bids || [];
      const asks = data.data.asks || [];

      console.log(`\n=== 📈 订单簿 (市场 ${mid}) ===\n`);
      console.log("  买单 (Bid)          卖单 (Ask)");
      console.log("  ─────────────       ─────────────");
      const maxLen = Math.max(bids.length, asks.length, 5);
      for (let i = 0; i < Math.min(maxLen, 10); i++) {
        const bid = bids[i];
        const ask = asks[i];
        const bidStr = bid
          ? `${(+(Array.isArray(bid) ? bid[0] : bid.price) * 100).toFixed(1)}¢ × ${Array.isArray(bid) ? bid[1] : bid.size}`
          : "";
        const askStr = ask
          ? `${(+(Array.isArray(ask) ? ask[0] : ask.price) * 100).toFixed(1)}¢ × ${Array.isArray(ask) ? ask[1] : ask.size}`
          : "";
        console.log(`  ${bidStr.padEnd(18)} ${askStr}`);
      }
      console.log("");
      return data.data;
    },

    async positions() {
      const data = await api.getPositions();
      if (!data?.data) {
        console.log("❌ 获取持仓失败，请检查认证");
        return;
      }
      const positions = Array.isArray(data.data) ? data.data : [];
      console.log(`\n=== 💼 我的持仓 (${positions.length} 个) ===\n`);
      if (positions.length === 0) {
        console.log("  暂无持仓");
      } else {
        positions.forEach((p, i) => {
          console.log(`${i + 1}. ${p.marketTitle || p.title || "Unknown"}`);
          console.log(`   数量: ${p.amount || p.size || "?"} | 方向: ${p.outcome || p.side || "?"}`);
        });
      }
      console.log("");
      return positions;
    },

    async orders() {
      const data = await api.getOrders();
      if (!data?.data) {
        console.log("❌ 获取订单失败，请检查认证");
        return;
      }
      const orders = Array.isArray(data.data) ? data.data : [];
      console.log(`\n=== 📋 我的挂单 (${orders.length} 个) ===\n`);
      if (orders.length === 0) {
        console.log("  暂无挂单");
      } else {
        orders.forEach((o, i) => {
          const price = o.price || o.pricePerShare || "?";
          const displayPrice = +price > 1 ? (+price / 1e18).toFixed(4) : (+price * 100).toFixed(1) + "¢";
          console.log(`${i + 1}. [${o.id || o.orderId}]`);
          console.log(`   方向: ${o.side || "?"} | 价格: ${displayPrice} | 数量: ${o.amount || o.makerAmount || "?"}`);
        });
      }
      console.log("");
      return orders;
    },

    analyze() {
      return page.analyze();
    },

    // --- 交易 ---
    async buy(side, price, amount) {
      return trader.placeOrderViaUI(side, price, amount);
    },

    async cancel(orderId) {
      return trader.cancelOrder(orderId);
    },

    async cancelAll() {
      return trader.cancelAllOrders();
    },

    async grid(side, centerPrice, spread, levels, amountPerLevel) {
      return trader.gridOrders(side, centerPrice, spread, levels, amountPerLevel);
    },

    // --- 设置 ---
    toggleMode() {
      CONFIG.DRY_RUN = !CONFIG.DRY_RUN;
      console.log(
        CONFIG.DRY_RUN
          ? "🧪 已切换到【模拟模式】- 不会真正下单"
          : "💰 已切换到【实盘模式】- 操作将真实执行！请谨慎！"
      );
      panel.refresh();
    },

    showPanel() {
      panel.show();
    },
    hidePanel() {
      panel.hide();
    },
    refresh() {
      panel.refresh();
    },
  };

  // ==================== 启动 ====================

  // 启动嗅探器
  sniffer.start();

  // 尝试从 cookie/localStorage 获取已有的 API Key
  try {
    const keys = Object.keys(localStorage);
    for (const k of keys) {
      const v = localStorage.getItem(k);
      if (v && v.length > 20 && v.length < 100 && /^[a-f0-9-]+$/i.test(v)) {
        // 可能是 API key
        if (k.toLowerCase().includes("api") || k.toLowerCase().includes("key")) {
          sniffer.apiKey = v;
          console.log("🔑 从 localStorage 找到可能的 API Key:", v.slice(0, 8) + "...");
          break;
        }
      }
    }
  } catch (_) {}

  // 注册到全局
  window.pft = pft;

  // 显示面板
  panel.show();

  // 打印欢迎信息
  console.log(`
╔══════════════════════════════════════════════════════════════╗
║         📊 Predict.fun F12 Trader v2.0 已就绪               ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  当前模式: 🧪 模拟模式（不会真正下单）                       ║
║                                                              ║
║  快速开始:                                                   ║
║    pft.help()              查看所有命令                      ║
║    pft.orderbook()         查看订单簿                        ║
║    pft.buy("yes", 0.45, 10)  模拟买入                       ║
║    pft.toggleMode()        切换到实盘                        ║
║                                                              ║
║  状态面板已显示在右上角，可拖动                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
  `);
})();
