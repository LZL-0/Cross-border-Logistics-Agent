/* 跨境物流引擎 · 原生单页前端 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => {
    const v = Number(n);
    return Number.isFinite(v) ? v.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) : "—";
  };
  const num = (n, d = 1) => {
    const v = Number(n);
    return Number.isFinite(v) ? v.toFixed(d).replace(/\.0$/, "") : "—";
  };

  /* ---------------- 实时参考块（四个板块共用） ---------------- */
  function renderLive(live, landed) {
    if (!live) return "";
    const head = `<h3>实时参考${live.as_of ? `<span class="live-meta">　取数 ${esc(live.as_of)}</span>` : ""}</h3>`;
    if (live.enabled === false) {
      return `<div class="live-block">${head}<div class="hint">实时取数已关闭，以上结果全部为知识库基线。</div></div>`;
    }
    const m = live.market || {};
    const scfi = m.scfi;
    const fx = m.fx;
    const chips = [];
    if (scfi && scfi.current) {
      const sign = Number(scfi.change) > 0 ? "+" : "";
      chips.push(
        `<span class="live-chip">SCFI ${num(scfi.current.value, 2)}（${esc(scfi.current.period)}）${esc(scfi.trend || "")} ${sign}${num(scfi.change_pct, 2)}%</span>`,
      );
    } else {
      chips.push('<span class="live-chip">SCFI 取数失败</span>');
    }
    if (fx && fx.rates && fx.rates.CNY) {
      chips.push(`<span class="live-chip">USD/CNY ${Number(fx.rates.CNY).toFixed(4)}</span>`);
    } else {
      chips.push('<span class="live-chip">汇率取数失败</span>');
    }
    if (m.stale) chips.push('<span class="pill warn">行情为上次成功值兜底</span>');

    const conv =
      landed && fx && fx.rates && fx.rates.CNY && Number(landed)
        ? `<div class="hint">按实时汇率折算：${money(landed)} 元 ≈ USD ${money(Number(landed) / Number(fx.rates.CNY))}</div>`
        : "";

    const news = (live.news || [])
      .slice(0, 6)
      .map(
        (n) => `<li><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title || n.url)}</a>
          <div class="live-meta">${esc(n.topic || "")}${n.published ? " · " + esc(String(n.published).slice(0, 20)) : ""}</div></li>`,
      )
      .join("");
    const errs = (live.errors || [])
      .slice(0, 2)
      .map((e) => `<div class="hint">降级：${esc(String(e).slice(0, 90))}</div>`)
      .join("");

    return `<div class="live-block">${head}
      <div class="actions" style="margin:0">${chips.join(" ")}</div>
      ${conv}
      ${news ? `<ul>${news}</ul>` : '<div class="hint">本次未取到资讯。</div>'}
      ${errs}
      <div class="hint">${esc(live.disclaimer || "实时数据仅作行情参考。")}</div>
    </div>`;
  }

  /* ---------------- tabs ---------------- */
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      btn.classList.add("active");
      $("tab-" + btn.dataset.tab).classList.add("active");
      if (btn.dataset.tab === "debug") loadDebug();
    });
  });

  /* ---------------- toast ---------------- */
  let toastTimer = null;
  function toast(msg) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  /* ---------------- http ---------------- */
  async function getJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }
  async function postJSON(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  /* ---------------- session ---------------- */
  const SESSION_KEY = "logi_session";
  let sessionId = localStorage.getItem(SESSION_KEY) || "";
  if (!sessionId) {
    sessionId = Math.random().toString(36).slice(2, 10);
    localStorage.setItem(SESSION_KEY, sessionId);
  }
  const showSession = () => ($("session-label").textContent = `会话 ${sessionId}`);
  showSession();

  /* ---------------- chat ---------------- */
  const messagesEl = $("messages");

  function addMessage(role, text, trace) {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    if (trace && trace.length) bubble.appendChild(renderTrace(trace));
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function renderTrace(trace) {
    const box = document.createElement("div");
    box.className = "trace";
    const okCount = trace.filter((t) => t.ok !== false).length;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `执行明细：${trace.length} 步（${okCount} 成功）`;
    details.appendChild(summary);

    const table = document.createElement("table");
    table.innerHTML =
      "<thead><tr><th>轮次</th><th>工具</th><th>状态</th><th>参数</th><th>结果摘要</th></tr></thead>";
    const tbody = document.createElement("tbody");
    trace.forEach((t) => {
      const tr = document.createElement("tr");
      const status = t.ok === false ? '<span class="pill bad">失败</span>' : '<span class="pill ok">成功</span>';
      tr.innerHTML = `<td>${esc(t.round ?? "")}</td><td>${esc(t.action)}</td><td>${status}</td>
        <td>${esc(JSON.stringify(t.args || {}).slice(0, 80))}</td>
        <td>${esc(String(t.result || "").slice(0, 120))}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    details.appendChild(table);
    box.appendChild(details);
    return box;
  }

  async function sendChat() {
    const input = $("chat-input");
    const text = input.value.trim();
    if (!text) return;
    addMessage("user", text, null);
    input.value = "";
    const btn = $("chat-send");
    btn.disabled = true;
    try {
      const data = await postJSON("/api/ask", { text, session_id: sessionId });
      addMessage("assistant", data.answer || "（无回答）", data.trace || []);
      lastTrace = data.trace || [];
      lastAgent = data.agent || "";
      renderLastTrace();
    } catch (err) {
      addMessage("assistant", `请求失败：${err.message}`, null);
    } finally {
      btn.disabled = false;
    }
  }

  $("chat-send").addEventListener("click", sendChat);
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $("chat-reset").addEventListener("click", () => {
    sessionId = Math.random().toString(36).slice(2, 10);
    localStorage.setItem(SESSION_KEY, sessionId);
    showSession();
    messagesEl.innerHTML = "";
    toast("已重置会话（工具预算同时重置）");
  });

  /* ---------------- quote ---------------- */
  function quoteBody(prefix) {
    return {
      country: $(prefix + "-country").value,
      mode: $(prefix + "-mode").value,
      weight_kg: Number($(prefix + "-weight").value || 1),
      pieces: Number($(prefix + "-pieces").value || 1),
      length: Number($(prefix + "-len").value || 0),
      width: Number($(prefix + "-wid").value || 0),
      height: Number($(prefix + "-hei").value || 0),
      declared_value: Number($(prefix + "-value").value || 0),
      hs: $(prefix + "-hs").value.trim(),
      prefer: $(prefix + "-prefer") ? $(prefix + "-prefer").value : "balance",
    };
  }

  function renderQuote(res) {
    const box = $("q-result");
    if (!res.ok) {
      box.innerHTML = `<pre class="output">${esc(res.error)}</pre>`;
      return;
    }
    const d = res.data;
    if (!d.quotes || !d.quotes.length) {
      box.innerHTML = `<p class="hint">${esc(d.message || "没有匹配的承运商")}</p>`;
      return;
    }
    const rows = d.quotes
      .map((q) => {
        const flags = (q.flags || []).map((f) => `<span class="pill warn">${esc(f)}</span>`).join("");
        return `<tr>
          <td><b>${esc(q.carrier)}</b><div class="hint">${esc(q.mode_name)}</div>${flags}</td>
          <td class="num">${money(q.total)}${q.agency_fee ? `<div class="hint">+ 代理 ${money(q.agency_fee)}</div>` : ""}</td>
          <td class="num">${money(q.tax)}</td>
          <td class="num"><b>${money(q.landed_total)}</b></td>
          <td class="num">${num(q.eta_days[0])}-${num(q.eta_days[1])} 天<div class="hint">${esc(q.eta_date)}</div></td>
          <td class="num">${(q.ontime * 100).toFixed(0)}%</td>
          <td class="num">${q.score}</td>
          <td><button class="ghost" data-export="${esc(
            encodeURIComponent(
              JSON.stringify({
                country: d.query.country,
                carrier: q.carrier,
                mode: q.mode,
                weight_kg: d.query.weight_kg,
                declared_value: d.query.declared_value,
                freight_total: q.total,
                tax: q.tax,
                landed_total: q.landed_total,
                eta_date: q.eta_date,
              }),
            ),
          )}">导出</button></td>
        </tr>`;
      })
      .join("");

    const excluded = (d.excluded || [])
      .slice(0, 6)
      .map((x) => `<span class="pill">${esc(x.carrier)}：${esc(x.reason)}</span>`)
      .join("");

    box.innerHTML = `
      <div class="stat-row" style="margin-bottom:12px">
        <div class="stat"><b>${d.count}</b><span>可选承运商</span></div>
        <div class="stat"><b>${money(d.recommendation.landed_total)}</b><span>推荐总成本（元）</span></div>
        <div class="stat"><b>${esc(d.recommendation.carrier)}</b><span>推荐承运商</span></div>
        <div class="stat"><b>${esc(d.recommendation.eta_date)}</b><span>预计到货</span></div>
      </div>
      <table>
        <thead><tr><th>承运商 / 方式</th><th>运费</th><th>税费</th><th>总成本</th><th>时效 / 到货</th><th>准点</th><th>评分</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="hint" style="margin-top:10px">推荐理由：${esc(d.recommendation.reason)}<br/>${esc(d.recommendation.tradeoff)}</p>
      ${excluded ? `<p class="hint">未纳入：${excluded}</p>` : ""}
      ${renderLive(d.live, d.recommendation.landed_total)}
    `;

    box.querySelectorAll("button[data-export]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const payload = JSON.parse(decodeURIComponent(btn.dataset.export));
        payload.note = "询价页导出";
        btn.disabled = true;
        try {
          const out = await postJSON("/api/export", payload);
          if (out.ok) toast(`已导出报价单 ${out.data.quote_id}`);
          else toast(`导出失败：${out.error}`);
        } catch (err) {
          toast(`导出失败：${err.message}`);
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  $("q-run").addEventListener("click", async () => {
    const box = $("q-result");
    box.innerHTML = `<div class="hint">比价中…</div>`;
    try {
      renderQuote(await postJSON("/api/quote", quoteBody("q")));
    } catch (err) {
      box.innerHTML = `<pre class="output">${esc(err.message)}</pre>`;
    }
  });

  /* ---------------- clearance ---------------- */
  function renderClearance(res) {
    const box = $("c-result");
    if (!res.ok) {
      box.innerHTML = `<pre class="output">${esc(res.error)}</pre>`;
      return;
    }
    const d = res.data;
    const breakdown = d.breakdown
      .map((b) => `<tr><td>${esc(b.item)}</td><td class="num">${b.days > 0 ? "+" : ""}${num(b.days, 1)} 天</td></tr>`)
      .join("");
    box.innerHTML = `
      <div class="actions" style="margin-top:0">
        <span class="badge">${num(d.clearance_days[0])}-${num(d.clearance_days[1])} 个工作日</span>
        <span class="hint">置信度 ${esc(d.confidence)}｜尾程另计 ${num(d.tail_days)} 天</span>
      </div>
      <h3>影响因素</h3>
      <table><thead><tr><th>因素</th><th>影响</th></tr></thead><tbody>${breakdown || '<tr><td colspan="2" class="hint">无额外因素</td></tr>'}</tbody></table>
      ${d.missing_docs.length ? `<h3>缺失单证</h3><p>${d.missing_docs.map((x) => `<span class="pill bad">${esc(x)}</span>`).join("")}</p>` : '<h3>单证</h3><p class="hint">按已备材料判断无缺口</p>'}
      ${d.risks.length ? `<h3>风险</h3><p>${d.risks.map((x) => `<span class="pill warn">${esc(x)}</span>`).join("")}</p>` : ""}
      <p class="hint">${esc(d.note)}</p>
      ${renderLive(d.live)}
    `;
  }

  $("c-run").addEventListener("click", async () => {
    const docs = [...document.querySelectorAll(".c-doc:checked")].map((x) => x.value);
    const body = {
      country: $("c-country").value,
      mode: $("c-mode").value,
      hs: $("c-hs").value.trim(),
      declared_value: Number($("c-value").value || 0),
      port: $("c-port").value.trim(),
      docs_provided: docs,
    };
    const box = $("c-result");
    box.innerHTML = `<div class="hint">估算中…</div>`;
    try {
      renderClearance(await postJSON("/api/clearance", body));
    } catch (err) {
      box.innerHTML = `<pre class="output">${esc(err.message)}</pre>`;
    }
  });

  /* ---------------- compliance ---------------- */
  async function runCompliance() {
    const box = $("k-result");
    box.innerHTML = `<div class="hint">查询中…</div>`;
    const keyword = $("k-keyword").value.trim();
    const hs = $("k-hs").value.trim();
    try {
      const res = await getJSON(`/api/hs?keyword=${encodeURIComponent(keyword)}&hs=${encodeURIComponent(hs)}`);
      const data = res.data || res;
      if (!data.found || !data.candidates.length) {
        box.innerHTML = `<p class="hint">未匹配到品类，试试：服装 / 手机 / 玩具 / 锂电池 / 家具 / 化妆品，或直接输入 10 位 HS。</p>`;
        return;
      }
      const cards = data.candidates
        .map(
          (c) => `<tr>
            <td><b>${esc(c.name)}</b><div class="hint">HS ${esc(c.hs)}</div></td>
            <td>${c.risk === "high" ? '<span class="pill bad">高风险</span>' : c.risk === "medium" ? '<span class="pill warn">中风险</span>' : '<span class="pill ok">低风险</span>'}
              ${c.dg ? '<span class="pill bad">危险品</span>' : ""}</td>
            <td>${esc((c.certs || []).join("、") || "无")}</td>
            <td><button class="ghost" data-hs="${esc(c.hs)}">测算税费</button></td>
          </tr>`,
        )
        .join("");

      box.innerHTML = `<table><thead><tr><th>品类 / HS</th><th>风险</th><th>认证要求</th><th>操作</th></tr></thead><tbody>${cards}</tbody></table><div id="k-tax"></div>`;

      box.querySelectorAll("button[data-hs]").forEach((btn) => {
        btn.addEventListener("click", () => loadDuty(btn.dataset.hs));
      });
      if (data.candidates.length === 1) loadDuty(data.candidates[0].hs);
    } catch (err) {
      box.innerHTML = `<pre class="output">${esc(err.message)}</pre>`;
    }
  }

  async function loadDuty(hs) {
    const slot = $("k-tax");
    if (!slot) return;
    slot.innerHTML = `<div class="hint">测算中…</div>`;
    const country = $("k-country").value;
    const value = Number($("k-value").value || 0);
    try {
      const res = await getJSON(
        `/api/duty?hs=${encodeURIComponent(hs)}&country=${country}&declared_value=${value}`,
      );
      if (!res.ok) {
        slot.innerHTML = `<pre class="output">${esc(res.error)}</pre>`;
        return;
      }
      const d = res.data;
      slot.innerHTML = `
        <h3>税费测算（${esc(d.country_name)}）</h3>
        <div class="stat-row">
          <div class="stat"><b>${money(d.duty)}</b><span>关税（${(d.duty_rate * 100).toFixed(1)}%）</span></div>
          <div class="stat"><b>${money(d.vat)}</b><span>${esc(d.vat_name)}（${(d.vat_rate * 100).toFixed(1)}%）</span></div>
          <div class="stat"><b>${money(d.total_tax)}</b><span>税费合计</span></div>
          <div class="stat"><b>${money(d.landed_cost)}</b><span>到岸成本</span></div>
        </div>
        ${d.duty_exempt ? '<p class="hint">申报货值低于免税额，关税免征。</p>' : ""}
        <p class="hint">单证：${esc((d.docs || []).join("、"))}<br/>${esc(d.note)}</p>
        ${renderLive(d.live, d.landed_cost)}
      `;
    } catch (err) {
      slot.innerHTML = `<pre class="output">${esc(err.message)}</pre>`;
    }
  }

  $("k-run").addEventListener("click", runCompliance);

  /* ---------------- plan ---------------- */
  function renderPlan(res) {
    const box = $("p-result");
    if (!res.ok || !res.data || res.data.ok === false) {
      box.innerHTML = `<pre class="output">${esc(res.error || (res.data && res.data.message) || "方案生成失败")}</pre>`;
      return;
    }
    const d = res.data;
    const rec = d.recommendation;
    const alts = (d.alternatives || [])
      .map(
        (a) =>
          `<tr><td>${esc(a.carrier)}</td><td>${esc(a.mode)}</td><td class="num">${money(a.landed_total)}</td>
           <td class="num">${esc(a.eta_date)}</td><td class="num">${a.score}</td></tr>`,
      )
      .join("");
    const timeline = d.timeline
      .map((t) => `<tr><td>${esc(t.stage)}</td><td class="num">第 ${t.start}-${t.end} 天</td><td class="num">${esc(t.date)}</td><td class="hint">${esc(t.note)}</td></tr>`)
      .join("");
    const docs = (d.docs.required || []).map((x) => `<span class="pill ok">${esc(x)}</span>`).join("");
    const missing = (d.docs.missing || []).map((x) => `<span class="pill bad">${esc(x)}</span>`).join("");

    box.innerHTML = `
      <div class="stat-row" style="margin-bottom:12px">
        <div class="stat"><b>${esc(rec.carrier)}</b><span>推荐承运商</span></div>
        <div class="stat"><b>${money(rec.landed_total)}</b><span>总成本（元）</span></div>
        <div class="stat"><b>${num(d.chosen_quote.eta_days[0])}-${num(d.chosen_quote.eta_days[1])} 天</b><span>预计 ${esc(rec.eta_date)} 签收</span></div>
        <div class="stat"><b>${num(d.clearance.clearance_days[0])}-${num(d.clearance.clearance_days[1])}</b><span>清关工作日</span></div>
      </div>
      <p class="hint">${esc(rec.reason)}｜${esc(rec.tradeoff)}</p>
      <h3>备选</h3>
      <table><thead><tr><th>承运商</th><th>方式</th><th>总成本</th><th>到货</th><th>评分</th></tr></thead><tbody>${alts}</tbody></table>
      <h3>税费</h3><p>${esc(d.duty_summary)}</p>
      <h3>单证</h3><p>${docs}${missing}</p>
      <h3>时间线</h3>
      <table><thead><tr><th>阶段</th><th>时间</th><th>节点</th><th>说明</th></tr></thead><tbody>${timeline}</tbody></table>
      ${d.risks.length ? `<h3>风险</h3><p>${d.risks.map((x) => `<span class="pill warn">${esc(x)}</span>`).join("")}</p>` : ""}
      ${d.advice.length ? `<h3>优化建议</h3><ul>${d.advice.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <p class="hint">口径：${esc((d.assumptions || []).slice(0, 3).join("；"))}</p>
      ${renderLive(d.live, rec.landed_total)}
    `;
  }

  $("p-run").addEventListener("click", async () => {
    const body = quoteBody("p");
    const box = $("p-result");
    box.innerHTML = `<div class="hint">生成中…</div>`;
    try {
      renderPlan(await postJSON("/api/plan", body));
    } catch (err) {
      box.innerHTML = `<pre class="output">${esc(err.message)}</pre>`;
    }
  });

  /* ---------------- debug ---------------- */
  let lastTrace = [];
  let lastAgent = "";

  function renderLastTrace() {
    const box = $("d-trace");
    if (!lastTrace.length) {
      box.innerHTML = `<div class="hint">还没有调用</div>`;
      return;
    }
    box.innerHTML = `<p class="hint">智能体：${esc(lastAgent || "—")}</p><table>
      <thead><tr><th>轮次</th><th>工具</th><th>状态</th><th>参数</th><th>结果</th></tr></thead>
      <tbody>${lastTrace
        .map(
          (t) => `<tr><td>${esc(t.round)}</td><td>${esc(t.action)}</td>
          <td>${t.ok === false ? '<span class="pill bad">失败</span>' : '<span class="pill ok">成功</span>'}</td>
          <td>${esc(JSON.stringify(t.args || {}).slice(0, 100))}</td>
          <td>${esc(String(t.result || "").slice(0, 160))}</td></tr>`,
        )
        .join("")}</tbody></table>`;
  }

  async function loadDebug() {
    try {
      const stats = await getJSON("/api/stats");
      const k = stats.knowledge || {};
      const q = stats.quotes || { total: 0 };
      $("d-stats").innerHTML = `
        <div class="stat"><b>${k.countries ?? 0}</b><span>目的国</span></div>
        <div class="stat"><b>${k.hs ?? 0}</b><span>HS 品类</span></div>
        <div class="stat"><b>${k.carriers ?? 0}</b><span>承运商</span></div>
        <div class="stat"><b>${q.total ?? 0}</b><span>已导出报价</span></div>
        <div class="stat"><b>${esc(stats.model)}</b><span>模型</span></div>
        <div class="stat"><b>${esc(stats.permission_mode)}</b><span>权限模式</span></div>
        <div class="stat"><b>${esc(stats.net_policy)}</b><span>网络策略</span></div>
      `;
      renderLastTrace();

      const tail = await getJSON("/api/debug/tail?n=30");
      $("d-tail").textContent = (tail.events || []).map((e) => JSON.stringify(e)).join("\n") || "（暂无遥测）";

      const quotes = await getJSON("/api/quotes?limit=10");
      const rows = (quotes.data && quotes.data.rows) || [];
      $("d-quotes").innerHTML = rows.length
        ? `<table><thead><tr><th>报价号</th><th>时间</th><th>国家</th><th>承运商</th><th>总成本</th><th>到货</th></tr></thead>
           <tbody>${rows
             .slice()
             .reverse()
             .map(
               (r) => `<tr><td>${esc(r.quote_id)}</td><td>${esc(r.created_at)}</td><td>${esc(r.country)}</td>
               <td>${esc(r.carrier)}</td><td class="num">${money(r.landed_total)}</td><td>${esc(r.eta_date)}</td></tr>`,
             )
             .join("")}</tbody></table>`
        : '<div class="hint">暂无</div>';
    } catch (err) {
      $("d-stats").innerHTML = `<div class="hint">加载失败：${esc(err.message)}</div>`;
    }
  }

  $("d-refresh").addEventListener("click", loadDebug);

  /* ---------------- config ---------------- */
  async function loadConfig() {
    try {
      const cfg = await getJSON("/api/config");
      const env = cfg.env || {};
      $("cfg-model").value = env.LLM_MODEL || "";
      $("cfg-base").value = env.LLM_BASE_URL || "";
      $("cfg-key").value = "";
      $("cfg-key").placeholder = env.LLM_API_KEY_SET ? "已配置（留空则不修改）" : "未配置（走规则兜底）";
      $("cfg-perm").value = env.AGENT_PERMISSION_MODE || "plan";
      $("cfg-net").value = env.TOOL_NET_POLICY || "allowlist";
      $("cfg-limit").value = env.MAX_TOOL_CALLS || "12";
      $("rt-note").textContent = env.REALTIME_ENABLED === "1"
        ? "实时接入已开启：检索 360 / 汇率 open.er-api / 指数 SCFI"
        : "实时接入已关闭（REALTIME_ENABLED=0），全部走知识库基线";
    } catch (err) {
      toast(`读取配置失败：${err.message}`);
    }
  }

  $("cfg-save").addEventListener("click", async () => {
    const values = {
      LLM_MODEL: $("cfg-model").value.trim(),
      LLM_BASE_URL: $("cfg-base").value.trim(),
      AGENT_PERMISSION_MODE: $("cfg-perm").value,
      TOOL_NET_POLICY: $("cfg-net").value,
      MAX_TOOL_CALLS: $("cfg-limit").value.trim(),
    };
    const key = $("cfg-key").value.trim();
    if (key) values.LLM_API_KEY = key;
    try {
      const res = await postJSON("/api/config", { values });
      toast(res.ok ? "已保存到 .env，重启服务后生效" : "保存失败");
      if (res.ok) $("cfg-key").value = "";
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  });

  /* ---------------- 实时接入自检 ---------------- */
  const rtRow = (label, check, render) => {
    const ok = check && check.ok;
    const pill = ok ? '<span class="pill ok">可用</span>' : '<span class="pill bad">异常</span>';
    const lat = check && check.latency_ms != null ? `${check.latency_ms}ms` : "";
    const body = ok ? render(check.detail || {}) : esc((check && check.error) || "未检测");
    return `<tr><td>${esc(label)}</td><td>${pill}</td><td class="num">${lat}</td><td>${body}</td></tr>`;
  };

  async function checkRealtime() {
    $("rt-result").innerHTML = '<div class="hint">检测中…（模型探测约 1–3 秒）</div>';
    try {
      const res = await getJSON("/api/realtime/health");
      const c = res.checks || {};
      $("rt-time").textContent = `检测时间 ${res.time}　检测源：${res.sources.search}`;
      $("rt-result").innerHTML = `<table><thead><tr><th>链路</th><th>状态</th><th>耗时</th><th>结果</th></tr></thead><tbody>
        ${rtRow("DeepSeek 模型", c.llm, (d) => `${esc(d.model || "")} → “${esc(d.reply || "")}”`)}
        ${rtRow("联网检索", c.search, (d) => `${esc(d.engine || "")} · ${d.count || 0} 条 · 首条：${esc((d.items && d.items[0] && d.items[0].title) || "")}`)}
        ${rtRow("实时汇率", c.fx, (d) => `${esc(d.base || "")}→CNY ${esc(String((d.rates && d.rates.CNY) || ""))}　更新 ${esc(d.updated_utc || "")}`)}
        ${rtRow("SCFI 运价指数", c.scfi, (d) => `${esc(String((d.current && d.current.value) || ""))}（${esc((d.current && d.current.period) || "")}）　${esc(d.trend || "")} ${esc(String(d.change_pct || 0))}%`)}
      </tbody></table>`;
    } catch (err) {
      $("rt-result").innerHTML = `<div class="hint">检测失败：${esc(err.message)}</div>`;
    }
  }

  $("rt-check").addEventListener("click", checkRealtime);

  /* ---------------- 知识库周更 ---------------- */
  function renderKb(p) {
    if (!p) {
      $("kb-result").innerHTML = '<div class="hint">还没有周报：点「生成本周周报」先生成候选。</div>';
      return;
    }
    const m = (p.market && p.market.proposed) || {};
    const scfi = m.scfi || {};
    const fx = (m.fx && m.fx.rates) || {};
    const rows = ((p.ports && p.ports.proposed) || [])
      .map(
        (c) => `<tr><td>${esc(c.name)}</td><td>${esc(c.code)}</td><td>${num(c.current, 1)}</td>
          <td>${num(c.proposed, 1)}</td><td>${c.delta > 0 ? "+" : ""}${num(c.delta, 1)}</td>
          <td>${esc(c.confidence)}</td>
          <td>${esc(c.reason)}${c.evidence && c.evidence[0] && c.evidence[0].url ? ` · <a href="${esc(c.evidence[0].url)}" target="_blank" rel="noopener">来源</a>` : ""}</td></tr>`,
      )
      .join("");
    const status = p.applied_at
      ? `<span class="pill ok">已于 ${esc(p.applied_at)} 写入</span>`
      : '<span class="pill warn">待确认</span>';
    $("kb-time").textContent = `生成于 ${p.generated_at}（${p.trigger === "scheduler" ? "定时" : "手动"}）`;
    $("kb-result").innerHTML = `
      <div class="actions" style="margin:0">${status}
        <span class="live-chip">SCFI ${num(scfi.value, 2)}（${esc(scfi.period || "—")}）${esc(scfi.trend || "")}</span>
        <span class="live-chip">USD/CNY ${fx.CNY ? Number(fx.CNY).toFixed(4) : "—"}</span>
        <span class="hint">${esc(p.report || "")}</span>
      </div>
      ${
        rows
          ? `<table><thead><tr><th>口岸</th><th>代码</th><th>当前</th><th>候选</th><th>变化</th><th>置信</th><th>依据</th></tr></thead><tbody>${rows}</tbody></table>`
          : '<div class="hint">本周口岸无候选调整（资讯里没有明确的拥堵变化信号）。</div>'
      }
      ${p.errors && p.errors.length ? `<div class="hint">降级：${esc(p.errors.slice(0, 2).join(" | ").slice(0, 160))}</div>` : ""}`;
  }

  async function loadKb() {
    try {
      const res = await getJSON("/api/kb/proposal");
      renderKb(res.ok ? res.data : null);
    } catch (err) {
      $("kb-result").innerHTML = `<div class="hint">读取周报失败：${esc(err.message)}</div>`;
    }
  }

  $("kb-run").addEventListener("click", async () => {
    $("kb-result").innerHTML = '<div class="hint">抓取行情与口岸资讯中…（约 5–15 秒）</div>';
    try {
      const res = await postJSON("/api/kb/run", {});
      if (!res.ok) throw new Error(res.error || "生成失败");
      renderKb(res.data);
    } catch (err) {
      $("kb-result").innerHTML = `<div class="hint">生成失败：${esc(err.message)}</div>`;
    }
  });

  $("kb-apply").addEventListener("click", async () => {
    if (!window.confirm("确认把最新周报写入知识库？会先备份 ports.json 与 market_baseline.json。")) return;
    try {
      const res = await postJSON("/api/kb/apply?confirm=true", {});
      if (!res.ok) throw new Error(res.error || "写入失败");
      const d = res.data || {};
      $("kb-result").innerHTML = `<div class="hint">已写入：口岸 ${d.ports_applied} 条、行情 ${d.market_written ? "1 份" : "未写入"}；备份在 ${esc(d.backup || "")}</div>`;
      loadKb();
    } catch (err) {
      $("kb-result").innerHTML = `<div class="hint">写入失败：${esc(err.message)}</div>`;
    }
  });

  /* ---------------- 实时行情条 ---------------- */
  async function refreshMarket(force) {
    try {
      const res = await getJSON("/api/realtime/market" + (force ? "?force=true" : ""));
      if (!res.ok) throw new Error(res.error || "行情不可用");
      const m = res.data || {};
      const scfi = m.scfi;
      const fx = m.fx;
      $("ls-scfi").textContent =
        scfi && scfi.current
          ? `SCFI ${num(scfi.current.value, 2)}（${scfi.current.period}）${scfi.trend || ""} ${num(Math.abs(scfi.change_pct), 2)}%`
          : "SCFI 取数失败";
      $("ls-fx").textContent =
        fx && fx.rates && fx.rates.CNY ? `USD/CNY ${Number(fx.rates.CNY).toFixed(4)}` : "汇率取数失败";
      $("ls-time").textContent = m.as_of ? `更新于 ${m.as_of}${m.stale ? "（上次成功值）" : ""}` : "";
    } catch (err) {
      $("ls-time").textContent = `行情刷新失败：${err.message}`;
    }
  }
  $("ls-refresh").addEventListener("click", () => refreshMarket(true));

  /* ---------------- init ---------------- */
  addMessage(
    "assistant",
    "你好，我是跨境物流引擎的主控。可以帮你做：\n" +
      "· 多承运商比价（运费+关税+增值税的总成本）\n" +
      "· 目的国清关时效预估与风险提示\n" +
      "· HS 归类、税率与认证要求\n" +
      "· 端到端方案（承运商+单证+时间线）\n" +
      "直接说货物、重量、尺寸、货值和目的国即可。",
    null,
  );
  loadDebug();
  loadConfig();
  loadKb();
  refreshMarket();
  setInterval(() => refreshMarket(), 5 * 60 * 1000);
})();
