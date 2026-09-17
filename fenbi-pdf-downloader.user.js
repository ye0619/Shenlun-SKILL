// ==UserScript==
// @name         粉笔试卷批量 PDF 下载器
// @namespace    https://local/fenbi-pdf-batch
// @version      1.4.0
// @description  登录粉笔网页版后，批量下载行测/申论等试卷的 PDF（历年真题、每日演练、模考等）。接口已按粉笔网页版真实接口校准（tiku.fenbi.com / urlimg.fenbi.com），并内置抓包自适配兜底。
// @author       you
// @match        https://www.fenbi.com/*
// @match        https://fenbi.com/*
// @grant        GM_download
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// @noframes
// ==/UserScript==

/*
 * 使用前提：已在粉笔网页版（www.fenbi.com）登录（浏览器里保持登录态即可）。
 * 安装 Tampermonkey 后把本文件拖入即可。页面右下角会出现「📥 粉笔PDF下载」按钮。
 *
 * 已校准的真实接口（2026-08 抓取自粉笔网页版 SPA 前端代码）：
 *   - 子分类：GET https://tiku.fenbi.com/api/{course}/subLabels
 *   - 试卷列表（依次尝试，labelId=0 表示全部）：
 *       GET https://tiku.fenbi.com/api/{course}/papers/v2?labelId={labelId}&toPage={page}&pageSize={pageSize}
 *       GET https://tiku.fenbi.com/api/{course}/papers?filter=review&labelId={labelId}&toPage={page}&pageSize={pageSize}
 *   - 真题 PDF（真实机制，ti 打印页同款）：
 *       GET https://tiku.fenbi.com/combine/exercise/getPaperSolution?format=html&key={combineKey}&routecs={course}&paperId={paperId}&checkId={encodeCheckInfo}
 *       → 响应里 switchVO.pdf.urls 即 PDF 地址
 *   - 兜底 PDF：GET https://urlimg.fenbi.com/api/pdf/tiku/{course}/{type}/{id}   （type: paper/papers/exercise/quiz/sheet）
 *   - course: xingce=行测, shenlun=申论；真题的 combineKey/encodeCheckInfo 来自试卷列表项
 * 上述接口均需登录（未登录返回 401）。
 *
 * 如果粉笔改版导致默认接口失效：打开「抓包」标签，在粉笔页面上正常浏览题库/下载一次 PDF，
 * 然后点「从抓包记录生成配置」即可自动适配。
 */

