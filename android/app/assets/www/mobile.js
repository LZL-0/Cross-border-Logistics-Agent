/* App 端适配层：拦截 /api 请求转本地引擎；无后端也能跑完整功能 */
(function () {
  "use strict";

  var nativeFetch = window.fetch ? window.fetch.bind(window) : null;

  function parseQuery(search) {
    var out = {};
    if (!search) return out;
    search.replace(/^\?/, "").split("&").forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split("=");
      out[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
    });
    return out;
  }

  function jsonResponse(obj) {
    var text = JSON.stringify(obj);
    if (typeof Response === "function") {
      return new Response(text, {
        status: 200,
        headers: { "Content-Type": "application/json; charset=utf-8" }
      });
    }
    // 老版本 WebView 没有 Fetch API 时，给出最小可用的兼容响应
    return {
      ok: true,
      status: 200,
      json: function () { return Promise.resolve(obj); },
      text: function () { return Promise.resolve(text); }
    };
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var idx = url.indexOf("/api/");
    if (idx < 0) return nativeFetch ? nativeFetch(input, init) : Promise.reject(new Error("fetch 不可用"));

    var path = url.slice(idx, url.indexOf("?", idx) > 0 ? url.indexOf("?", idx) : url.length);
    var params = parseQuery(url.indexOf("?") >= 0 ? url.slice(url.indexOf("?")) : "");
    var body = {};
    if (init && init.body) {
      try { body = JSON.parse(init.body); } catch (e) { body = {}; }
    }
    if (!init || !init.method || init.method.toUpperCase() === "GET") params.method = "GET";
    else params.method = init.method.toUpperCase();

    var data;
    try {
      data = window.Engine.handleApi(path, params, body);
    } catch (e) {
      data = { ok: false, error: String(e && e.message ? e.message : e) };
    }

    // 导出报价单时顺带把 CSV 落到手机下载目录
    if (path === "/api/export" && data && data.ok && data.data && data.data.csv) {
      try {
        if (window.AndroidBridge && window.AndroidBridge.saveCsv) {
          var saved = window.AndroidBridge.saveCsv("报价单_" + data.data.quote_id + ".csv", data.data.csv);
          if (saved && window.AndroidBridge.toast) window.AndroidBridge.toast(saved);
        }
      } catch (e) { /* 保存失败不影响接口返回 */ }
      delete data.data.csv;
    }

    return Promise.resolve(jsonResponse(data));
  };

  /* ---------- 界面适配 ---------- */
  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  ready(function () {
    // 头部标注离线模式
    var brandLine = document.querySelector(".brand-line");
    if (brandLine) {
      var chip = document.createElement("span");
      chip.className = "mode-chip";
      chip.textContent = "离线";
      chip.title = "数据与算力全部在手机本地，无需电脑";
      brandLine.appendChild(chip);
    }
    // 隐藏「配置」标签：手机端由「设置」标签调用系统对话框替代
    var cfgBtn = document.querySelector('.tab-btn[data-tab="config"]');
    if (cfgBtn) cfgBtn.style.display = "none";
    var cfgTab = document.getElementById("tab-config");
    if (cfgTab) cfgTab.style.display = "none";

    // 追加「设置」标签，打开原生连接方式对话框
    var tabs = document.getElementById("tabs");
    if (tabs) {
      var setBtn = document.createElement("button");
      setBtn.className = "tab-btn";
      setBtn.id = "btn-settings";
      setBtn.textContent = "设置";
      setBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (window.AndroidBridge && window.AndroidBridge.openSettings) {
          window.AndroidBridge.openSettings();
        } else if (window.AndroidBridge && window.AndroidBridge.toast) {
          window.AndroidBridge.toast("当前环境不支持设置面板");
        }
      });
      tabs.appendChild(setBtn);
    }

    // 保存配置在离线模式下给出说明
    var save = document.getElementById("cfg-save");
    if (save) {
      save.addEventListener("click", function () {
        setTimeout(function () {
          var t = document.getElementById("toast");
          if (t) t.textContent = "离线版不读取模型 Key，全部走内置规则引擎";
        }, 300);
      });
    }
    window.Engine.logEvent({ type: "app_open" });
  });
})();
