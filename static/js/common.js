/* 试卷批改系统 —— 公共脚本
   JS 基线 ES2017：不用可选链 ?. 、空值合并 ?? 、对象展开 {...x} 、Array.flat。
   老师那台机器（银河麒麟 / 统信 UOS）自带浏览器可能较老。 */
'use strict';

function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) {
  return Array.prototype.slice.call((root || document).querySelectorAll(sel));
}

function param(name, fallback) {
  var v = new URLSearchParams(location.search).get(name);
  return (v === null || v === '') ? (fallback === undefined ? '' : fallback) : v;
}

function escapeHtml(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 建 DOM：h('div', {class:'x', onclick:fn}, '文字', 子节点) */
function h(tag, attrs, children) {
  var node = document.createElement(tag);
  attrs = attrs || {};
  Object.keys(attrs).forEach(function (key) {
    var val = attrs[key];
    if (val === null || val === undefined || val === false) return;
    if (key.slice(0, 2) === 'on' && typeof val === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), val);
    } else if (key === 'class') {
      node.className = val;
    } else if (key === 'html') {
      node.innerHTML = val;
    } else if (key === 'text') {
      node.textContent = val;
    } else if (key === 'value') {
      node.value = val;
    } else {
      node.setAttribute(key, val === true ? '' : val);
    }
  });
  var rest = Array.prototype.slice.call(arguments, 2);
  rest.forEach(function add(child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(add); return; }
    node.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  });
  return node;
}

/* ---------- 提示条 ---------- */
function toast(message, kind, ms) {
  var box = $('#toast');
  if (!box) {
    box = h('div', { id: 'toast' });
    document.body.appendChild(box);
  }
  var item = h('div', { class: 'toast-item ' + (kind || ''), text: message });
  box.appendChild(item);
  setTimeout(function () {
    if (item.parentNode) item.parentNode.removeChild(item);
  }, ms || (kind === 'err' ? 7000 : 2600));
}

/* ---------- 接口调用 ---------- */
function handle(resp) {
  return resp.text().then(function (text) {
    var data = null;
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = null; }
    if (!resp.ok) {
      var msg = (data && data.error) ? data.error : ('请求失败（HTTP ' + resp.status + '）');
      var err = new Error(msg);
      err.status = resp.status;
      throw err;
    }
    return data === null ? {} : data;
  });
}