(function () {
  'use strict';

  // ================================================================
  // 常量与默认配置
  // ================================================================

  const LS_KEY = 'fbpdf_cfg_v2';

  const DEFAULT_CONFIG = {
    api: {
      tiku: 'https://tiku.fenbi.com/api',   // 题库接口前缀（对应前端配置 api.tikuApi）
      pdf: 'https://urlimg.fenbi.com'       // PDF 接口前缀（对应前端配置 downloadUrl）
    },
    courses: [                               // 一级分类（课程代码），可增删
      { code: 'xingce', name: '行测' },
      { code: 'shenlun', name: '申论' }
    ],
    subLabels: {                             // 二级分类（国考/省考/联考…）
      method: 'GET',
      path: '/{course}/subLabels',
      itemsPath: '',                         // 空字符串 = 响应本身就是数组
      idField: 'labelId',
      nameField: 'name',
      childrenField: 'childrenLabels',
      maxDepth: 3
    },
    papers: {
      method: 'GET',
      // 依次尝试的试卷列表接口：行测网页端用 v2；申论网页端用 filter=review 变体
      paths: [
        '/{course}/papers/v2?labelId={labelId}&toPage={page}&pageSize={pageSize}',
        '/{course}/papers?filter=review&labelId={labelId}&toPage={page}&pageSize={pageSize}'
      ],
      labelParam: 'labelId',                 // 选中二级分类时追加的查询参数（模板里没有 {labelId} 时使用）
      itemsPath: 'list',
      totalPath: 'pageInfo.totalItem',
      idField: 'paperId',
      nameField: 'name',
      pageSize: 15,
      maxPages: 300
    },
    pdf: {
      // 优先从试卷对象里直接找这些字段当 PDF 地址
      paperUrlFields: ['pdfUrl', 'pdf', 'downloadUrl', 'resourceUrl'],
      // 真题 PDF 的真实来源：试卷信息接口（ti 打印页同款），响应里带 switchVO.pdf.urls
      solution: {
        enabled: true,
        method: 'GET',
        base: 'https://tiku.fenbi.com',
        path: '/combine/exercise/getPaperSolution?format={format}&key={key}&routecs={course}&paperId={paperId}&checkId={checkId}',
        format: 'html'
      },
      // 依次尝试的 PDF 接口（拼在 api.pdf 之后）；响应可能是：直接返回 PDF 流 / 302 跳转 / JSON 带 PDF 地址
      candidates: [
        { method: 'GET', path: '/api/pdf/tiku/{course}/paper/{id}', urlPath: '' },
        { method: 'GET', path: '/api/pdf/tiku/{course}/papers/{id}', urlPath: '' },
        { method: 'GET', path: '/api/pdf/tiku/{course}/exercise/{id}', urlPath: '' },
        { method: 'GET', path: '/api/pdf/tiku/{course}/quiz/{id}', urlPath: '' },
        { method: 'GET', path: '/api/pdf/tiku/{course}/sheet/{id}', urlPath: '' }
      ],
      viewFallback: 'https://spa.fenbi.com/ti/view/paper/{id}'  // 全部失败时提示的打印页
    },
    download: {
      concurrency: 3,   // 同时下载数
      retry: 2,         // 失败重试次数
      timeoutMs: 120000 // 单个下载超时（毫秒）
    },
    capture: {
      enabled: true,
      maxRecords: 300,
      maxBody: 300000
    }
  };

  const state = {
    cfg: null,
    records: [],
    logs: [],
    subLabels: [],      // 当前课程的二级分类
    currentCourse: 'xingce',
    papers: [],
    selected: new Set(),
    downloading: false,
    queue: null,
    sh: null
  };

  // ================================================================
  // 小工具
  // ================================================================

  function log(msg) {
    const line = '[' + new Date().toLocaleTimeString() + '] ' + msg;
    state.logs.unshift(line);
    if (state.logs.length > 500) state.logs.length = 500;
    if (state.sh) renderLog();
  }

  function toast(msg, ms) {
    const sh = state.sh;
    if (!sh) return;
    let t = sh.getElementById('fbpdf-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'fbpdf-toast';
      sh.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._to);
    t._to = setTimeout(function () { t.classList.remove('show'); }, ms || 2500);
  }

  function tryParse(s) {
    try { return JSON.parse(s); } catch (e) { return null; }
  }

  function deepMerge(base, over) {
    for (const k of Object.keys(over || {})) {
      if (over[k] && typeof over[k] === 'object' && !Array.isArray(over[k]) &&
          base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) {
        deepMerge(base[k], over[k]);
      } else {
        base[k] = over[k];
      }
    }
    return base;
  }

  function loadCfg() {
    const c = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
    try {
      if (typeof GM_getValue === 'function') {
        const saved = tryParse(GM_getValue(LS_KEY, 'null'));
        if (saved && typeof saved === 'object') deepMerge(c, saved);
      }
    } catch (e) { /* 忽略 */ }
    return c;
  }

  function saveCfg() {
    try {
      if (typeof GM_setValue === 'function') GM_setValue(LS_KEY, JSON.stringify(state.cfg));
    } catch (e) { /* 忽略 */ }
  }

  function fillTemplate(str, vars) {
    if (str == null) return str;
    return String(str).replace(/\{(\w+)\}/g, function (m, k) {
      return vars[k] != null ? vars[k] : m;
    });
  }

  function resolvePath(obj, path) {
    if (!path || !obj) return obj;
    const segs = String(path).split('.');
    let cur = obj;
    for (const s of segs) {
      if (cur == null) return undefined;
      cur = cur[s];
    }
    return cur;
  }

  function firstArray(obj, depth, seen) {
    if (depth <= 0 || !obj || typeof obj !== 'object') return null;
    seen = seen || new Set();
    if (seen.has(obj)) return null;
    seen.add(obj);
    if (Array.isArray(obj)) return obj;
    for (const k of Object.keys(obj)) {
      const r = firstArray(obj[k], depth - 1, seen);
      if (r) return r;
    }
    return null;
  }

  function findArrayPath(obj, keys, depth, seen, prefix) {
    if (depth <= 0 || !obj || typeof obj !== 'object') return null;
    seen = seen || new Set();
    prefix = prefix || '';
    if (seen.has(obj)) return null;
    seen.add(obj);
    if (Array.isArray(obj)) {
      const sample = obj.slice(0, 5);
      if (sample.length && sample.every(function (it) {
        return it && typeof it === 'object' && keys.every(function (k) { return k in it; });
      })) {
        return { arr: obj, path: prefix };
      }
    }
    for (const k of Object.keys(obj)) {
      const p = prefix ? prefix + '.' + k : k;
      const r = findArrayPath(obj[k], keys, depth - 1, seen, p);
      if (r) return r;
    }
    return null;
  }

  function findUrlPath(obj, depth, seen, prefix) {
    if (depth <= 0 || !obj || typeof obj !== 'object') return null;
    seen = seen || new Set();
    prefix = prefix || '';
    if (seen.has(obj)) return null;
    seen.add(obj);
    if (Array.isArray(obj)) {
      for (let i = 0; i < obj.length; i++) {
        const r = findUrlPath(obj[i], depth - 1, seen, prefix + '[' + i + ']');
        if (r) return r;
      }
      return null;
    }
    for (const k of Object.keys(obj)) {
      const v = obj[k];
      const p = prefix ? prefix + '.' + k : k;
      if (typeof v === 'string') {
        if (/\.pdf($|\?)/i.test(v) || (/pdf/i.test(v) && /^https?:\/\//i.test(v))) {
          return { path: p, value: v };
        }
      } else {
        const r = findUrlPath(v, depth - 1, seen, p);
        if (r) return r;
      }
    }
    return null;
  }

  function findUrl(obj, depth) {
    const r = findUrlPath(obj, depth || 8);
    return r ? r.value : null;
  }

  // 取对象字段，支持 a.b.c 点路径，返回第一个非空值
  function pick(obj, keys) {
    if (!obj || !keys) return undefined;
    for (const k of keys) {
      let v;
      if (k.indexOf('.') > 0) v = resolvePath(obj, k);
      else v = obj[k];
      if (v != null && v !== '') return v;
    }
    return undefined;
  }

  // DFS 找响应里的 switchVO.pdf.urls（真题 PDF 地址数组），兼容 switchVO.pdf.url 单值
  function findSwitchPdfUrls(obj, depth, seen) {
    if (depth <= 0 || !obj || typeof obj !== 'object') return null;
    seen = seen || new Set();
    if (seen.has(obj)) return null;
    seen.add(obj);
    if (obj.switchVO && obj.switchVO.pdf) {
      if (Array.isArray(obj.switchVO.pdf.urls) && obj.switchVO.pdf.urls.length) return obj.switchVO.pdf.urls;
      if (typeof obj.switchVO.pdf.url === 'string' && obj.switchVO.pdf.url) return [obj.switchVO.pdf.url];
    }
    for (const k of Object.keys(obj)) {
      const r = findSwitchPdfUrls(obj[k], depth - 1, seen);
      if (r) return r;
    }
    return null;
  }

  function normUrl(u) {
    u = String(u || '').trim();
    if (!u) return u;
    if (u.indexOf('//') === 0) return location.protocol + u;
    if (u.charAt(0) === '/') {
      const m = String(state.cfg.api.pdf).match(/^https?:\/\/[^/]+/);
      return (m ? m[0] : location.origin) + u;
    }
    return u;
  }

  function safeName(s) {
    return String(s || '未命名试卷').replace(/[\\/:*?"<>|\r\n\t]/g, '_').slice(0, 150);
  }

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // ================================================================
  // 接口调用（fetch，自动带登录 Cookie；对 tiku/urlimg 跨域由粉笔自身 CORS 允许）
  // ================================================================

  async function callFb(base, method, path, bodyStr, opts) {
    opts = opts || {};
    const p = String(path || '');
    let url;
    if (/^https?:/i.test(p)) {
      url = p;
    } else if (p.indexOf('//') === 0) {
      url = location.protocol + p;            // 协议相对地址（//host/...）
    } else {
      url = String(base || '').replace(/\/+$/, '') + p;
    }
    const init = {
      method: method || 'GET',
      credentials: 'include',
      redirect: opts.redirect || 'manual'
    };
    // 仅带请求体时才设置 Content-Type，GET 请求保持简单请求，避免 CORS 预检
    if (bodyStr) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = bodyStr;
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      throw new Error('请求失败（网络/CORS/被拦截）：' + url + ' —— ' + e.message);
    }
    if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
      let loc = null;
      try { loc = res.headers.get('location'); } catch (e) { /* 忽略 */ }
      if (loc) return { type: 'url', url: loc, status: res.status };
      if (res.type === 'opaqueredirect') {
        throw new Error('请求被跨域重定向且无法跟随：' + url);
      }
    }
    const ct = (res.headers.get('content-type') || '');
    if (/pdf|octet-stream/i.test(ct)) {
      return { type: 'pdf', blob: await res.blob(), status: res.status };
    }
    let text = '';
    try { text = await res.text(); } catch (e) {
      throw new Error('读取响应失败：' + url + ' —— ' + e.message);
    }
    return { type: 'json', json: tryParse(text), text: text, status: res.status };
  }

  // ================================================================
  // 业务：二级分类 / 试卷列表 / PDF 解析
  // ================================================================

  async function loadSubLabels(course) {
    const cfg = state.cfg.subLabels;
    const path = fillTemplate(cfg.path, { course: course });
    const r = await callFb(state.cfg.api.tiku, cfg.method, path, null, { redirect: 'follow' });
    if (r.status === 401 || r.status === 403) throw new Error('未登录或登录已过期，请先在粉笔网页版登录');
    if (r.type !== 'json' || !r.json) throw new Error('分类接口返回异常（HTTP ' + r.status + '）');
    let arr = resolvePath(r.json, cfg.itemsPath);
    if (!Array.isArray(arr)) {
      arr = (findArrayPath(r.json, [cfg.idField, cfg.nameField], 8) || {}).arr || firstArray(r.json, 8) || [];
    }
    return arr.map(function (it) {
      return {
        id: it[cfg.idField] != null ? it[cfg.idField] : (it.id != null ? it.id : it.labelId),
        name: it[cfg.nameField] != null ? it[cfg.nameField] : (it.name || '未知分类'),
        children: (it[cfg.childrenField] || it.children || []).map(function (c) {
          return {
            id: c[cfg.idField] != null ? c[cfg.idField] : (c.id != null ? c.id : c.labelId),
            name: c[cfg.nameField] != null ? c[cfg.nameField] : (c.name || '未知'),
            children: c[cfg.childrenField] || c.children || []
          };
        })
      };
    });
  }

  function flattenLabels(list, maxDepth) {
    const out = [];
    const walk = function (items, path, d) {
      for (const it of items) {
        const label = path ? path + ' / ' + it.name : it.name;
        const kids = (it.children && it.children.length) ? it.children : [];
        if (kids.length && d < maxDepth) walk(kids, label, d + 1);
        else out.push({ id: it.id, name: label, raw: it });
      }
    };
    walk(list || [], '', 1);
    return out;
  }

  async function fetchPapersByTemplate(tpl, course, labelId, cfg, onPage) {
    const list = [];
    const seen = new Set();
    for (let page = 0; page < cfg.maxPages; page++) {
      const vars = { course: course, page: page, pageSize: cfg.pageSize };
      if (String(tpl).indexOf('{labelId}') >= 0) {
        vars.labelId = labelId != null ? labelId : 0;   // labelId=0 表示不过滤
      }
      let path = fillTemplate(tpl, vars);
      if (String(tpl).indexOf('{labelId}') < 0 && labelId != null && cfg.labelParam) {
        path += (path.indexOf('?') >= 0 ? '&' : '?') + cfg.labelParam + '=' + encodeURIComponent(labelId);
      }
      const r = await callFb(state.cfg.api.tiku, cfg.method, path, null, { redirect: 'follow' });
      if (r.status === 401 || r.status === 403) throw new Error('未登录或登录已过期，请先在粉笔网页版登录');
      if (r.type !== 'json' || !r.json) throw new Error('试卷列表接口返回异常（HTTP ' + r.status + '）：' + path);
      let items = resolvePath(r.json, cfg.itemsPath);
      if (!Array.isArray(items)) {
        items = (findArrayPath(r.json, [cfg.idField, cfg.nameField], 8) || {}).arr || firstArray(r.json, 8) || [];
      }
      let added = 0;
      for (const it of items) {
        const id = it[cfg.idField] != null ? it[cfg.idField] : (it.id != null ? it.id : it.paperId);
        const name = it[cfg.nameField] != null ? it[cfg.nameField] : (it.name || '未命名试卷');
        if (id == null || seen.has(String(id))) continue;
        seen.add(String(id));
        list.push({ id: id, name: String(name), course: course, raw: it });
        added++;
      }
      if (onPage) onPage(page, added);
      const total = cfg.totalPath ? resolvePath(r.json, cfg.totalPath) : null;
      if (typeof total === 'number' && list.length >= total) break;
      if (added === 0) break;
    }
    return list;
  }

  // 依次尝试多个试卷列表接口变体（行测 v2 / 申论 filter=review），第一个有数据的生效
  async function loadPapers(course, labelId, onPage) {
    const cfg = state.cfg.papers;
    const templates = (cfg.paths && cfg.paths.length) ? cfg.paths : (cfg.path ? [cfg.path] : []);
    if (!templates.length) throw new Error('未配置试卷列表接口（papers.paths）');
    let lastErr = null;
    for (const tpl of templates) {
      const shown = fillTemplate(tpl, { course: course, page: 0, pageSize: cfg.pageSize, labelId: labelId != null ? labelId : 0 });
      try {
        const list = await fetchPapersByTemplate(tpl, course, labelId, cfg, onPage);
        if (list.length) return list;
        lastErr = new Error('列表为空');
        log('试卷列表变体无数据，换下一个：' + shown);
      } catch (e) {
        lastErr = e;
        log('试卷列表变体失败：' + shown + ' —— ' + e.message);
      }
    }
    throw lastErr || new Error('所有试卷列表接口均未返回数据');
  }

  async function resolvePdfFor(paper) {
    const cfg = state.cfg;
    // 1) 试卷对象自带 PDF 字段
    for (const f of cfg.pdf.paperUrlFields) {
      const v = paper.raw ? paper.raw[f] : undefined;
      if (typeof v === 'string' && v) return { url: normUrl(v) };
    }
    // 2) 试卷对象已带 switchVO.pdf.urls（真题信息里可能直接有）
    if (paper.raw) {
      const direct = findSwitchPdfUrls(paper.raw, 5);
      if (direct && direct.length) return { urls: direct };
    }
    let lastErr = null;
    // 3) 真题 PDF 真实来源：getPaperSolution（ti 打印页同款接口）
    const sol = cfg.pdf.solution;
    if (sol && sol.enabled) {
      const key = pick(paper.raw, ['combineKey', 'exerciseKey', 'key', 'exercise.key', 'exerciseKeyV2']);
      const checkId = pick(paper.raw, ['encodeCheckInfo', 'checkId']);
      if (key != null && checkId != null) {
        const path = fillTemplate(sol.path, {
          format: sol.format || 'html',
          key: key,
          course: paper.course || 'xingce',
          paperId: paper.id,
          checkId: checkId
        });
        try {
          const r = await callFb(sol.base, sol.method || 'GET', path, null, { redirect: 'follow' });
          if (r.status === 401 || r.status === 403) throw new Error('未登录或登录已过期（PDF信息接口）');
          if (r.type === 'json' && r.json) {
            const urls = findSwitchPdfUrls(r.json, 12);
            if (urls && urls.length) {
              log('getPaperSolution 命中 ' + paper.name + '（' + urls.length + ' 个PDF）');
              return { urls: urls };
            }
            const u = findUrl(r.json, 10);
            if (u) return { url: normUrl(u) };
          }
          throw new Error('PDF信息接口未返回下载地址（可能无权限或次数用完）');
        } catch (e) {
          lastErr = e;
          log('getPaperSolution 失败：' + paper.name + ' —— ' + e.message);
        }
      }
    }
    // 4) 依次尝试 urlimg 上的 PDF 接口
    for (const cand of cfg.pdf.candidates) {
      const path = fillTemplate(cand.path, { course: paper.course || 'xingce', id: paper.id });
      try {
        const r = await callFb(cfg.api.pdf, cand.method || 'GET', path, null);
        if (r.status === 401 || r.status === 403) {
          const body = String(r.text || '').trim().slice(0, 60);
          throw new Error('未登录或登录已过期（' + (body || '无权限') + '）');
        }
        if (r.type === 'url' && r.url) return { url: normUrl(r.url) };
        if (r.type === 'pdf') return { blob: r.blob };
        if (r.type === 'json' && r.json) {
          const u = cand.urlPath ? resolvePath(r.json, cand.urlPath) : findUrl(r.json, 8);
          if (u) return { url: normUrl(u) };
        }
      } catch (e) { lastErr = e; }
    }
    const fb = cfg.pdf.viewFallback ? fillTemplate(cfg.pdf.viewFallback, { id: paper.id }) : '';
    throw new Error('未能获取PDF地址' + (lastErr ? '（' + lastErr.message + '）' : '') + (fb ? '。可打开打印页自行保存：' + fb : ''));
  }

  // ================================================================
  // 下载
  // ================================================================

  function saveBlob(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 60000);
  }

  function gmDownload(url, name) {
    return new Promise(function (resolve) {
      let settled = false;
      const fin = function (r) { if (!settled) { settled = true; resolve(r); } };
      const cap = (state.cfg.download.timeoutMs || 120000) + 8000;
      const to = setTimeout(function () {
        fin({ ok: false, err: '下载超时（可能被浏览器拦截了多个下载）' });
      }, cap);
      try {
        GM_download({
          url: url,
          name: name,
          saveAs: false,
          timeout: state.cfg.download.timeoutMs || 120000,
          onload: function () { clearTimeout(to); fin({ ok: true }); },
          onerror: function (e) { clearTimeout(to); fin({ ok: false, err: 'GM_download失败: ' + ((e && e.error) || '未知') }); },
          ontimeout: function () { clearTimeout(to); fin({ ok: false, err: 'GM_download超时' }); }
        });
      } catch (e) {
        clearTimeout(to);
        fin({ ok: false, err: 'GM_download异常: ' + e.message });
      }
    });
  }

  async function savePdf(pdf, name) {
    if (pdf.url) {
      if (typeof GM_download === 'function') {
        return await gmDownload(pdf.url, name);
      }
      try {
        const res = await fetch(pdf.url, { credentials: 'include' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const blob = await res.blob();
        saveBlob(blob, name);
        return { ok: true };
      } catch (e) {
        return { ok: false, err: '无法直接保存（跨域或网络错误）。建议用 Tampermonkey 安装本脚本；或手动打开: ' + pdf.url };
      }
    }
    if (pdf.blob) {
      saveBlob(pdf.blob, name);
      return { ok: true };
    }
    return { ok: false, err: '没有可用的 PDF 数据' };
  }

  function makeQueue(limit, onProgress) {
    const q = { active: 0, pending: [], cancelled: false, done: 0, total: 0 };
    q.push = function (task) {
      return new Promise(function (res, rej) {
        q.pending.push({ task: task, res: res, rej: rej });
        q.pump();
      });
    };
    q.pump = function () {
      while (q.active < limit && q.pending.length && !q.cancelled) {
        const job = q.pending.shift();
        q.active++;
        job.task()
          .then(job.res, job.rej)
          .finally(function () {
            q.active--;
            q.done++;
            if (onProgress) onProgress();
            if (!q.cancelled) q.pump();
          });
      }
    };
    q.cancel = function () { q.cancelled = true; q.pending.length = 0; };
    return q;
  }

  async function downloadPapers(items) {
    if (!items.length) { toast('请先勾选要下载的试卷'); return; }
    if (state.downloading) { toast('已有下载任务进行中'); return; }
    state.downloading = true;
    const sh = state.sh;
    const dlBar = sh.getElementById('dlBar');
    const dlFill = sh.getElementById('dlFill');
    const dlInfo = sh.getElementById('dlInfo');
    const dlStatus = sh.getElementById('dlStatus');
    dlBar.hidden = false;
    dlStatus.innerHTML = '';

    const statusRows = new Map();
    items.forEach(function (item) {
      const row = document.createElement('div');
      row.className = 'dlrow';
      row.innerHTML = '<span class="dlname"></span><span class="dlst"></span><a class="dllink" target="_blank" hidden>打印页</a>';
      row.querySelector('.dlname').textContent = item.name;
      row.querySelector('.dlst').textContent = '排队中';
      const link = row.querySelector('.dllink');
      const fb = state.cfg.pdf.viewFallback ? fillTemplate(state.cfg.pdf.viewFallback, { id: item.id }) : '';
      if (fb) link.href = fb;
      dlStatus.appendChild(row);
      statusRows.set(String(item.id), { row: row, st: row.querySelector('.dlst'), link: link });
    });

    const setSt = function (item, text, cls) {
      const cell = statusRows.get(String(item.id));
      if (cell) {
        cell.st.textContent = text;
        cell.st.className = 'dlst' + (cls ? ' ' + cls : '');
        if (cls === 'fail') cell.link.hidden = false;
      }
    };
    const updateBar = function () {
      const done = state.queue ? state.queue.done : 0;
      const total = items.length;
      const pct = total ? Math.round(done / total * 100) : 0;
      dlFill.style.width = pct + '%';
      dlInfo.textContent = '完成 ' + done + ' / ' + total;
      if (done >= total) {
        dlInfo.textContent = '全部完成 ' + done + ' / ' + total;
        state.downloading = false;
        toast('下载任务结束：成功 ' + done + ' 份');
      }
    };

    const queue = makeQueue(state.cfg.download.concurrency, function () { updateBar(); });
    state.queue = queue;

    for (const item of items) {
      if (queue.cancelled) break;
      queue.push(async function () {
        setSt(item, '下载中', 'run');
        const nameBase = safeName(item.name);
        const retry = state.cfg.download.retry || 0;
        for (let attempt = 0; attempt <= retry; attempt++) {
          try {
            const pdf = await resolvePdfFor(item);
            // 多 PDF（分卷）逐个下载
            if (pdf.urls && pdf.urls.length) {
              for (let i = 0; i < pdf.urls.length; i++) {
                const nm = nameBase + (pdf.urls.length > 1 ? '_' + (i + 1) : '') + '.pdf';
                const r = await savePdf({ url: pdf.urls[i] }, nm);
                if (!r.ok) throw new Error(r.err);
              }
              setSt(item, '完成', 'ok');
              log('下载完成: ' + item.name + '（' + pdf.urls.length + ' 个文件）');
              return;
            }
            const r = await savePdf(pdf, nameBase + '.pdf');
            if (r.ok) { setSt(item, '完成', 'ok'); log('下载完成: ' + item.name); return; }
            if (attempt < retry) { setSt(item, '重试(' + (attempt + 1) + ')', 'run'); await sleep(800); }
            else { setSt(item, '失败: ' + r.err, 'fail'); log('下载失败: ' + item.name + ' -> ' + r.err); }
          } catch (e) {
            if (attempt < retry) { setSt(item, '重试(' + (attempt + 1) + ')', 'run'); await sleep(800); }
            else { setSt(item, '失败: ' + e.message, 'fail'); log('下载失败: ' + item.name + ' -> ' + e.message); }
          }
        }
      });
    }
  }

  // ================================================================
  // 抓包：监听页面自身发出的接口请求
  // ================================================================

  function isInteresting(url) {
    return /\.pdf($|\?)/i.test(url) ||
      (/\/api\//i.test(url) && /(tiku|paper|category|pdf|download|practice|exam|label|daily)/i.test(url)) ||
      /\/combine\//i.test(url);
  }

  function addRecord(rec) {
    state.records.unshift(rec);
    if (state.records.length > state.cfg.capture.maxRecords) state.records.length = state.cfg.capture.maxRecords;
    renderCaptureList();
  }

  function installCapture() {
    if (!state.cfg.capture.enabled) return;
    try {
      const oOpen = XMLHttpRequest.prototype.open;
      const oSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function (m, u) {
        this.__fbp = { method: String(m || 'GET').toUpperCase(), url: String(u || '') };
        return oOpen.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function (body) {
        const x = this;
        if (x.__fbp) x.__fbp.reqBody = (typeof body === 'string' && body.length < 20000) ? body : null;
        this.addEventListener('loadend', function () {
          const meta = x.__fbp;
          if (!meta || !meta.url || !isInteresting(meta.url)) return;
          let text = '';
          try { text = x.responseText || ''; } catch (e) { /* 忽略 */ }
          if (text.length > state.cfg.capture.maxBody) text = text.slice(0, state.cfg.capture.maxBody);
          addRecord({
            kind: 'xhr', method: meta.method, url: meta.url, reqBody: meta.reqBody,
            status: x.status, responseText: text,
            ctype: (x.getResponseHeader && x.getResponseHeader('content-type')) || ''
          });
        });
        return oSend.apply(this, arguments);
      };
    } catch (e) { log('安装 XHR 抓包失败: ' + e.message); }

    try {
      const oFetch = window.fetch;
      window.fetch = function (input, init) {
        const url = typeof input === 'string' ? input : (input && input.url) || '';
        const method = String(
          (init && init.method) ||
          (typeof input === 'string' ? 'GET' : (input && input.method) || 'GET')
        ).toUpperCase();
        const p = oFetch.apply(this, arguments);
        if (isInteresting(url)) {
          p.then(function (res) {
            try {
              const clone = res.clone();
              clone.text().then(function (t) {
                if (t.length > state.cfg.capture.maxBody) t = t.slice(0, state.cfg.capture.maxBody);
                addRecord({
                  kind: 'fetch', method: method, url: url,
                  reqBody: (init && typeof init.body === 'string') ? init.body.slice(0, 20000) : null,
                  status: res.status, responseText: t,
                  ctype: res.headers.get('content-type') || ''
                });
              }).catch(function () { /* 二进制响应无法读文本 */ });
            } catch (e) { /* 忽略 */ }
          }).catch(function () { /* 忽略 */ });
        }
        return p;
      };
    } catch (e) { log('安装 fetch 抓包失败: ' + e.message); }
  }

  // ---- 从抓包记录生成配置 ----

  const COURSE_RE = /\/(xingce|shenlun)(\/|$|\?)/;

  function courseOfUrl(u) {
    const m = String(u).match(COURSE_RE);
    return m ? m[1] : null;
  }

  function ensureCourse(code) {
    if (!code) return;
    if (!state.cfg.courses.some(function (c) { return c.code === code; })) {
      state.cfg.courses.push({ code: code, name: code });
    }
  }

  function stripHost(u) {
    return String(u).replace(/^(?:https?:)?\/\/[^/]+/, '');   // 去掉 //host 或 https://host
  }

  function stripBasePath(u, base) {
    try {
      const p = new URL(base).pathname;
      if (p && p !== '/' && String(u).indexOf(p) === 0) return String(u).slice(p.length);
    } catch (e) { /* 忽略 */ }
    return u;
  }

  function templatePath(u) {
    let s = stripBasePath(stripHost(u), state.cfg.api.tiku);
    s = s.replace(/\/(xingce|shenlun)\//, '/{course}/');
    s = s.replace(/([?&])(toPage|page|pageSize|size|labelId|categoryId)=(\d+)/g, function (m, p, key) {
      const ph = { toPage: 'page', page: 'page', pageSize: 'pageSize', size: 'pageSize', labelId: 'labelId', categoryId: 'categoryId' }[key];
      return p + key + '={' + ph + '}';
    });
    return s;
  }

  function templatePdfPath(u) {
    let s = stripBasePath(stripHost(u), state.cfg.api.pdf);
    s = s.replace(/\/(xingce|shenlun)\//, '/{course}/');
    s = s.replace(/\/(paper|papers|exercise|quiz|sheet)\/(\d+)/, '/$1/{id}');
    return s;
  }

  function genConfigFromRecords() {
    const recs = state.records.filter(function (r) {
      return r.status >= 200 && r.status < 400 && r.responseText;
    });
    const newCfg = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
    let found = 0;
    for (const rec of recs) {
      const json = tryParse(rec.responseText);
      if (!json || typeof json !== 'object') continue;
      const course = courseOfUrl(rec.url);

      // PDF 接口
      if (/\.pdf($|\?)/i.test(rec.url) || /\/api\/pdf\//i.test(rec.url) || findUrl(json, 10)) {
        const up = findUrlPath(json, 10);
        const path = templatePdfPath(rec.url);
        if (course) ensureCourse(course);
        if (path && /\/\{id\}/.test(path) && !newCfg.pdf.candidates.some(function (c) {
          return c.method === rec.method && c.path === path;
        })) {
          newCfg.pdf.candidates.unshift({ method: rec.method, path: path, urlPath: up ? up.path : '' });
          found++;
        }
      }

      // 试卷列表接口
      if (!newCfg.papers.autoSet) {
        const ap = findArrayPath(json, ['paperId', 'name'], 8) || findArrayPath(json, ['id', 'name'], 8);
        if (ap) {
          if (course) ensureCourse(course);
          const tpl = templatePath(rec.url);
          if (tpl && !newCfg.papers.paths.some(function (x) { return x === tpl; })) {
            newCfg.papers.paths.unshift(tpl);
          }
          newCfg.papers.method = rec.method;
          newCfg.papers.itemsPath = ap.path;
          newCfg.papers.autoSet = true;
          found++;
        }
      }

      // 二级分类接口
      if (!newCfg.subLabels.autoSet) {
        const ac = findArrayPath(json, ['labelId', 'name'], 8);
        if (ac) {
          if (course) ensureCourse(course);
          newCfg.subLabels.method = rec.method;
          newCfg.subLabels.path = templatePath(rec.url);
          newCfg.subLabels.itemsPath = ac.path;
          newCfg.subLabels.autoSet = true;
          found++;
        }
      }
    }
    delete newCfg.papers.autoSet;
    delete newCfg.subLabels.autoSet;
    if (!found) {
      log('未从抓包记录识别出接口。请先在粉笔页面操作：打开题库 → 试卷列表 → 打开一份试卷并点一次页面上的“下载PDF/下载试卷”，再回来点此按钮。');
      toast('没有识别到可用接口，详见日志');
      return;
    }
    state.cfg = newCfg;
    saveCfg();
    renderConfigTab();
    log('已根据抓包记录生成并应用配置（' + found + ' 项）。可到「配置」标签查看。');
    toast('已生成配置（' + found + ' 项）');
  }

  // ================================================================
  // UI
  // ================================================================

  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }
  #wrap { position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; font-size: 13px; color: #222; }
  #toggle { border: none; cursor: pointer; padding: 9px 16px; border-radius: 20px; background: #e83c4b; color: #fff; font-size: 13px; font-weight: 600; box-shadow: 0 3px 10px rgba(0,0,0,.25); }
  #toggle:hover { background: #c92f3d; }
  #panel { width: 480px; max-width: 96vw; max-height: 80vh; background: #fff; border: 1px solid #ddd; border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.2); display: flex; flex-direction: column; overflow: hidden; }
  #panel[hidden] { display: none; }
  .head { padding: 8px 12px; background: #e83c4b; color: #fff; font-weight: 600; display: flex; align-items: center; justify-content: space-between; cursor: move; }
  .head .x { cursor: pointer; opacity: .8; }
  .tabs { display: flex; background: #f6f6f6; border-bottom: 1px solid #e5e5e5; }
  .tab { padding: 7px 16px; cursor: pointer; border: none; background: transparent; font-size: 13px; color: #555; border-bottom: 2px solid transparent; }
  .tab.on { background: #fff; color: #e83c4b; font-weight: 600; border-bottom-color: #e83c4b; }
  .body { padding: 10px; overflow: auto; flex: 1; }
  .row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
  select, input[type=text] { padding: 5px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; max-width: 190px; }
  button { padding: 5px 10px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 12px; }
  button:hover { border-color: #e83c4b; color: #e83c4b; }
  button.primary { background: #e83c4b; border-color: #e83c4b; color: #fff; font-weight: 600; }
  button.primary:hover { background: #c92f3d; color: #fff; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .list { border: 1px solid #eee; border-radius: 6px; max-height: 260px; overflow: auto; background: #fafafa; margin-bottom: 8px; }
  .list .item { display: flex; align-items: flex-start; gap: 6px; padding: 5px 8px; border-bottom: 1px solid #f0f0f0; font-size: 12px; }
  .list .item:last-child { border-bottom: none; }
  .list .item:hover { background: #fff7f8; }
  .list .item label { display: flex; gap: 6px; cursor: pointer; word-break: break-all; }
  .count { color: #888; font-size: 12px; }
  #dlBar { margin: 6px 0; }
  .bar { height: 8px; background: #eee; border-radius: 4px; overflow: hidden; }
  #dlFill { height: 100%; width: 0; background: #e83c4b; transition: width .2s; }
  #dlInfo { font-size: 12px; color: #666; margin-top: 4px; }
  .dlrow { display: flex; justify-content: space-between; gap: 8px; padding: 3px 4px; font-size: 12px; border-bottom: 1px dashed #f0f0f0; }
  .dlname { flex: 1; word-break: break-all; }
  .dlst { white-space: nowrap; color: #888; }
  .dlst.ok { color: #22a06b; }
  .dlst.fail { color: #d4380d; }
  .dlst.run { color: #0958d9; }
  .dlrow .dllink { color: #0958d9; font-size: 11px; text-decoration: none; border: 1px solid #b7d4f7; border-radius: 4px; padding: 1px 6px; }
  .dlrow .dllink:hover { background: #eaf3ff; }
  textarea { width: 100%; height: 200px; font-family: Consolas, Menlo, monospace; font-size: 12px; border: 1px solid #ccc; border-radius: 6px; padding: 6px; }
  .rec { border: 1px solid #eee; border-radius: 6px; margin-bottom: 6px; font-size: 12px; background: #fafafa; }
  .rec .rline { display: flex; gap: 6px; align-items: center; padding: 6px 8px; cursor: pointer; flex-wrap: wrap; }
  .rec .m { font-weight: 700; color: #e83c4b; min-width: 40px; }
  .rec .u { color: #0958d9; word-break: break-all; flex: 1; }
  .rec .s { color: #888; }
  .rec .detail { display: none; padding: 6px 8px; border-top: 1px dashed #eee; }
  .rec.open .detail { display: block; }
  .rec pre { max-height: 180px; overflow: auto; background: #111; color: #7ee787; padding: 6px; border-radius: 4px; font-size: 11px; white-space: pre-wrap; word-break: break-all; margin: 4px 0; }
  .rec .acts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  #logbox { background: #111; color: #9cdcfe; font-family: Consolas, Menlo, monospace; font-size: 11px; padding: 6px; border-radius: 6px; max-height: 300px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
  #fbpdf-toast { position: fixed; left: 50%; bottom: 30px; transform: translateX(-50%); background: rgba(0,0,0,.8); color: #fff; padding: 8px 16px; border-radius: 6px; font-size: 13px; z-index: 2147483647; opacity: 0; transition: opacity .25s; pointer-events: none; max-width: 80vw; word-break: break-all; }
  #fbpdf-toast.show { opacity: 1; }
  .hint { color: #888; font-size: 12px; line-height: 1.6; }
  `;

  function buildPanel() {
    const host = document.createElement('div');
    host.id = 'fbpdf-host';
    const sh = host.attachShadow({ mode: 'open' });
    sh.innerHTML =
      '<style>' + CSS + '</style>' +
      '<div id="wrap">' +
      '  <div id="panel" hidden>' +
      '    <div class="head"><span>📥 粉笔试卷 PDF 批量下载</span><span class="x" data-act="toggle">✕</span></div>' +
      '    <div class="tabs">' +
      '      <button class="tab on" data-tabbtn="papers">试卷</button>' +
      '      <button class="tab" data-tabbtn="config">配置</button>' +
      '      <button class="tab" data-tabbtn="capture">抓包</button>' +
      '      <button class="tab" data-tabbtn="log">日志</button>' +
      '    </div>' +
      '    <div class="body">' +
      '      <div data-pane="papers">' +
      '        <div class="row">' +
      '          <select id="selCat1"></select>' +
      '          <select id="selCat2"></select>' +
      '          <button class="primary" data-act="loadPapers">加载试卷列表</button>' +
      '        </div>' +
      '        <div class="row">' +
      '          <input type="text" id="inSearch" placeholder="按名称筛选…">' +
      '          <button data-act="checkAll">全选</button>' +
      '          <button data-act="uncheckAll">清空</button>' +
      '          <span class="count" id="selCount"></span>' +
      '        </div>' +
      '        <div class="list" id="paperList"></div>' +
      '        <div class="row">' +
      '          <button class="primary" data-act="downloadSel">⬇ 批量下载已选</button>' +
      '          <button data-act="stopDl">停止</button>' +
      '          <button data-act="dlCurrent">下载当前页试卷</button>' +
      '        </div>' +
      '        <div id="dlBar" hidden><div class="bar"><div id="dlFill"></div></div><div id="dlInfo"></div></div>' +
      '        <div class="list" id="dlStatus" style="max-height:180px"></div>' +
      '      </div>' +
      '      <div data-pane="config" hidden>' +
      '        <div class="hint">接口配置（JSON）。「{course}」「{id}」「{page}」「{pageSize}」「{labelId}」是占位符；course 取自行测= xingce / 申论= shenlun。<br>接口变了？去「抓包」标签点「从抓包记录生成配置」。</div>' +
      '        <textarea id="cfgText" spellcheck="false"></textarea>' +
      '        <div class="row">' +
      '          <button class="primary" data-act="saveCfg">保存配置</button>' +
      '          <button data-act="resetCfg">恢复默认</button>' +
      '          <button data-act="genCfg">从抓包记录生成配置</button>' +
      '        </div>' +
      '      </div>' +
      '      <div data-pane="capture" hidden>' +
      '        <div class="row">' +
      '          <button data-act="refreshCap">刷新</button>' +
      '          <button data-act="clearCap">清空记录</button>' +
      '          <button class="primary" data-act="genCfg">从抓包记录生成配置</button>' +
      '        </div>' +
      '        <div class="hint">在粉笔页面正常浏览题库、打开试卷、点页面上的“下载PDF/下载试卷”，这里会记录真实的接口请求。识别出接口后点上方按钮即可一键生成配置。</div>' +
      '        <div id="capList"></div>' +
      '      </div>' +
      '      <div data-pane="log" hidden>' +
      '        <div class="row"><button data-act="clearLog">清空日志</button></div>' +
      '        <div id="logbox"></div>' +
      '      </div>' +
      '    </div>' +
      '  </div>' +
      '  <button id="toggle">📥 粉笔PDF下载</button>' +
      '</div>';

    document.documentElement.appendChild(host);
    state.sh = sh;

    sh.getElementById('toggle').addEventListener('click', togglePanel);
    sh.addEventListener('click', function (ev) {
      const el = ev.target.closest('[data-act]');
      if (!el) return;
      const act = el.dataset.act;
      if (act === 'toggle') togglePanel();
      else if (act === 'loadPapers') onLoadPapers();
      else if (act === 'checkAll') checkAll(true);
      else if (act === 'uncheckAll') checkAll(false);
      else if (act === 'downloadSel') downloadPapers(currentSelection());
      else if (act === 'stopDl') { if (state.queue) state.queue.cancel(); state.downloading = false; toast('已请求停止（进行中的会继续完成）'); }
      else if (act === 'dlCurrent') onDlCurrent();
      else if (act === 'saveCfg') onSaveCfg();
      else if (act === 'resetCfg') onResetCfg();
      else if (act === 'genCfg') genConfigFromRecords();
      else if (act === 'refreshCap') renderCaptureList();
      else if (act === 'clearCap') { state.records = []; renderCaptureList(); }
      else if (act === 'clearLog') { state.logs = []; renderLog(); }
    });
    sh.addEventListener('change', function (ev) {
      if (ev.target.id === 'selCat1') onCat1Change();
      if (ev.target.closest && ev.target.closest('#paperList')) updateSelCount();
    });
    sh.addEventListener('input', function (ev) {
      if (ev.target.id === 'inSearch') renderPaperList();
    });

    sh.querySelectorAll('[data-tabbtn]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        sh.querySelectorAll('[data-tabbtn]').forEach(function (b) { b.classList.toggle('on', b === btn); });
        sh.querySelectorAll('[data-pane]').forEach(function (p) {
          p.hidden = p.dataset.pane !== btn.dataset.tabbtn;
        });
        if (btn.dataset.tabbtn === 'config') renderConfigTab();
        if (btn.dataset.tabbtn === 'capture') renderCaptureList();
        if (btn.dataset.tabbtn === 'log') renderLog();
      });
    });

    makeDraggable(sh.getElementById('panel'));

    log('脚本已加载。请确认已在粉笔网页版登录。');
    log('提示：先在粉笔页面打开「题库」浏览一下，可自动抓包学习接口。');
  }

  function togglePanel() {
    const p = state.sh.getElementById('panel');
    p.hidden = !p.hidden;
    if (!p.hidden) {
      renderCat1();
      renderCat2();
      renderPaperList();
    }
  }

  function makeDraggable(panel) {
    const head = panel.querySelector('.head');
    let sx = 0, sy = 0, ox = 0, oy = 0, drag = false;
    head.addEventListener('mousedown', function (e) {
      drag = true; sx = e.clientX; sy = e.clientY;
      const r = panel.getBoundingClientRect();
      ox = r.left; oy = r.top;
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!drag) return;
      panel.style.position = 'fixed';
      panel.style.left = (ox + e.clientX - sx) + 'px';
      panel.style.top = (oy + e.clientY - sy) + 'px';
    });
    document.addEventListener('mouseup', function () { drag = false; });
  }

  // ---- 试卷标签 ----

  function currentCourse() {
    const sh = state.sh;
    const v = sh.getElementById('selCat1').value;
    return v || (state.cfg.courses[0] && state.cfg.courses[0].code) || 'xingce';
  }

  async function onCat1Change() {
    state.currentCourse = currentCourse();
    state.subLabels = [];
    renderCat2();
    try {
      toast('正在加载「' + state.currentCourse + '」分类…');
      state.subLabels = await loadSubLabels(state.currentCourse);
      renderCat2();
      log('分类加载完成：' + state.currentCourse + ' 共 ' + state.subLabels.length + ' 个二级分类');
    } catch (e) {
      toast('分类加载失败：' + e.message);
      log('分类加载失败：' + e.message);
    }
  }

  async function onLoadPapers() {
    const sh = state.sh;
    const btn = sh.querySelector('[data-act="loadPapers"]');
    btn.disabled = true;
    try {
      const course = currentCourse();
      state.currentCourse = course;
      if (!state.subLabels.length) {
        toast('正在加载分类…');
        log('正在加载分类…');
        state.subLabels = await loadSubLabels(course);
        renderCat2();
      }
      const sel2 = sh.getElementById('selCat2').value;
      let labelId = null;
      if (sel2 && sel2 !== '__ALL__') labelId = sel2;

      log('加载试卷：' + course + (labelId ? ' / labelId=' + labelId : ' / 全部'));
      const list = await loadPapers(course, labelId, function (page, added) {
        toast('正在加载第 ' + (page + 1) + ' 页…（本页 ' + added + ' 份）');
      });
      state.papers = list;
      state.selected = new Set();
      renderPaperList();
      toast('共加载 ' + state.papers.length + ' 份试卷');
      log('共加载 ' + state.papers.length + ' 份试卷');
    } catch (e) {
      toast('加载失败：' + e.message);
      log('加载失败：' + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  function renderCat1() {
    const sh = state.sh;
    const sel = sh.getElementById('selCat1');
    const prev = sel.value;
    sel.innerHTML = '';
    (state.cfg.courses || []).forEach(function (c) {
      const o = document.createElement('option');
      o.value = c.code;
      o.textContent = c.name;
      sel.appendChild(o);
    });
    if (prev && Array.from(sel.options).some(function (o) { return o.value === prev; })) sel.value = prev;
  }

  function renderCat2() {
    const sh = state.sh;
    const sel2 = sh.getElementById('selCat2');
    const prev = sel2.value;
    sel2.innerHTML = '';
    const all = document.createElement('option');
    all.value = '__ALL__';
    all.textContent = '全部子分类';
    sel2.appendChild(all);
    flattenLabels(state.subLabels, state.cfg.subLabels.maxDepth).forEach(function (k) {
      const o = document.createElement('option');
      o.value = String(k.id);
      o.textContent = k.name;
      sel2.appendChild(o);
    });
    if (prev && Array.from(sel2.options).some(function (o) { return o.value === prev; })) sel2.value = prev;
  }

  function renderPaperList() {
    const sh = state.sh;
    const listEl = sh.getElementById('paperList');
    const kw = (sh.getElementById('inSearch').value || '').trim().toLowerCase();
    listEl.innerHTML = '';
    state.papers.forEach(function (p) {
      if (kw && p.name.toLowerCase().indexOf(kw) < 0) return;
      const div = document.createElement('div');
      div.className = 'item';
      const label = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.id = String(p.id);
      cb.checked = state.selected.has(String(p.id));
      const span = document.createElement('span');
      span.textContent = p.name;
      label.appendChild(cb);
      label.appendChild(span);
      div.appendChild(label);
      listEl.appendChild(div);
    });
    updateSelCount();
  }

  function checkAll(v) {
    const sh = state.sh;
    const kw = (sh.getElementById('inSearch').value || '').trim().toLowerCase();
    state.papers.forEach(function (p) {
      if (kw && p.name.toLowerCase().indexOf(kw) < 0) return;
      if (v) state.selected.add(String(p.id));
      else state.selected.delete(String(p.id));
    });
    renderPaperList();
  }

  function updateSelCount() {
    const sh = state.sh;
    state.selected = new Set();
    sh.querySelectorAll('#paperList input[type=checkbox]:checked').forEach(function (cb) {
      state.selected.add(cb.dataset.id);
    });
    sh.getElementById('selCount').textContent = '已选 ' + state.selected.size + ' / ' + state.papers.length;
  }

  function currentSelection() {
    return state.papers.filter(function (p) { return state.selected.has(String(p.id)); });
  }

  async function onDlCurrent() {
    const m = location.pathname.match(/\/paper\/(\d+)/);
    if (!m) { toast('当前页面没有识别到试卷 ID（需在试卷详情页，URL 含 /paper/数字）'); return; }
    const id = m[1];
    let name = (document.title || '').replace(/[_\-—]\s*粉笔.*$/, '').trim();
    if (!name || name.length > 120) name = 'paper-' + id;
    try {
      toast('正在获取当前试卷 PDF…');
      log('下载当前页试卷：' + name);
      const pdf = await resolvePdfFor({ id: id, name: name, course: state.currentCourse, raw: {} });
      const r = await savePdf(pdf, safeName(name) + '.pdf');
      toast(r.ok ? '开始下载：' + name : '下载失败：' + r.err);
      log(r.ok ? '开始下载：' + name : '下载失败：' + r.err);
    } catch (e) {
      toast('获取 PDF 失败：' + e.message);
      log('获取 PDF 失败：' + e.message);
    }
  }

  // ---- 配置标签 ----

  function renderConfigTab() {
    const sh = state.sh;
    const ta = sh.getElementById('cfgText');
    if (ta) ta.value = JSON.stringify(state.cfg, null, 2);
  }

  function onSaveCfg() {
    const sh = state.sh;
    const obj = tryParse(sh.getElementById('cfgText').value);
    if (!obj) { toast('配置不是合法的 JSON'); return; }
    state.cfg = deepMerge(JSON.parse(JSON.stringify(DEFAULT_CONFIG)), obj);
    saveCfg();
    renderConfigTab();
    toast('配置已保存');
    log('配置已保存');
  }

  function onResetCfg() {
    state.cfg = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
    saveCfg();
    renderConfigTab();
    toast('已恢复默认配置');
  }

  // ---- 抓包标签 ----

  function renderCaptureList() {
    const sh = state.sh;
    const box = sh.getElementById('capList');
    if (!box) return;
    box.innerHTML = '';
    if (!state.records.length) {
      box.innerHTML = '<div class="hint">暂无记录。请在粉笔页面打开题库/试卷列表，或点一次“下载PDF”。</div>';
      return;
    }
    const limit = 100;
    state.records.slice(0, limit).forEach(function (rec) {
      const div = document.createElement('div');
      div.className = 'rec';
      const head = document.createElement('div');
      head.className = 'rline';
      head.innerHTML = '<span class="m">' + rec.method + '</span>' +
        '<span class="u"></span>' +
        '<span class="s">' + rec.status + ' · ' + fmtSize((rec.responseText || '').length) + '</span>' +
        '<span style="color:#999">▸</span>';
      head.querySelector('.u').textContent = rec.url.replace(/^https?:\/\/[^/]+/, '');
      const detail = document.createElement('div');
      detail.className = 'detail';
      let pretty = rec.responseText;
      const j = tryParse(rec.responseText);
      if (j) { try { pretty = JSON.stringify(j, null, 2); } catch (e) { /* 忽略 */ } }
      if (pretty.length > 6000) pretty = pretty.slice(0, 6000) + '\n…(已截断)';
      detail.innerHTML =
        '<div class="hint">URL: ' + esc(rec.url) + '</div>' +
        (rec.reqBody ? '<div class="hint">请求体: ' + esc(rec.reqBody) + '</div>' : '') +
        '<pre></pre>' +
        '<div class="acts">' +
        '  <button data-recact="setPaper">设为试卷接口</button>' +
        '  <button data-recact="setCat">设为分类接口</button>' +
        (rec.responseText && (/\.pdf($|\?)/i.test(rec.url) || /\/api\/pdf\//i.test(rec.url)) ? '  <button data-recact="setPdf">设为PDF接口</button>' : '') +
        '  <button data-recact="copyUrl">复制URL</button>' +
        '</div>';
      detail.querySelector('pre').textContent = pretty;
      head.addEventListener('click', function () { div.classList.toggle('open'); });
      detail.addEventListener('click', function (ev) {
        const b = ev.target.closest('[data-recact]');
        if (!b) return;
        const act = b.dataset.recact;
        if (act === 'copyUrl') {
          copyText(rec.url);
          toast('已复制 URL');
        } else if (act === 'setPaper') {
          const json = tryParse(rec.responseText);
          const ap = json && (findArrayPath(json, ['paperId', 'name'], 8) || findArrayPath(json, ['id', 'name'], 8));
          const course = courseOfUrl(rec.url);
          if (course) ensureCourse(course);
          const tpl = templatePath(rec.url);
          if (tpl && !state.cfg.papers.paths.some(function (x) { return x === tpl; })) {
            state.cfg.papers.paths.unshift(tpl);
          }
          state.cfg.papers.method = rec.method;
          if (ap) state.cfg.papers.itemsPath = ap.path;
          saveCfg(); renderConfigTab();
          toast('已设为试卷接口');
        } else if (act === 'setCat') {
          const json = tryParse(rec.responseText);
          const ac = json && findArrayPath(json, ['labelId', 'name'], 8);
          const course = courseOfUrl(rec.url);
          if (course) ensureCourse(course);
          state.cfg.subLabels.method = rec.method;
          state.cfg.subLabels.path = templatePath(rec.url);
          if (ac) state.cfg.subLabels.itemsPath = ac.path;
          saveCfg(); renderConfigTab();
          toast('已设为分类接口');
        } else if (act === 'setPdf') {
          const json = tryParse(rec.responseText);
          const up = json && findUrlPath(json, 8);
          const path = templatePdfPath(rec.url);
          const course = courseOfUrl(rec.url);
          if (course) ensureCourse(course);
          const c = { method: rec.method, path: path, urlPath: up ? up.path : '' };
          if (!state.cfg.pdf.candidates.some(function (x) { return x.method === c.method && x.path === c.path; })) {
            state.cfg.pdf.candidates.unshift(c);
          }
          saveCfg(); renderConfigTab();
          toast('已加入 PDF 候选接口');
        }
      });
      div.appendChild(head);
      div.appendChild(detail);
      box.appendChild(div);
    });
    if (state.records.length > limit) {
      const more = document.createElement('div');
      more.className = 'hint';
      more.textContent = '… 仅显示最近 ' + limit + ' 条（共 ' + state.records.length + ' 条）';
      box.appendChild(more);
    }
  }

  function fmtSize(n) {
    if (n < 1024) return n + 'B';
    if (n < 1048576) return (n / 1024).toFixed(1) + 'KB';
    return (n / 1048576).toFixed(1) + 'MB';
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function copyText(t) {
    try {
      const ta = document.createElement('textarea');
      ta.value = t;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    } catch (e) { /* 忽略 */ }
  }

  // ---- 日志标签 ----

  function renderLog() {
    const sh = state.sh;
    const box = sh.getElementById('logbox');
    if (!box) return;
    box.textContent = state.logs.join('\n');
  }

  // ================================================================
  // 启动
  // ================================================================

  state.cfg = loadCfg();
  installCapture();
  buildPanel();
  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('切换粉笔PDF下载面板', togglePanel);
  }
})();
