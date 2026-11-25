/* 跨境物流引擎 · 离线规则引擎
 * 由 logistics_agent/tools/{knowledge,clearance,rates,shipment}.py 与
 * Agents/{supervisor,quote,plan,clearance,compliance,summarizer} 的 classic 逻辑移植，
 * 口径保持一致；仅在移动端 App 内使用，不联网。
 */
(function (global) {
  "use strict";

  var KB = global.KB || { countries: [], hs: [], carriers: [], ports: [] };

  var MODE_CN = { express: "国际快递", air: "空运", ocean: "海运", lcl: "海运拼柜", fcl: "海运整柜", rail: "铁路" };
  var MODE_TAIL = { express: "express", air: "air", ocean: "ocean", lcl: "ocean", fcl: "ocean", rail: "rail" };
  var PREP_DAYS = { express: 1, air: 1, ocean: 3, lcl: 3, fcl: 3, rail: 3 };
  var TAIL_DAYS = { express: 1.0, air: 2.0, ocean: 3.0, lcl: 3.0, fcl: 3.0, rail: 2.5 };
  var SPREAD = { express: 0.8, air: 1.5, ocean: 2.5, rail: 2.0 };
  var PEAK_MONTHS = [9, 10, 11, 12];
  var AGENCY_CLEARANCE_FEE = 300;

  /* ---------------- 数值/格式化 ---------------- */
  function num(v, d) {
    var x = Number(v);
    if (!isFinite(x)) return 0;
    return d === undefined ? x : roundPy(x, d);
  }
  /* Python round() 是「四舍六入五成双」（round-half-even），JS 的 Math.round 是五入，
   * 这里按 Python 口径还原，保证 App 结果与后端逐分对齐。 */
  function roundPy(x, d) {
    if (!isFinite(x)) return 0;
    var m = Math.pow(10, d);
    var sign = x < 0 ? -1 : 1;
    var ax = Math.abs(x) * m;
    var fl = Math.floor(ax);
    var diff = ax - fl;
    var out;
    if (Math.abs(diff - 0.5) < 1e-9) out = (fl % 2 === 0) ? fl : fl + 1;
    else out = Math.round(ax);
    return sign * out / m;
  }
  function round2(v) { return roundPy(Number(v), 2); }
  function round1(v) { return roundPy(Number(v), 1); }
  function round4(v) { return roundPy(Number(v), 4); }
  function ceilTo(v, step) {
    if (step <= 0) return num(v, 3);
    return num(Math.ceil(num(v / step, 6)) * step, 3);
  }
  function money(v) {
    var x = Number(v);
    if (!isFinite(x)) return String(v);
    return Math.round(x).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  }
  function g(v) {
    var x = Number(v);
    if (!isFinite(x)) return String(v);
    return String(Number(x.toFixed(6)));
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function today() { var d = new Date(); d.setHours(12, 0, 0, 0); return d; }
  function addDays(n) { var d = today(); d.setDate(d.getDate() + n); return d; }
  function md(d) { return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); }
  function nowText() {
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
      p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }
  function hash10(s) {
    var h1 = 0x811c9dc5, h2 = 0x1000193;
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      h1 = ((h1 ^ c) * 16777619) >>> 0;
      h2 = ((h2 + c * (i + 7)) * 2654435761) >>> 0;
    }
    return (h1.toString(16) + h2.toString(16)).slice(0, 10);
  }

  /* ---------------- 知识库 ---------------- */
  function countries() { return KB.countries || []; }
  function hsItems() { return KB.hs || []; }
  function carriersData() { return KB.carriers || []; }
  function portsData() { return KB.ports || []; }

  function resolveCountry(value) {
    var text = String(value == null ? "" : value).trim();
    if (!text) return null;
    var upper = text.toUpperCase();
    var list = countries(), i;
    for (i = 0; i < list.length; i++) if (list[i].code.toUpperCase() === upper) return list[i];
    for (i = 0; i < list.length; i++) {
      if (list[i].name.indexOf(text) >= 0 || text.indexOf(list[i].name) >= 0) return list[i];
    }
    var alias = { "美线": "US", "美国站": "US", "欧洲": "DE", "欧线": "DE", "英国站": "GB", "日本站": "JP", "澳洲": "AU" };
    for (var key in alias) if (text.indexOf(key) >= 0) return resolveCountry(alias[key]);
    return null;
  }

  function findHsEntry(keyword, hs) {
    var code = String(hs || "").trim().replace(/\s+/g, "");
    var items = hsItems(), i, k;
    if (code) {
      for (i = 0; i < items.length; i++) {
        if (items[i].hs.indexOf(code) === 0 || code.indexOf(items[i].hs) === 0) return items[i];
      }
    }
    var low = String(keyword || "").trim().toLowerCase();
    if (!low) return null;
    for (i = 0; i < items.length; i++) {
      if (items[i].name.toLowerCase().indexOf(low) >= 0) return items[i];
      var kws = items[i].keywords || [];
      for (k = 0; k < kws.length; k++) {
        var kw = String(kws[k]).toLowerCase();
        if (kw.indexOf(low) >= 0 || low.indexOf(kw) >= 0) return items[i];
      }
    }
    return null;
  }

  function matchHsCandidates(keyword, hs, limit) {
    var code = String(hs || "").trim().replace(/\s+/g, "");
    var low = String(keyword || "").trim().toLowerCase();
    var items = hsItems(), hits = [], i;
    for (i = 0; i < items.length; i++) {
      if (code && (items[i].hs.indexOf(code) === 0 || code.indexOf(items[i].hs) === 0)) { hits.push(items[i]); continue; }
      if (low) {
        var hit = items[i].name.toLowerCase().indexOf(low) >= 0;
        if (!hit) {
          var kws = items[i].keywords || [];
          for (var k = 0; k < kws.length; k++) if (String(kws[k]).toLowerCase().indexOf(low) >= 0) { hit = true; break; }
        }
        if (hit) hits.push(items[i]);
      }
    }
    if (!hits.length && low) {
      for (i = 0; i < items.length; i++) {
        var kk = items[i].keywords || [], any = false;
        for (var j = 0; j < kk.length; j++) {
          if (String(kk[j]).length >= 2 && low.indexOf(kk[j]) >= 0) { any = true; break; }
        }
        if (any) hits.push(items[i]);
      }
    }
    return hits.slice(0, Math.max(1, Number(limit) || 5));
  }

  function computeDuty(hsEntry, country, declaredValue, freight) {
    var declared = Math.max(0, Number(declaredValue) || 0);
    var rate = 0.05;
    if (hsEntry) {
      var d = (hsEntry.duty || {})[country.code];
      rate = d === undefined ? 0.05 : Number(d);
    }
    var duty = 0.0;
    var dutyExempt = declared <= Number(country.duty_de_minimis_cny || 0);
    if (!dutyExempt) duty = declared * rate;
    var vatBase = declared + duty + Math.max(0, Number(freight) || 0);
    var vatRate = Number(country.vat_rate || 0);
    var vatExempt = (declared + Math.max(0, Number(freight) || 0)) <= Number(country.vat_de_minimis_cny || 0);
    var vat = vatExempt ? 0 : vatBase * vatRate;
    return {
      country: country.code,
      country_name: country.name,
      hs: hsEntry ? hsEntry.hs : "",
      declared_value: round2(declared),
      duty_rate: round4(rate),
      duty: round2(duty),
      duty_exempt: dutyExempt,
      duty_de_minimis: country.duty_de_minimis_cny || 0,
      vat_rate: round4(vatRate),
      vat: round2(vat),
      vat_name: country.vat_name || "VAT",
      total_tax: round2(duty + vat),
      landed_cost: round2(declared + duty + vat),
      note: "关税/增值税为演示基线，正式申报以目的国海关核定为准"
    };
  }

  function requiredDocs(hsEntry, country) {
    var docs = (country.docs || []).slice();
    if (hsEntry) {
      (hsEntry.certs || []).forEach(function (c) { if (docs.indexOf(c) < 0) docs.push(c); });
    }
    return docs;
  }

  function lookupHsBrief(keyword, hs, limit) {
    var cands = matchHsCandidates(keyword, hs, limit);
    if (!cands.length) return { found: false, candidates: [] };
    var brief = cands.map(function (it) {
      return {
        hs: it.hs, name: it.name, duty_by_country: it.duty || {},
        certs: it.certs || [], risk: it.risk || "medium", dg: !!it.dg, notes: it.notes || ""
      };
    });
    return { found: true, count: brief.length, candidates: brief };
  }

  /* ---------------- 运输方式 ---------------- */
  /* 归一化运输方式：接受代码（express/air/ocean/lcl/fcl/rail）、
     工具返回的显示名（国际快递/海运拼柜/海运整柜…）和中文简称；空值按快递。
     认不出来返回 null，由调用方报错，避免静默按快递报价。 */
  function normalizeMode(mode) {
    var text = String(mode || "").trim().toLowerCase();
    if (!text) return "express";
    if (MODE_CN.hasOwnProperty(text)) return text;
    var code;
    for (code in MODE_CN) {
      if (MODE_CN.hasOwnProperty(code) && MODE_CN[code] === text) return code;
    }
    var zh = {
      "快递": "express", "国际快递": "express", "专线": "express", "小包": "express",
      "空运": "air", "空派": "air", "海运": "ocean", "海派": "ocean",
      "整柜": "fcl", "拼柜": "lcl", "铁路": "rail", "班列": "rail", "中欧班列": "rail"
    };
    if (zh.hasOwnProperty(text)) return zh[text];
    return null;
  }

  function modeLabel(mode) {
    return MODE_CN[mode] || "国际快递 / 空运 / 海运 / 海运拼柜 / 海运整柜 / 铁路";
  }

  function modeMatch(requested, carrierMode) {
    if (!requested) return true;
    var req = normalizeMode(requested);
    if (!req) return true;
    if (req === "ocean") return carrierMode === "lcl" || carrierMode === "fcl";
    if (req === "lcl") return carrierMode === "lcl";
    if (req === "fcl") return carrierMode === "fcl";
    return carrierMode === req;
  }

  function cbm(weightKg, dims, pieces) {
    var l = dims[0], w = dims[1], h = dims[2];
    if (l && w && h) return (l * w * h / 1000000.0) * Math.max(1, pieces);
    return Math.max(0.01, weightKg / 300.0);
  }

  function priceOne(carrier, countryCode, weightKg, dims, pieces) {
    var bl = carrier.billing || {}, pr = carrier.price || {};
    var mode = carrier.modes[0];
    var factor = Number((carrier.lane_factor || {})[countryCode] || 1.1);
    var peak = PEAK_MONTHS.indexOf(new Date().getMonth() + 1) >= 0;
    var l = dims[0], w = dims[1], h = dims[2];

    if (mode === "express" || mode === "air") {
      var divisor = Number(bl.divisor || 5000);
      var step = Number(bl.rounding !== undefined ? bl.rounding : 0.5);
      var vol = (l && w && h) ? (l * w * h / divisor) * Math.max(1, pieces) : 0.0;
      var cw = Math.max(weightKg, vol, Number(bl.min_kg || 0.5));
      cw = ceilTo(cw, step);
      var base = Number(pr.base_fee || 0) + Number(pr.per_kg || 0) * cw * factor;
      var freight = Math.max(Number(pr.min_charge || 0), base);
      var fuel = freight * Number(pr.fuel_pct || 0);
      var peakFee = peak ? freight * Number(pr.peak_pct || 0) : 0.0;
      var extras = Number(pr.clearance_fee || 0) + Number(pr.doc_fee || 0);
      var total = freight + fuel + peakFee + extras;
      return {
        chargeable: round2(cw),
        charge_basis: "计费重 " + g(cw) + "kg（体积重系数 " + Math.round(divisor) + "）",
        cbm: null,
        freight: round2(freight),
        fuel: round2(fuel),
        peak_surcharge: round2(peakFee),
        clearance_or_doc: round2(extras),
        dest_fee: 0.0,
        total: round2(total)
      };
    }

    if (mode === "lcl" || mode === "rail") {
      var cb = cbm(weightKg, dims, pieces);
      var wm = Math.max(cb, weightKg / 1000.0);
      var base2 = Number(pr.per_cbm || 0) * wm * factor;
      var freight2 = Math.max(Number(pr.min_charge || 0), base2);
      var fuel2 = freight2 * Number(pr.fuel_pct || 0);
      var peak2 = peak ? freight2 * Number(pr.peak_pct || 0) : 0.0;
      var dest = Number(pr.dest_fee || 0);
      return {
        chargeable: round2(wm),
        charge_basis: "W/M 计费 " + round2(wm) + "（体积 " + round2(cb) + "CBM / 重量 " + num(weightKg / 1000, 3) + "吨）",
        cbm: round2(cb),
        freight: round2(freight2),
        fuel: round2(fuel2),
        peak_surcharge: round2(peak2),
        clearance_or_doc: 0.0,
        dest_fee: round2(dest),
        total: round2(freight2 + fuel2 + peak2 + dest)
      };
    }

    if (mode === "fcl") {
      var cb2 = cbm(weightKg, dims, pieces);
      var box = cb2 <= 28 ? "20GP" : "40HQ";
      var freight3 = Number((pr.container || {})[box] || 0) * factor;
      var fuel3 = freight3 * Number(pr.fuel_pct || 0);
      var peak3 = peak ? freight3 * Number(pr.peak_pct || 0) : 0.0;
      var local = Number(pr.local_fee || 0);
      return {
        chargeable: round2(cb2),
        charge_basis: box + " 整柜（按体积 " + round2(cb2) + "CBM 选型）",
        cbm: round2(cb2),
        container: box,
        freight: round2(freight3),
        fuel: round2(fuel3),
        peak_surcharge: round2(peak3),
        clearance_or_doc: round2(local),
        dest_fee: 0.0,
        total: round2(freight3 + fuel3 + peak3 + local)
      };
    }
    return null;
  }

  function quoteAll(country, weightKg, length, width, height, mode, pieces, hs, declaredValue) {
    var countryEntry = resolveCountry(country);
    if (!countryEntry) throw new Error("未识别的目的国：" + (country || "（空）"));
    if (mode && !normalizeMode(mode)) {
      throw new Error("未知运输方式：" + mode + "（可选 " + modeLabel() + "）");
    }
    var w = Number(weightKg);
    if (!isFinite(w) || w <= 0) throw new Error("总重量需大于 0 kg");
    if (w > 1000000) throw new Error("总重量超出可测算范围（≤ 1000000 kg）");
    var weight = w;
    var dims = [Number(length) || 0, Number(width) || 0, Number(height) || 0];
    dims.forEach(function (v) {
      if (!isFinite(v) || v < 0) throw new Error("尺寸不能为负数");
      if (v > 10000) throw new Error("单边尺寸超出可测算范围（≤ 10000 cm）");
    });
    pieces = (pieces === undefined || pieces === null || pieces === "") ? 1 : parseInt(pieces, 10);
    if (!isFinite(pieces) || pieces < 1) throw new Error("件数需为 ≥ 1 的整数");
    if (pieces > 100000) throw new Error("件数超出可测算范围（≤ 100000）");
    var declaredValueNum = Number(declaredValue) || 0;
    if (declaredValueNum < 0) throw new Error("申报货值不能为负数");
    declaredValue = declaredValueNum;
    var code = countryEntry.code;
    var hsEntry = hs ? findHsEntry("", hs) : null;
    var needDg = !!(hsEntry && hsEntry.dg);

    var quotes = [], excluded = [];
    carriersData().forEach(function (carrier) {
      var name = carrier.name;
      if ((carrier.lanes || []).indexOf(code) < 0) {
        excluded.push({ carrier: name, reason: "该承运商不覆盖 " + countryEntry.name + " 线路" });
        return;
      }
      if (!modeMatch(mode, carrier.modes[0])) {
        excluded.push({ carrier: name, reason: "运输方式不匹配" });
        return;
      }
      var maxKg = Number((carrier.billing || {}).max_kg || 1e9);
      if (weight > maxKg) {
        excluded.push({ carrier: name, reason: "超出单票重量上限 " + g(maxKg) + "kg，需分票或改走空运/海运" });
        return;
      }
      var maxDeclared = Number(carrier.max_declared || 1e12);
      if (declaredValue && Number(declaredValue) > maxDeclared) {
        excluded.push({ carrier: name, reason: "超出可承运货值上限 " + g(maxDeclared) + " 元" });
        return;
      }
      if (needDg && !(carrier.dangerous && carrier.dangerous.length)) {
        excluded.push({ carrier: name, reason: "不承运危险品（锂电池/液体等）" });
        return;
      }
      var price = priceOne(carrier, code, weight, dims, pieces);
      if (!price) return;
      var transit = (carrier.transit || {})[code];
      if (!transit) {
        excluded.push({ carrier: name, reason: "缺少该线路时效基线" });
        return;
      }
      var q = {
        carrier_id: carrier.id,
        carrier: name,
        service: carrier.service || "",
        mode: carrier.modes[0],
        mode_name: MODE_CN[carrier.modes[0]] || "",
        own_clearance: !!carrier.own_clearance,
        ontime: Number(carrier.ontime !== undefined ? carrier.ontime : 0.9),
        max_kg: maxKg < 1e8 ? maxKg : null,
        max_declared: maxDeclared < 1e11 ? maxDeclared : null,
        transit_days: [parseInt(transit[0], 10), parseInt(transit[1], 10)],
        notes: carrier.notes || ""
      };
      for (var k in price) q[k] = price[k];
      quotes.push(q);
    });

    return {
      query: {
        country: code,
        country_name: countryEntry.name,
        mode: mode ? normalizeMode(mode) : "不限",
        weight_kg: round2(weight),
        dims_cm: [dims[0], dims[1], dims[2]],
        pieces: pieces,
        hs: hsEntry ? hsEntry.hs : "",
        declared_value: Number(declaredValue) || 0
      },
      currency: "CNY",
      count: quotes.length,
      quotes: quotes,
      excluded: excluded,
      assumptions: [
        "报价为演示价卡，不含燃油以外的目的港杂费、关税与保险",
        "体积重按承运商 divisor 计算，实际以承运商复测为准",
        "旺季附加费按当前月份自动叠加"
      ]
    };
  }

  /* ---------------- 清关时效 ---------------- */
  function docsCovered(required, provided) {
    if (!provided || !provided.length) return { covered: [], missing: required.slice() };
    var covered = [], missing = [];
    required.forEach(function (doc) {
      var hit = provided.some(function (p) { return p && (doc.indexOf(p) >= 0 || p.indexOf(doc) >= 0); });
      if (hit) covered.push(doc); else missing.push(doc);
    });
    return { covered: covered, missing: missing };
  }

  function portCongestion(port, countryCode, mode) {
    var ports = portsData();
    if (port) {
      for (var i = 0; i < ports.length; i++) {
        if (String(port).toUpperCase() === ports[i].code || String(port).indexOf(ports[i].name) >= 0) {
          return { days: Number(ports[i].congestion_days), name: ports[i].name };
        }
      }
      return { days: 0, name: "" };
    }
    var kind = mode === "air" ? "airport" : "seaport";
    var matched = ports.filter(function (p) { return p.country === countryCode && p.type === kind; });
    if (!matched.length) return { days: 0, name: "" };
    var sum = matched.reduce(function (acc, p) { return acc + Number(p.congestion_days); }, 0);
    return { days: sum / matched.length, name: matched.map(function (p) { return p.name; }).join("、") };
  }

  function estimate(country, mode, hs, docsProvided, carrier, port, declaredValue) {
    var countryEntry = resolveCountry(country);
    if (!countryEntry) throw new Error("未识别的目的国：" + (country || "（空）"));
    var rawMode = mode;
    mode = normalizeMode(mode || "express");
    if (!mode) throw new Error("未知运输方式：" + (rawMode || "") + "（可选 " + modeLabel() + "）");
    var daysKey = MODE_TAIL[mode];
    var base = Number((countryEntry.clearance_days || {})[daysKey] || 2.5);

    var breakdown = [{ item: "目的国基础清关", days: round2(base) }];
    var risks = [], assumptions = [];

    var hsEntry = hs ? findHsEntry("", hs) : null;
    var required = requiredDocs(hsEntry, countryEntry);
    var res = docsCovered(required, docsProvided || []);
    var covered = res.covered, missing = res.missing;

    var docPenalty = 0.0;
    if (missing.length) {
      docPenalty = round2(0.35 * Math.min(4, missing.length));
      breakdown.push({ item: "单证缺失（" + missing.length + " 项）", days: docPenalty });
      risks.push("缺少单证：" + missing.slice(0, 5).join("、"));
    }

    var certGap = 0.0;
    if (hsEntry) {
      var certs = hsEntry.certs || [];
      var certMissing = certs.filter(function (c) {
        return !(docsProvided || []).some(function (p) { return p && (c.indexOf(p) >= 0 || p.indexOf(c) >= 0); });
      });
      if (certMissing.length && hsEntry.risk === "high") {
        certGap = 2.0;
        breakdown.push({ item: "高风险品类认证缺失", days: certGap });
        risks.push("认证未齐：" + certMissing.slice(0, 3).join("、") + "，可能被扣留或退运");
      }
    }

    var riskPenalty = 0.0;
    if (hsEntry) {
      var risk = hsEntry.risk || "medium";
      riskPenalty = risk === "high" ? 1.2 : (risk === "medium" ? 0.5 : 0.0);
      if (riskPenalty) breakdown.push({ item: "品类风险（" + risk + "）", days: riskPenalty });
      if (hsEntry.dg) {
        riskPenalty += 1.0;
        breakdown.push({ item: "危险品（锂电池/液体等）", days: 1.0 });
        risks.push("危险品需承运商书面确认舱位与包装（UN38.3/MSDS）");
      }
    }

    var clearanceGain = 0.0;
    if (carrier) {
      var list = carriersData();
      for (var i = 0; i < list.length; i++) {
        if (carrier === list[i].id || list[i].name.indexOf(carrier) >= 0 || carrier.indexOf(list[i].name) >= 0) {
          if (list[i].own_clearance) {
            clearanceGain = -0.6;
            breakdown.push({ item: list[i].name + " 自营清关", days: -0.6 });
          }
          break;
        }
      }
    }

    var cong = portCongestion(port, countryEntry.code, mode);
    var congestionPenalty = round2(cong.days * 0.6);
    if (congestionPenalty) {
      breakdown.push({ item: "口岸拥堵（" + cong.name + "）", days: congestionPenalty });
      risks.push(cong.name + " 当前拥堵指数偏高（+" + congestionPenalty + " 天）");
    }

    var peakPenalty = 0.0;
    var month = new Date().getMonth() + 1;
    if (PEAK_MONTHS.indexOf(month) >= 0 && (countryEntry.peak_months || []).indexOf(month) >= 0) {
      peakPenalty = (mode === "ocean" || mode === "air" || mode === "rail") ? 1.2 : 0.6;
      breakdown.push({ item: "旺季（舱位/查验排队）", days: peakPenalty });
      risks.push("当前处于旺季，舱位与查验排队时间上升");
    }

    var exemptGain = 0.0;
    if (declaredValue && Number(declaredValue) <= Number(countryEntry.duty_de_minimis_cny || 0)) {
      exemptGain = -0.3;
      breakdown.push({ item: "低于免税额（简化申报）", days: -0.3 });
    }

    var raw = base + docPenalty + certGap + riskPenalty + clearanceGain + congestionPenalty + peakPenalty + exemptGain;
    var est = Math.max(0.5, round1(raw));
    var spread = SPREAD[mode] !== undefined ? SPREAD[mode] : 1.5;
    if (certGap || (missing.length && missing.length >= 3)) spread += 1.0;

    var tail = TAIL_DAYS[MODE_TAIL[mode]] !== undefined ? TAIL_DAYS[MODE_TAIL[mode]] : 2.0;
    assumptions.push("清关时效为工作日口径，不含航班/船期等待");
    assumptions.push("未提供单证清单时按『已备齐常规单证』计算，实际以提交材料为准");

    var confidence = (!missing.length && !certGap) ? "高" : (missing.length <= 2 ? "中" : "低");

    return {
      country: countryEntry.code,
      country_name: countryEntry.name,
      mode: mode,
      mode_name: MODE_CN[mode] || mode,
      clearance_days: [est, round1(est + spread)],
      confidence: confidence,
      breakdown: breakdown.filter(function (b) { return b.days; }),
      missing_docs: missing,
      docs_covered: covered,
      required_docs: required,
      risks: risks,
      assumptions: assumptions,
      tail_days: tail,
      note: "时效为估算区间，受船期、查验率与旺季影响，不作为赔付承诺"
    };
  }

  /* ---------------- 比价 ---------------- */
  function etaWindow(transit, clearanceRange, tail, mode) {
    var prep = PREP_DAYS[mode] !== undefined ? PREP_DAYS[mode] : 1;
    var lo = round1(prep + transit[0] + clearanceRange[0] + tail);
    var hi = round1(prep + transit[1] + clearanceRange[1] + tail);
    var dLo = addDays(Math.ceil(lo));
    var dHi = addDays(Math.ceil(hi));
    return { eta: [lo, hi], date: md(dLo) + " ~ " + md(dHi) };
  }

  function compare(country, weightKg, length, width, height, mode, pieces, hs, declaredValue, docsProvided, prefer) {
    var base = quoteAll(country, weightKg, length, width, height, mode, pieces, hs, declaredValue);
    var countryEntry = resolveCountry(country);
    var hsEntry = hs ? findHsEntry("", hs) : null;
    var tax = declaredValue ? computeDuty(hsEntry, countryEntry, declaredValue, 0) : null;

    var rows = [];
    base.quotes.forEach(function (quote) {
      var cl = estimate(country, quote.mode, hs, docsProvided, quote.carrier_id, "", declaredValue);
      var tail = TAIL_DAYS[MODE_TAIL[quote.mode]] !== undefined ? TAIL_DAYS[MODE_TAIL[quote.mode]] : 2.0;
      var win = etaWindow(quote.transit_days, cl.clearance_days, tail, quote.mode);

      var taxAmount = tax ? tax.total_tax : 0.0;
      var agencyFee = quote.own_clearance ? 0.0 : AGENCY_CLEARANCE_FEE;
      var landed = quote.total + taxAmount + agencyFee;
      var riskPenalty = 0.0, flags = [];
      if (!quote.own_clearance) {
        riskPenalty += 0.2;
        flags.push("含代理清关预估 " + money(agencyFee) + " 元（自行清关可省）");
      }
      if (quote.service.indexOf("机场到机场") >= 0 || quote.service.indexOf("港到港") >= 0 || quote.service.indexOf("站到站") >= 0) {
        riskPenalty += 0.1;
        flags.push("不含尾程派送（需另行安排）");
      }
      if (hsEntry && hsEntry.dg && (quote.mode === "express" || quote.mode === "air")) riskPenalty += 0.3;
      if (cl.confidence === "低") riskPenalty += 0.3;

      var row = {};
      for (var k in quote) row[k] = quote[k];
      row.tax = taxAmount;
      row.duty = tax ? tax.duty : 0.0;
      row.vat = tax ? tax.vat : 0.0;
      row.agency_fee = round2(agencyFee);
      row.landed_total = round2(landed);
      row.clearance_days = cl.clearance_days;
      row.eta_days = win.eta;
      row.eta_date = win.date;
      row.confidence = cl.confidence;
      row.risk_penalty = round2(riskPenalty);
      row.flags = flags;
      rows.push(row);
    });

    if (!rows.length) {
      var empty = {};
      for (var k2 in base) empty[k2] = base[k2];
      empty.quotes = [];
      empty.message = "没有满足条件的承运商，请放宽重量/货值或更换运输方式";
      return empty;
    }

    var costs = rows.map(function (r) { return r.landed_total; });
    var etas = rows.map(function (r) { return r.eta_days[1]; });
    var costMin = Math.min.apply(null, costs), costMax = Math.max.apply(null, costs);
    var etaMin = Math.min.apply(null, etas), etaMax = Math.max.apply(null, etas);
    var costSpan = Math.max(costMax - costMin, 1e-6);
    var etaSpan = Math.max(etaMax - etaMin, 1e-6);

    rows.forEach(function (row) {
      var nCost = (row.landed_total - costMin) / costSpan;
      var nEta = (row.eta_days[1] - etaMin) / etaSpan;
      var score = 100 - 50 * nCost - 30 * nEta - 8 * (1 - row.ontime) * 10 - 10 * row.risk_penalty;
      row.score = round1(Math.max(0, Math.min(100, score)));
    });

    function byMin(list, fn) {
      var best = list[0], bestV = fn(list[0]);
      list.forEach(function (x) { var v = fn(x); if (v < bestV) { best = x; bestV = v; } });
      return best;
    }
    var cheapest = byMin(rows, function (r) { return r.landed_total; });
    var fastest = byMin(rows, function (r) { return r.eta_days[1]; });
    var best = byMin(rows, function (r) { return -r.score; });

    var pick = best;
    if (prefer === "cheapest") pick = cheapest;
    else if (prefer === "fastest") pick = fastest;

    var gapCost = round2(fastest.landed_total - cheapest.landed_total);
    var gapDay = round1(cheapest.eta_days[1] - fastest.eta_days[1]);
    var tradeoff = "最省为 " + cheapest.carrier + "（" + String(cheapest.landed_total) + " 元，" + cheapest.eta_date + " 到）；" +
      "最快为 " + fastest.carrier + "（" + fastest.eta_date + " 到，贵 " + String(gapCost) + " 元）；" +
      "若按时效优先，可少等约 " + String(gapDay) + " 天。";

    rows.forEach(function (r) { delete r.carrier_id; });
    rows.sort(function (a, b) { return b.score - a.score; });

    var out = {};
    for (var k3 in base) out[k3] = base[k3];
    out.quotes = rows;
    out.tax = tax;
    out.cheapest = cheapest.carrier;
    out.fastest = fastest.carrier;
    out.best_value = best.carrier;
    out.recommendation = {
      carrier: pick.carrier,
      mode: pick.mode_name,
      landed_total: pick.landed_total,
      eta_date: pick.eta_date,
      eta_days: pick.eta_days,
      score: pick.score,
      reason: "综合成本、时效与准点率评分最高（" + pick.score + " 分）",
      tradeoff: tradeoff
    };
    return out;
  }

  /* ---------------- 全链路方案 ---------------- */
  function timeline(transit, clearance, tail, mode) {
    var prep = (mode === "express" || mode === "air") ? 1 : 3;
    var tLo = parseInt(transit[0], 10), tHi = parseInt(transit[1], 10);
    var cLo = Number(clearance[0]), cHi = Number(clearance[1]);
    var transitEnd = prep + tHi;
    var clearEnd = prep + tHi + cHi;
    var total = prep + tHi + cHi + tail;
    var mil = [
      { stage: "备货与订舱", start: 0, end: prep, note: (mode === "fcl" || mode === "lcl") ? "整柜/拼柜建议提前 10-14 天订舱" : "快递/空运舱位提前 2-3 天确认" },
      { stage: "出运与干线", start: prep, end: transitEnd, note: "干线 " + tLo + "-" + tHi + " 天，以实际航班/船期为准" },
      { stage: "目的国清关", start: transitEnd, end: round1(clearEnd), note: "清关 " + g(cLo) + "-" + g(cHi) + " 个工作日，单证齐备可缩短" },
      { stage: "尾程派送", start: round1(clearEnd), end: round1(total), note: "派送约 " + g(tail) + " 天，偏远地区另加 1-2 天" }
    ];
    mil.forEach(function (item) { item.date = md(addDays(Math.round(item.end))); });
    return mil;
  }

  function planShipment(country, weightKg, length, width, height, declaredValue, hs, mode, pieces, docsProvided, prefer) {
    var baseDocsAssumed = false;
    if (!docsProvided || !docsProvided.length) {
      docsProvided = ["商业发票", "装箱单"];
      baseDocsAssumed = true;
    }
    var countryEntry = resolveCountry(country);
    if (!countryEntry) throw new Error("未识别的目的国：" + (country || "（空）"));

    var cmp = compare(country, weightKg, length, width, height, mode, pieces, hs, declaredValue, docsProvided, prefer);
    if (!cmp.quotes || !cmp.quotes.length) {
      return { ok: false, message: cmp.message || "无可选承运商", excluded: cmp.excluded || [] };
    }

    var pick = cmp.recommendation;
    var chosen = null;
    for (var i = 0; i < cmp.quotes.length; i++) {
      if (cmp.quotes[i].carrier === pick.carrier) { chosen = cmp.quotes[i]; break; }
    }
    if (!chosen) chosen = cmp.quotes[0];

    var hsEntry = hs ? findHsEntry("", hs) : null;
    var cl = estimate(country, chosen.mode, hs, docsProvided, chosen.carrier, "", declaredValue);
    var docs = requiredDocs(hsEntry, countryEntry);

    var advice = [];
    if (cl.missing_docs.length) advice.push("优先补齐单证：" + cl.missing_docs.slice(0, 4).join("、") + "，可压缩 1-3 天清关时间");
    if (hsEntry && hsEntry.risk === "high") advice.push("高查验品类，建议提前取得认证并保留检测报告；清关行优先选自营清关承运商");
    if (countryEntry.vat_rate && declaredValue) {
      advice.push("目的国" + (countryEntry.vat_name || "VAT") + "税率 " + Math.round(countryEntry.vat_rate * 100) + "%，报价时按含税口径向客户披露");
    }
    if (chosen.mode === "fcl" || chosen.mode === "lcl") advice.push("海运转关环节多，建议预留 3-5 天缓冲，并购买一切险");
    if (hsEntry && hsEntry.dg) advice.push("危险品需承运商书面确认，包装与 UN38.3/MSDS 必须齐备");
    if (declaredValue && Number(declaredValue) <= Number(countryEntry.duty_de_minimis_cny || 0)) {
      advice.push("低于免税额，可享关税豁免；但不得为避税拆单或低报申报价值");
    }

    var weaknesses = (cl.risks || []).slice();
    (chosen.flags || []).forEach(function (f) { if (weaknesses.indexOf(f) < 0) weaknesses.push(f); });

    return {
      ok: true,
      query: cmp.query,
      recommendation: pick,
      chosen_quote: chosen,
      alternatives: cmp.quotes.slice(0, 5).map(function (q) {
        return { carrier: q.carrier, mode: q.mode_name, landed_total: q.landed_total, eta_date: q.eta_date, score: q.score };
      }),
      tax: cmp.tax,
      duty_summary: cmp.tax
        ? ("关税 " + cmp.tax.duty + " 元 + " + cmp.tax.vat_name + " " + cmp.tax.vat + " 元 = " + cmp.tax.total_tax + " 元")
        : "未提供货值，税费未计",
      clearance: cl,
      docs: { required: docs, covered: cl.docs_covered, missing: cl.missing_docs },
      timeline: timeline(chosen.transit_days, cl.clearance_days, cl.tail_days, chosen.mode),
      risks: weaknesses,
      advice: advice,
      assumptions: (baseDocsAssumed ? ["未提供单证清单，按已备商业发票与装箱单估算，其余按缺失计"] : [])
        .concat(cmp.assumptions || []).concat(cl.assumptions || [])
    };
  }

  /* ---------------- 自然语言解析 ---------------- */
  var COUNTRY_ALIASES = {
    US: ["美国", "美线", "usa", "us", "united states", "america", "洛杉矶", "纽约"],
    CA: ["加拿大", "canada", "ca", "多伦多", "温哥华"],
    DE: ["德国", "de", "germany", "汉堡", "法兰克福", "柏林"],
    FR: ["法国", "fr", "france", "巴黎", "勒阿弗尔"],
    NL: ["荷兰", "nl", "netherlands", "holland", "鹿特丹", "阿姆斯特丹"],
    IT: ["意大利", "it", "italy", "米兰", "罗马"],
    ES: ["西班牙", "es", "spain", "马德里", "巴塞罗那"],
    GB: ["英国", "gb", "uk", "united kingdom", "伦敦", "费利克斯托"],
    JP: ["日本", "jp", "japan", "东京", "大阪", "名古屋"],
    SG: ["新加坡", "sg", "singapore"],
    AU: ["澳大利亚", "澳洲", "au", "australia", "悉尼", "墨尔本"]
  };
  var MODE_ALIASES = {
    express: ["快递", "express", "国际快递", "门到门", "小包", "专线"],
    air: ["空运", "air", "空派", "机场到机场", "空加派"],
    ocean: ["海运", "ocean", "海派", "船运", "整柜", "拼柜", "fcl", "lcl"],
    lcl: ["拼柜", "拼箱", "lcl"],
    fcl: ["整柜", "整箱", "fcl"],
    rail: ["铁路", "班列", "中欧班列", "rail"]
  };
  var COUNTRY_NAME = {
    US: "美国", CA: "加拿大", DE: "德国", FR: "法国", NL: "荷兰",
    IT: "意大利", ES: "西班牙", GB: "英国", JP: "日本", SG: "新加坡", AU: "澳大利亚"
  };

  function findAlias(text, table) {
    var low = String(text || "").toLowerCase();
    var bestCode = "", bestLen = 0;
    Object.keys(table).forEach(function (code) {
      table[code].forEach(function (alias) {
        var al = alias.toLowerCase();
        if (low.indexOf(al) < 0 || alias.length <= bestLen) return;
        // 短英文别名按词边界匹配（避免 "us" 命中 "business"），不用 lookbehind 以兼容老 WebView
        if (/^[\x00-\x7f]+$/.test(alias) && alias.length <= 3) {
          var idx = low.indexOf(al);
          while (idx >= 0) {
            var before = idx > 0 ? low.charAt(idx - 1) : "";
            var after = low.charAt(idx + al.length);
            var okBefore = !before || !/[a-z]/.test(before);
            var okAfter = !after || !/[a-z]/.test(after);
            if (okBefore && okAfter) break;
            idx = low.indexOf(al, idx + 1);
          }
          if (idx < 0) return;
        }
        bestCode = code;
        bestLen = alias.length;
      });
    });
    return bestCode;
  }

  function parseMode(text) {
    if (findAlias(text, { lcl: MODE_ALIASES.lcl })) return "lcl";
    if (findAlias(text, { fcl: MODE_ALIASES.fcl })) return "fcl";
    if (findAlias(text, { rail: MODE_ALIASES.rail })) return "rail";
    if (findAlias(text, { air: MODE_ALIASES.air })) return "air";
    if (findAlias(text, { express: MODE_ALIASES.express })) return "express";
    if (findAlias(text, { ocean: MODE_ALIASES.ocean })) return "ocean";
    return "";
  }

  function parseRequest(text) {
    text = String(text || "");
    var country = findAlias(text, COUNTRY_ALIASES);
    var mode = parseMode(text);
    var weightM = text.match(/(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克|KG)/i);
    var dimsM = text.match(/(\d+(?:\.\d+)?)\s*[x×*X]\s*(\d+(?:\.\d+)?)\s*[x×*X]\s*(\d+(?:\.\d+)?)/);
    var valueM = text.match(/(?:货值|价值|申报(?:价值)?|金额|单价|总价)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(万元|元|块|rmb|cny|美元|美金|usd)?/i);
    var piecesM = text.match(/(\d+)\s*(?:件|箱|个|托盘|pcs|ctn)/i);
    var hsM = text.match(/(?:hs|海关编码|编码)\s*[:：]?\s*(\d{6,10})/i);
    var hs = "";
    if (hsM) hs = hsM[1];
    else {
      var nums = text.match(/\b\d{6,10}\b/g);
      if (nums && nums.length) hs = nums[0];
    }
    var value = 0;
    if (valueM) {
      value = parseFloat(valueM[1]);
      var unit = String(valueM[2] || "").toLowerCase();
      if (unit === "万元") value *= 10000;
      else if (unit === "美元" || unit === "美金" || unit === "usd") value *= 7.2;
    }
    return {
      country: country,
      country_name: COUNTRY_NAME[country] || country,
      mode: mode,
      weight_kg: weightM ? parseFloat(weightM[1]) : 0,
      dims_cm: dimsM ? [parseFloat(dimsM[1]), parseFloat(dimsM[2]), parseFloat(dimsM[3])] : [0, 0, 0],
      declared_value: value,
      pieces: piecesM ? parseInt(piecesM[1], 10) : 1,
      hs: hs
    };
  }

  /* ---------------- 多智能体（classic 兜底） ---------------- */
  var ROUTE_RULES = [
    ["plan", /全流程|一条龙|端到端|走一遍|方案|推荐|怎么发|帮我选|最优/],
    ["summarizer", /总结|汇总|复盘|统计|报价记录|历史/],
    ["quote", /比价|报价|运费|多少钱|便宜|成本|价格|对比|运价/],
    ["clearance", /清关|时效|多久|几天|查验|扣关|滞港|延误/],
    ["compliance", /HS|海关编码|归类|关税|增值税|税|认证|单证|合规|申报/]
  ];

  function route(text) {
    for (var i = 0; i < ROUTE_RULES.length; i++) if (ROUTE_RULES[i][1].test(text)) return ROUTE_RULES[i][0];
    return "quote";
  }

  function tr(trace, action, args, result, ok) {
    trace.push({
      round: trace.length + 1,
      action: action,
      args: args || {},
      ok: ok === false ? false : true,
      result: typeof result === "string" ? result : JSON.stringify(result).slice(0, 1200)
    });
  }

  var IMPLIED_MODE = ["专线", "小包", "拼柜", "整柜", "班列", "空运", "海运"];
  function label(carrier, modeName) {
    if (!modeName || carrier.indexOf(modeName) >= 0 || IMPLIED_MODE.some(function (k) { return carrier.indexOf(k) >= 0; })) return carrier;
    return carrier + "（" + modeName + "）";
  }

  function quoteReply(text) {
    var trace = [];
    var req = parseRequest(text);
    if (!req.country) {
      return {
        answer: "请补充以下信息后我再比价：\n" +
          "1) 目的国（美国 / 德国 / 英国 / 日本 / 新加坡 / 澳洲等）\n" +
          "2) 重量（如 20kg）与单件尺寸（如 40x30x20cm）\n" +
          "3) 货值（如 5000 元）与品类（如 服装 / 手机 / 玩具）\n" +
          "4) 期望方式（快递 / 空运 / 海运拼柜 / 整柜 / 铁路）\n" +
          "示例：\"20kg 服装，尺寸 50x40x30cm，货值 5000 元，发德国，比一下价\"",
        trace: trace
      };
    }
    var weight = req.weight_kg || 1.0;
    var notes = [];
    if (!req.weight_kg) notes.push("未识别到重量，按 1kg 试算");

    var hs = req.hs, hsName = "";
    if (!hs) {
      var found = lookupHsBrief(text, "", 5);
      tr(trace, "knowledge.lookup_hs", { keyword: text }, found);
      if (found.found) { hs = found.candidates[0].hs; hsName = found.candidates[0].name; }
    }

    var args = {
      country: req.country, weight_kg: weight,
      length: req.dims_cm[0], width: req.dims_cm[1], height: req.dims_cm[2],
      pieces: req.pieces, hs: hs, declared_value: req.declared_value, mode: req.mode
    };
    var result;
    try {
      result = compare(req.country, weight, req.dims_cm[0], req.dims_cm[1], req.dims_cm[2], req.mode, req.pieces, hs, req.declared_value, null, "balance");
      tr(trace, "rates.compare_rates", args, { count: result.count, recommendation: result.recommendation });
    } catch (e) {
      return { answer: "比价失败：" + e.message, trace: trace };
    }

    var quotes = result.quotes || [];
    if (!quotes.length) {
      return {
        answer: (result.message || "没有可用承运商") + "\n被排除的承运商：\n" +
          (result.excluded || []).slice(0, 6).map(function (x) { return "- " + x.carrier + "：" + x.reason; }).join("\n"),
        trace: trace
      };
    }

    var lines = [
      "【比价结果】" + result.query.country_name + " · " + g(weight) + "kg · " +
      ((!result.query.mode || result.query.mode === "不限") ? "不限方式" : result.query.mode) +
      "｜货值 " + money(req.declared_value) + " 元" +
      (hs ? "｜品类 " + (hsName || "已识别 HS") + "（HS " + hs + "）" : ""),
      "共 " + result.count + " 家可选，按综合评分排序（总成本含运费+关税+增值税，不自营清关的已计入代理费）：",
      ""
    ];
    quotes.slice(0, 8).forEach(function (q, idx) {
      var parts = "运费 " + money(q.total) + " + 税费 " + money(q.tax);
      if (q.agency_fee) parts += " + 代理清关 " + money(q.agency_fee);
      lines.push((idx + 1) + ". " + label(q.carrier, q.mode_name) +
        "｜总成本 " + money(q.landed_total) + " 元（" + parts + "）" +
        "｜时效 " + g(q.eta_days[0]) + "-" + g(q.eta_days[1]) + " 天（" + q.eta_date + "）" +
        "｜准点率 " + Math.round(q.ontime * 100) + "%｜评分 " + q.score);
      if (q.flags && q.flags.length) lines.push("    注意：" + q.flags.join("；"));
    });

    var rec = result.recommendation;
    lines.push("", "推荐：" + label(rec.carrier, rec.mode) + "｜" + money(rec.landed_total) + " 元｜" +
      g(rec.eta_days[0]) + "-" + g(rec.eta_days[1]) + " 天（" + rec.eta_date + "）",
      "理由：" + rec.reason, "权衡：" + rec.tradeoff);

    if (result.excluded && result.excluded.length) {
      lines.push("", "未纳入比较：");
      result.excluded.slice(0, 5).forEach(function (x) { lines.push("- " + x.carrier + "：" + x.reason); });
    }
    if (notes.length) lines.push("", "说明：" + notes.join("；"));
    lines.push("口径：" + (result.assumptions || []).slice(0, 3).join("；"));
    return { answer: lines.join("\n"), trace: trace };
  }

  function planReply(text) {
    var trace = [];
    var req = parseRequest(text);
    if (!req.country) {
      return {
        answer: "给我目的国 + 重量 + 货值 + 品类，我就出完整方案（含承运商、税费、清关时效、单证与时间线）。\n" +
          "示例：\"300kg 家具，尺寸 120x80x60cm，货值 30000 元，发德国，要海运拼柜\"",
        trace: trace
      };
    }
    var hs = req.hs, hsName = "";
    if (!hs) {
      var found = lookupHsBrief(text, "", 5);
      tr(trace, "knowledge.lookup_hs", { keyword: text }, found);
      if (found.found) { hs = found.candidates[0].hs; hsName = found.candidates[0].name; }
    }
    var weight = req.weight_kg || 1.0;
    var plan;
    try {
      plan = planShipment(req.country, weight, req.dims_cm[0], req.dims_cm[1], req.dims_cm[2],
        req.declared_value, hs, req.mode, req.pieces, null, "balance");
      tr(trace, "shipment.plan_shipment", { country: req.country, weight_kg: weight, hs: hs, mode: req.mode }, { ok: plan.ok });
    } catch (e) {
      return { answer: "方案生成失败：" + e.message, trace: trace };
    }
    if (!plan.ok) {
      var bad = ["暂时无法生成方案：" + plan.message, "", "被排除的承运商："];
      (plan.excluded || []).slice(0, 6).forEach(function (x) { bad.push("- " + x.carrier + "：" + x.reason); });
      return { answer: bad.join("\n"), trace: trace };
    }

    var chosen = plan.chosen_quote, rec = plan.recommendation, cl = plan.clearance;
    var lines = [
      "【全链路方案】" + plan.query.country_name + " · " + g(weight) + "kg" +
      (hs ? " · " + (hsName || "已识别") + "（HS " + hs + "）" : "") +
      "｜货值 " + money(req.declared_value) + " 元",
      "",
      "推荐：" + rec.carrier + "（" + rec.mode + "）",
      "总成本：" + money(chosen.landed_total) + " 元（运费 " + money(chosen.total) + " + 税费 " + money(chosen.tax) + "）",
      "时效：" + g(chosen.eta_days[0]) + "-" + g(chosen.eta_days[1]) + " 天（预计 " + chosen.eta_date + " 签收），" +
      "其中清关 " + g(cl.clearance_days[0]) + "-" + g(cl.clearance_days[1]) + " 天（置信度 " + cl.confidence + "）",
      "计费：" + chosen.charge_basis,
      "",
      "备选（按评分）："
    ];
    plan.alternatives.slice(0, 3).forEach(function (alt) {
      lines.push("- " + alt.carrier + "（" + alt.mode + "）：" + money(alt.landed_total) + " 元｜" + alt.eta_date + "｜评分 " + alt.score);
    });

    lines.push("", "税费：" + plan.duty_summary);
    if (plan.tax) {
      lines.push("计税口径：关税率 " + (plan.tax.duty_rate * 100).toFixed(1) + "%，" +
        plan.tax.vat_name + " " + (plan.tax.vat_rate * 100).toFixed(1) + "%，免税额 " + money(plan.tax.duty_de_minimis) + " 元");
    }
    lines.push("", "单证：", "- 需备：" + plan.docs.required.join("、"));
    lines.push(plan.docs.missing.length ? "- 缺失：" + plan.docs.missing.join("、") : "- 缺失：无（按已备材料判断）");
    lines.push("", "时间线：");
    plan.timeline.forEach(function (it) {
      lines.push("- " + it.stage + "：第 " + it.start + "-" + it.end + " 天（约 " + it.date + " 前完成）");
    });
    if (plan.risks.length) {
      lines.push("", "风险：");
      plan.risks.slice(0, 6).forEach(function (r) { lines.push("- " + r); });
    }
    if (plan.advice.length) {
      lines.push("", "优化建议：");
      plan.advice.slice(0, 5).forEach(function (a) { lines.push("- " + a); });
    }
    lines.push("", "口径：" + (plan.assumptions || []).slice(0, 3).join("；"));
    return { answer: lines.join("\n"), trace: trace };
  }

  var DOC_WORDS = {
    "商业发票": ["发票", "invoice"],
    "装箱单": ["装箱单", "packing"],
    "空运/海运提单": ["提单", "运单", "bl", "awb"],
    "EORI 号": ["eori"],
    "EORI(GB) 号": ["eori"],
    "CE 符合性声明": ["ce"],
    "CE": ["ce"],
    "FDA 备案": ["fda"],
    "FCC": ["fcc"]
  };

  function detectDocs(text) {
    var low = String(text || "").toLowerCase();
    var found = [];
    Object.keys(DOC_WORDS).forEach(function (doc) {
      if (DOC_WORDS[doc].some(function (w) { return low.indexOf(w) >= 0; })) found.push(doc);
    });
    return found;
  }

  function clearanceReply(text) {
    var trace = [];
    var req = parseRequest(text);
    if (!req.country) {
      var top = portsData().slice().sort(function (a, b) { return Number(b.congestion_days) - Number(a.congestion_days); }).slice(0, 5);
      tr(trace, "clearance.port_status", {}, { top_congested: top });
      var lines = ["请补充目的国（美国/德国/英国/日本/新加坡/澳洲等）与运输方式，我再估算清关时效。", "", "当前拥堵最高的口岸（可作为起运/目的口岸参考）："];
      top.forEach(function (item) {
        lines.push("- " + item.name + "（" + item.code + "）：拥堵指数 " + item.congestion_days + " 天，平均滞港 " + item.avg_dwell + " 天");
      });
      return { answer: lines.join("\n"), trace: trace };
    }
    var hs = req.hs, hsName = "";
    if (text && !hs) {
      var found = lookupHsBrief(text, "", 5);
      tr(trace, "knowledge.lookup_hs", { keyword: text }, found);
      if (found.found) { hs = found.candidates[0].hs; hsName = found.candidates[0].name; }
    }
    var docs = detectDocs(text);
    var est;
    try {
      est = estimate(req.country, req.mode || "express", hs, docs, "", "", req.declared_value);
      tr(trace, "clearance.estimate_clearance", { country: req.country, mode: req.mode || "express", hs: hs, docs_provided: docs }, { clearance_days: est.clearance_days });
    } catch (e) {
      return { answer: "清关时效估算失败：" + e.message, trace: trace };
    }
    var lines = [
      "【清关时效】" + est.country_name + " · " + est.mode_name + (hs ? " · " + (hsName || "已识别") + "（HS " + hs + "）" : ""),
      "预估清关 " + g(est.clearance_days[0]) + "-" + g(est.clearance_days[1]) + " 个工作日（置信度：" + est.confidence + "），尾程派送另计约 " + g(est.tail_days) + " 天",
      "", "影响因素分解："
    ];
    est.breakdown.forEach(function (item) {
      var sign = item.days > 0 ? "+" : "";
      lines.push("- " + item.item + "：" + sign + g(item.days) + " 天");
    });
    if (est.missing_docs.length) lines.push("", "缺失单证：" + est.missing_docs.join("、"));
    if (est.docs_covered.length) lines.push("已备单证：" + est.docs_covered.join("、"));
    if (est.risks.length) {
      lines.push("", "风险提示：");
      est.risks.slice(0, 6).forEach(function (r) { lines.push("- " + r); });
    }
    lines.push("", "假设：" + est.assumptions.join("；"));
    lines.push("注：" + est.note);
    return { answer: lines.join("\n"), trace: trace };
  }

  function complianceReply(text) {
    var trace = [];
    var req = parseRequest(text);
    var found = lookupHsBrief(text, req.hs, 5);
    tr(trace, "knowledge.lookup_hs", { hs: req.hs, keyword: text }, found);
    if (!found.found) {
      return {
        answer: "未能在知识库中匹配到品类，请提供更具体的品名或 6-10 位 HS 编码。\n\n知识库覆盖的品类：\n" +
          "手机、笔记本、锂电池、T恤、鞋、玩具、木家具、化妆品、保健品、箱包、塑料件、线缆、五金",
        trace: trace
      };
    }
    var item = found.candidates[0];
    var hs = item.hs;

    if (!req.country) {
      var lines = [
        "【归类】" + item.name + " → HS " + hs,
        "风险等级：" + item.risk + (item.dg ? "（危险品）" : ""),
        "认证要求：" + ((item.certs || []).join("、") || "无"),
        "", "告诉我目的国（如 德国/美国/英国），我再算关税与增值税。", "", "各国关税率参考："
      ];
      Object.keys(item.duty_by_country || {}).slice(0, 8).forEach(function (code) {
        lines.push("- " + code + "：" + (Number(item.duty_by_country[code]) * 100).toFixed(1) + "%");
      });
      return { answer: lines.join("\n"), trace: trace };
    }

    var value = req.declared_value;
    var valueNote = "";
    if (!value) { valueNote = "未识别到货值，按 1000 元试算。"; value = 1000.0; }

    var countryEntry = resolveCountry(req.country);
    var hsEntry = findHsEntry("", hs);
    var tax;
    try {
      tax = computeDuty(hsEntry, countryEntry, value, 0);
      tax.docs = requiredDocs(hsEntry, countryEntry);
      tr(trace, "knowledge.duty_calc", { hs: hs, country: req.country, declared_value: value }, tax);
    } catch (e) {
      return { answer: "税费测算失败：" + e.message, trace: trace };
    }

    var lines2 = [
      "【归类与税费】" + item.name + " → HS " + hs + "｜目的国 " + tax.country_name,
      "申报货值 " + money(tax.declared_value) + " 元｜关税率 " + (tax.duty_rate * 100).toFixed(1) + "%" + (tax.duty_exempt ? "（低于免税额，免征）" : ""),
      "关税 " + money(tax.duty) + " 元 + " + tax.vat_name + "（" + (tax.vat_rate * 100).toFixed(1) + "%）" + money(tax.vat) + " 元 = 税费合计 " + money(tax.total_tax) + " 元",
      "到岸成本（货值+税费）" + money(tax.landed_cost) + " 元",
      "",
      "免税额：关税 " + money(tax.duty_de_minimis) + " 元以下免征；" + tax.vat_name + " 起征点按目的国规则执行",
      "认证：" + ((item.certs || []).join("、") || "无"),
      "单证：" + (tax.docs || []).join("、"),
      "风险等级：" + item.risk + (item.dg ? "｜危险品，需承运商确认" : "")
    ];
    if (item.notes) lines2.push("提示：" + item.notes);
    if (valueNote) lines2.push(valueNote);
    lines2.push("注：税率为演示基线，正式申报以目的国海关核定为准，不构成报关依据。");
    return { answer: lines2.join("\n"), trace: trace };
  }

  function summarizerReply(text) {
    var rows = quoteRows();
    var byCountry = {}, byCarrier = {}, total = 0.0;
    rows.forEach(function (row) {
      byCountry[row.country || ""] = (byCountry[row.country || ""] || 0) + 1;
      byCarrier[row.carrier || ""] = (byCarrier[row.carrier || ""] || 0) + 1;
      total += Number(row.landed_total || 0);
    });
    var lines = [
      "【汇总】",
      "- 知识库：" + countries().length + " 个目的国、" + hsItems().length + " 个品类、" +
      carriersData().length + " 家承运商、" + portsData().length + " 个口岸",
      "- 报价记录：" + rows.length + " 条，累计总成本 " + money(total) + " 元"
    ];
    function topOf(obj) {
      return Object.keys(obj).map(function (k) { return [k, obj[k]]; })
        .sort(function (a, b) { return b[1] - a[1]; }).slice(0, 5);
    }
    if (rows.length) {
      var tc = topOf(byCountry);
      if (tc.length) lines.push("- 目的国分布：" + tc.map(function (x) { return x[0] + "×" + x[1]; }).join("、"));
      var tk = topOf(byCarrier);
      if (tk.length) lines.push("- 承运商分布：" + tk.map(function (x) { return x[0] + "×" + x[1]; }).join("、"));
      lines.push("结论：已形成「询价 → 比价 → 导出报价」闭环，可继续沉淀常走线路的价卡。");
    } else {
      lines.push("结论：还没有导出过报价单，建议先跑一次比价并导出，形成可复盘的记录。");
    }
    lines.push("说明：统计只覆盖已导出的报价单，不包含仅比价未导出的询价。");
    return { answer: lines.join("\n"), trace: [] };
  }

  function ask(text, sessionId) {
    text = String(text || "").trim();
    if (!text) return { answer: "请输入内容。", trace: [], agent: "supervisor" };
    var role = route(text);
    var res;
    if (role === "plan") res = planReply(text);
    else if (role === "summarizer") res = summarizerReply(text);
    else if (role === "clearance") res = clearanceReply(text);
    else if (role === "compliance") res = complianceReply(text);
    else res = quoteReply(text);
    var trace = [{ round: 0, action: "route", args: {}, ok: true, result: role }].concat(res.trace || []);
    logEvent({ type: "ask", agent: role, chars: res.answer.length });
    return { answer: res.answer, trace: trace, agent: role };
  }

  /* ---------------- 存储：报价与遥测（App 本地） ---------------- */
  var QUOTE_FIELDS = ["quote_id", "created_at", "country", "mode", "carrier", "weight_kg",
    "declared_value", "freight_total", "tax", "landed_total", "eta_date", "note"];

  function readJSON(key, def) {
    try { var v = localStorage.getItem(key); return v ? JSON.parse(v) : def; } catch (e) { return def; }
  }
  function writeJSON(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) { /* 忽略配额错误 */ }
  }
  function quoteRows() { return readJSON("logi_quotes", []); }
  function logEvent(ev) {
    ev.ts = nowText();
    var list = readJSON("logi_events", []);
    list.push(ev);
    writeJSON("logi_events", list.slice(-60));
  }

  function exportQuote(body) {
    var countryEntry = resolveCountry(body.country);
    if (!countryEntry) throw new Error("未识别的目的国：" + body.country);
    if (!body.carrier) throw new Error("carrier 不能为空");
    var qid = hash10(nowText() + "|" + body.carrier + "|" + body.country + "|" + body.weight_kg);
    var row = {
      quote_id: qid,
      created_at: nowText(),
      country: countryEntry.code,
      mode: normalizeMode(body.mode) || "",
      carrier: body.carrier,
      weight_kg: round2(body.weight_kg || 0),
      declared_value: round2(body.declared_value || 0),
      freight_total: round2(body.freight_total || 0),
      tax: round2(body.tax || 0),
      landed_total: round2(body.landed_total || 0),
      eta_date: body.eta_date || "",
      note: String(body.note || "").slice(0, 200)
    };
    var rows = quoteRows();
    rows.push(row);
    writeJSON("logi_quotes", rows);
    logEvent({ type: "export", quote_id: qid, carrier: row.carrier });
    return { ok: true, quote_id: qid, row: row, total_quotes: rows.length, csv: toCSV(row) };
  }

  function toCSV(row) {
    var head = QUOTE_FIELDS.join(",");
    var line = QUOTE_FIELDS.map(function (f) {
      var v = row[f] === undefined ? "" : String(row[f]);
      // 防公式注入：以 = + - @ 开头的值在 Excel 里会被当公式执行
      if (/^[=+\-@]/.test(v)) v = "'" + v;
      return /[",\n]/.test(v) ? "\"" + v.replace(/"/g, "\"\"") + "\"" : v;
    }).join(",");
    return "\ufeff" + head + "\n" + line + "\n";
  }

  /* ---------------- 本地 API（由 mobile.js 的 fetch 拦截调用） ---------------- */
  function guard(fn) {
    try { return { ok: true, data: fn() }; }
    catch (e) { logEvent({ type: "api_error", error: String(e.message || e) }); return { ok: false, error: "Error: " + (e.message || e) }; }
  }

  function stats() {
    var rows = quoteRows();
    return {
      knowledge: { countries: countries().length, hs: hsItems().length, carriers: carriersData().length, ports: portsData().length },
      quotes: { total: rows.length, recent: rows.slice(-5) },
      budget: { used: 0, max: 12 },
      model: "离线规则引擎（无 LLM）",
      permission_mode: "plan",
      net_policy: "allowlist"
    };
  }

  function handleApi(path, params, body) {
    var p = path.replace(/^\/api\//, "");
    switch (p) {
      case "ask":
        return ask(body.text, body.session_id);
      case "quote":
        return guard(function () {
          return compare(body.country, body.weight_kg, body.length, body.width, body.height,
            body.mode, body.pieces, body.hs, body.declared_value, body.docs_provided, body.prefer || "balance");
        });
      case "plan":
        return guard(function () {
          return planShipment(body.country, body.weight_kg, body.length, body.width, body.height,
            body.declared_value, body.hs, body.mode, body.pieces, body.docs_provided, body.prefer || "balance");
        });
      case "clearance":
        return guard(function () {
          return estimate(body.country, body.mode, body.hs, body.docs_provided, body.carrier, body.port, body.declared_value);
        });
      case "export":
        return guard(function () { return exportQuote(body); });
      case "carriers":
        return guard(function () {
          var code = "";
          if (params.country) {
            var entry = resolveCountry(params.country);
            if (!entry) throw new Error("未识别的目的国：" + params.country);
            code = entry.code;
          }
          var items = carriersData().filter(function (c) {
            if (code && (c.lanes || []).indexOf(code) < 0) return false;
            if (params.mode && !modeMatch(params.mode, c.modes[0])) return false;
            return true;
          }).map(function (c) {
            return {
              id: c.id, name: c.name, mode: c.modes[0], mode_name: MODE_CN[c.modes[0]] || "",
              service: c.service || "", lanes: c.lanes || [], ontime: c.ontime,
              own_clearance: c.own_clearance, dangerous: c.dangerous || [], max_declared: c.max_declared, notes: c.notes || ""
            };
          });
          return { count: items.length, carriers: items };
        });
      case "country":
        return guard(function () {
          var entry = resolveCountry(params.country);
          if (!entry) throw new Error("未识别的目的国：" + (params.country || "（空）"));
          return {
            code: entry.code, name: entry.name, region: entry.region || "", vat_rate: entry.vat_rate,
            vat_name: entry.vat_name, duty_de_minimis_cny: entry.duty_de_minimis_cny,
            vat_de_minimis_cny: entry.vat_de_minimis_cny, clearance_days: entry.clearance_days || {},
            docs: entry.docs || [], peak_months: entry.peak_months || [], notes: entry.notes || ""
          };
        });
      case "ports":
        return guard(function () {
          var ports = portsData();
          if (params.port) {
            for (var i = 0; i < ports.length; i++) {
              if (String(params.port).toUpperCase() === ports[i].code || String(params.port).indexOf(ports[i].name) >= 0) {
                return { found: true, port: ports[i], advice: "拥堵指数已计入清关时效预估；建议预留缓冲或改走邻近口岸" };
              }
            }
            throw new Error("未收录的口岸：" + params.port);
          }
          var top = ports.slice().sort(function (a, b) { return Number(b.congestion_days) - Number(a.congestion_days); }).slice(0, 5);
          return { found: true, top_congested: top, total: ports.length };
        });
      case "hs":
        return { ok: true, data: { found: matchHsCandidates(params.keyword, params.hs, params.limit || 5).length > 0, candidates: matchHsCandidates(params.keyword, params.hs, params.limit || 5) } };
      case "duty":
        return guard(function () {
          var entry = resolveCountry(params.country);
          if (!entry) throw new Error("未识别的目的国：" + (params.country || "（空）"));
          var hsEntry = findHsEntry("", params.hs);
          if (!hsEntry && params.hs) throw new Error("未收录的 HS 编码：" + params.hs);
          var out = computeDuty(hsEntry, entry, params.declared_value, params.freight || 0);
          out.docs = requiredDocs(hsEntry, entry);
          return out;
        });
      case "stats":
        return stats();
      case "quotes": {
        var rows = quoteRows();
        var limit = Math.max(1, Math.min(Number(params.limit) || 20, 200));
        return { ok: true, data: { total: rows.length, rows: rows.slice(-limit) } };
      }
      case "config":
        if (params.method === "POST") {
          logEvent({ type: "config_save" });
          return { ok: true, updated: [], added: [], need_restart: true, offline: true };
        }
        return {
          env: {
            LLM_MODEL: "离线规则引擎", LLM_BASE_URL: "", LLM_API_KEY_SET: false,
            AGENT_PERMISSION_MODE: "plan", TOOL_NET_POLICY: "allowlist", TOOL_NET_ALLOWLIST: "", MAX_TOOL_CALLS: "12"
          },
          writable: [], need_restart: false, offline: true
        };
      case "debug/tail": {
        var ev = readJSON("logi_events", []);
        var n = Math.max(1, Math.min(Number(params.n) || 30, 200));
        return { events: ev.slice(-n) };
      }
      case "upload":
        return { ok: false, error: "离线版暂不支持文件上传，请在电脑端使用" };
      default:
        return { ok: false, error: "未实现的接口：" + path };
    }
  }

  global.Engine = {
    compare: compare,
    planShipment: planShipment,
    estimate: estimate,
    quoteAll: quoteAll,
    computeDuty: computeDuty,
    resolveCountry: resolveCountry,
    findHsEntry: findHsEntry,
    lookupHsBrief: lookupHsBrief,
    parseRequest: parseRequest,
    ask: ask,
    handleApi: handleApi,
    stats: stats,
    quoteRows: quoteRows,
    logEvent: logEvent
  };
})(window);