var API = {
  get: function (path) {
    return fetch(path, { headers: { 'Accept': 'application/json' } }).then(handle);
  },
  send: function (method, path, body) {
    return fetch(path, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(handle);
  },
  post: function (path, body) { return API.send('POST', path, body); },
  put: function (path, body) { return API.send('PUT', path, body); },
  del: function (path) { return API.send('DELETE', path); },
  upload: function (path, formData) {
    return fetch(path, { method: 'POST', body: formData }).then(handle);
  }
};

/** 出错时统一弹提示；调用方拿到 null 表示已经处理过错误了 */
function guard(promise, what) {
  return promise.catch(function (err) {
    toast((what ? what + '：' : '') + err.message, 'err');
    return null;
  });
}

/* ---------- 顶栏 ---------- */
var NAV = [
  { key: 'exam', label: '考试设置 / 题卡', href: 'exam.html' },
  { key: 'students', label: '学生与答卷', href: 'students.html' },
  { key: 'grade', label: '批改', href: 'grade.html' },
  { key: 'report', label: '成绩分析', href: 'report.html' }
];

function mountHeader(opts) {
  opts = opts || {};
  var old = $('.topbar');           // 页面可能反复重载数据，别叠出好几条顶栏
  if (old) old.parentNode.removeChild(old);
  var bar = h('div', { class: 'topbar' },
    h('a', { class: 'logo', href: 'index.html' }, '试卷批改'));
  if (opts.exam) {
    bar.appendChild(h('span', { class: 'exam-name', title: opts.exam.name },
      opts.exam.name + (opts.exam.klass ? '（' + opts.exam.klass + '）' : '')));
    var tabs = h('div', { class: 'tabs' });
    NAV.forEach(function (item) {
      tabs.appendChild(h('a', {
        class: opts.tab === item.key ? 'active' : '',
        href: item.href + '?exam=' + opts.exam.id
      }, item.label));
    });
    bar.appendChild(tabs);
  }
  bar.appendChild(h('span', { class: 'spacer' }));
  bar.appendChild(h('a', { href: 'settings.html', class: 'tabs' }, 'AI 设置'));
  var badge = h('span', { class: 'version', id: 'version-badge' }, '…');
  bar.appendChild(badge);
  document.body.insertBefore(bar, document.body.firstChild);
  API.get('/api/version').then(function (v) {
    badge.textContent = 'v' + v.version;
    badge.title = '版本号。更新代码后这里会变。';
  }).catch(function () { badge.textContent = 'v?'; });
  return bar;
}

/* ---------- 弹层 ---------- */
function modal(title, bodyNode, opts) {
  opts = opts || {};
  return new Promise(function (resolve) {
    var mask = h('div', { class: 'modal-mask' });
    function close(value) {
      if (mask.parentNode) mask.parentNode.removeChild(mask);
      document.removeEventListener('keydown', onKey);
      resolve(value);
    }
    function onKey(e) { if (e.key === 'Escape') close(null); }
    var okBtn = h('button', {
      class: 'btn' + (opts.danger ? ' danger' : ''),
      onclick: function () { close(opts.getValue ? opts.getValue() : true); }
    }, opts.okText || '确定');
    var box = h('div', { class: 'modal' },
      h('h3', {}, title),
      bodyNode,
      h('div', { class: 'actions' },
        h('button', { class: 'btn ghost', onclick: function () { close(null); } },
          opts.cancelText || '取消'),
        okBtn));
    mask.appendChild(box);
    mask.addEventListener('click', function (e) { if (e.target === mask) close(null); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(mask);
    var first = box.querySelector('input, textarea, select');
    if (first) first.focus(); else okBtn.focus();
  });
}

function confirmBox(title, message, opts) {
  opts = opts || {};
  var body = h('div', {}, h('p', { style: 'white-space:pre-wrap;margin:0' }, message));
  return modal(title, body, {
    okText: opts.okText || '确定', danger: opts.danger
  }).then(function (v) { return v === true; });
}

/* ---------- 格式化 ---------- */
function fmt(n, dash) {
  if (n === null || n === undefined || n === '') return dash === undefined ? '—' : dash;
  var v = Number(n);
  if (isNaN(v)) return String(n);
  return String(Math.round(v * 100) / 100);
}

var STATUS_LABEL = { todo: '未批', doing: '批改中', done: '已批完' };

function statusTag(status) {
  return h('span', { class: 'tag ' + status }, STATUS_LABEL[status] || status);
}

/** 把图片缩到指定宽度并转成 JPEG dataURL —— 发给 AI 之前必须压，原图太大会超时 */
function shrinkImage(imgEl, maxWidth, quality) {
  maxWidth = maxWidth || 1500;
  var w = imgEl.naturalWidth || imgEl.width;
  var hgt = imgEl.naturalHeight || imgEl.height;
  if (!w || !hgt) return null;
  var scale = Math.min(1, maxWidth / w);
  var cw = Math.max(1, Math.round(w * scale));
  var ch = Math.max(1, Math.round(hgt * scale));
  var cvs = document.createElement('canvas');
  cvs.width = cw;
  cvs.height = ch;
  var ctx = cvs.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, cw, ch);
  ctx.drawImage(imgEl, 0, 0, cw, ch);
  return cvs.toDataURL('image/jpeg', quality || 0.82);
}

function loadImage(src) {
  return new Promise(function (resolve, reject) {
    var img = new Image();
    img.onload = function () { resolve(img); };
    img.onerror = function () { reject(new Error('图片加载失败：' + src)); };
    img.src = src;
  });
}

function fileUrl(rel) { return '/files/' + String(rel).split('/').map(encodeURIComponent).join('/'); }
