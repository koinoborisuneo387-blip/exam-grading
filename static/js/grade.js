/* 批改台：左边卷面（Canvas 红笔批注），右边逐题打分 + AI 建议
   注意：AI 给的分只写进「建议」栏，老师点「采纳」才会记进成绩。 */
'use strict';

var EXAM_ID = param('exam');
var students = [];
var index = 0;
var data = null;          // 当前学生的完整批改数据
var zoom = 1;
var tool = 'pen';
var MAX_BASE = 1800;      // 卷面画布最长边，太大低配电脑吃不消
var pageViews = [];       // 每页一个 {page, base, ink, strokes, ...}
var activeView = null;
var saveTimers = {};

if (!EXAM_ID) location.href = 'index.html';

/* ================= 笔迹 ================= */
/* 坐标一律存成 0~1 的相对值，这样缩放、换机器都不会错位。
   线宽以「1000 像素宽的画布」为基准，画的时候按实际宽度换算。 */

var COLOR = '#e11d48';

function newStroke(kind, extra) {
  var s = { t: kind, c: COLOR, w: kind === 'circle' ? 3 : 4, p: [] };
  if (extra) Object.keys(extra).forEach(function (k) { s[k] = extra[k]; });
  return s;
}

function drawStroke(ctx, s, W, H) {
  var scale = W / 1000;
  ctx.save();
  ctx.strokeStyle = s.c || COLOR;
  ctx.fillStyle = s.c || COLOR;
  ctx.lineWidth = Math.max(1.2, (s.w || 4) * scale);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  var pts = s.p || [];
  if (s.t === 'pen' && pts.length) {
    ctx.beginPath();
    ctx.moveTo(pts[0][0] * W, pts[0][1] * H);
    for (var i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0] * W, pts[i][1] * H);
    ctx.stroke();
  } else if (s.t === 'check' && pts.length) {
    var x = pts[0][0] * W, y = pts[0][1] * H, r = 17 * scale;
    ctx.lineWidth = Math.max(2, 4.5 * scale);
    ctx.beginPath();
    ctx.moveTo(x - r, y);
    ctx.lineTo(x - r * 0.25, y + r * 0.75);
    ctx.lineTo(x + r * 1.05, y - r * 0.9);
    ctx.stroke();
  } else if (s.t === 'cross' && pts.length) {
    var cx = pts[0][0] * W, cy = pts[0][1] * H, d = 14 * scale;
    ctx.lineWidth = Math.max(2, 4.5 * scale);
    ctx.beginPath();
    ctx.moveTo(cx - d, cy - d); ctx.lineTo(cx + d, cy + d);
    ctx.moveTo(cx + d, cy - d); ctx.lineTo(cx - d, cy + d);
    ctx.stroke();
  } else if (s.t === 'circle' && pts.length >= 2) {
    var x0 = pts[0][0] * W, y0 = pts[0][1] * H;
    var x1 = pts[1][0] * W, y1 = pts[1][1] * H;
    ctx.beginPath();
    ctx.ellipse((x0 + x1) / 2, (y0 + y1) / 2,
      Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2);
    ctx.stroke();
  } else if (s.t === 'text' && pts.length) {
    var size = Math.max(12, 22 * scale);
    ctx.font = '600 ' + size + 'px "Microsoft YaHei","Noto Sans CJK SC",sans-serif';
    ctx.textBaseline = 'top';
    String(s.s || '').split('\n').forEach(function (line, i) {
      ctx.fillText(line, pts[0][0] * W, pts[0][1] * H + i * size * 1.25);
    });
  }
  ctx.restore();
}

function validStroke(s) {
  if (!s || !Array.isArray(s.p) || !s.p.length) return false;
  return s.p.every(function (pt) {
    return Array.isArray(pt) && typeof pt[0] === 'number' && typeof pt[1] === 'number'
      && isFinite(pt[0]) && isFinite(pt[1]);
  });
}

function redraw(view) {
  var ctx = view.ink.getContext('2d');
  ctx.clearRect(0, 0, view.ink.width, view.ink.height);
  view.strokes.forEach(function (s) {
    if (validStroke(s)) drawStroke(ctx, s, view.ink.width, view.ink.height);
  });
}

function strokeHit(s, x, y, tolerance) {
  var pts = s.p || [];
  for (var i = 0; i < pts.length; i++) {
    if (Math.abs(pts[i][0] - x) < tolerance && Math.abs(pts[i][1] - y) < tolerance) return true;
  }
  if (s.t === 'circle' && pts.length >= 2) {
    var minX = Math.min(pts[0][0], pts[1][0]) - tolerance;
    var maxX = Math.max(pts[0][0], pts[1][0]) + tolerance;
    var minY = Math.min(pts[0][1], pts[1][1]) - tolerance;
    var maxY = Math.max(pts[0][1], pts[1][1]) + tolerance;
    return x >= minX && x <= maxX && y >= minY && y <= maxY;
  }
  return false;
}

function scheduleSave(view) {
  clearTimeout(saveTimers[view.page.id]);
  saveTimers[view.page.id] = setTimeout(function () {
    API.post('/api/pages/' + view.page.id + '/annotation', { data: { strokes: view.strokes } })
      .catch(function (err) { toast('批注没存上：' + err.message, 'err'); });
  }, 700);
}

/* ================= 页面渲染 ================= */

function rotatePoints(strokes, W, H, deg) {
  // 只支持 90 度的倍数。旋转卷面时把已有笔迹一起转过去，免得错位。
  strokes.forEach(function (s) {
    s.p = (s.p || []).map(function (pt) {
      var x = pt[0], y = pt[1];
      if (deg === 90) return [1 - y, x];
      if (deg === 180) return [1 - x, 1 - y];
      if (deg === 270) return [y, 1 - x];
      return [x, y];
    });
  });
  return [H, W];
}

function buildPageView(page, img) {
  var rot = ((Number(page.rotate) || 0) % 360 + 360) % 360;
  var natW = img.naturalWidth, natH = img.naturalHeight;
  var swap = (rot === 90 || rot === 270);
  var dispW = swap ? natH : natW;
  var dispH = swap ? natW : natH;
  var scale = Math.min(1, MAX_BASE / Math.max(dispW, dispH));
  var W = Math.max(1, Math.round(dispW * scale));
  var H = Math.max(1, Math.round(dispH * scale));

  var base = h('canvas', { width: W, height: H });
  var bctx = base.getContext('2d');
  bctx.fillStyle = '#fff';
  bctx.fillRect(0, 0, W, H);
  bctx.save();
  if (rot === 90) { bctx.translate(W, 0); bctx.rotate(Math.PI / 2); }
  else if (rot === 180) { bctx.translate(W, H); bctx.rotate(Math.PI); }
  else if (rot === 270) { bctx.translate(0, H); bctx.rotate(-Math.PI / 2); }
  bctx.drawImage(img, 0, 0, swap ? H : W, swap ? W : H);
  bctx.restore();

  var ink = h('canvas', { width: W, height: H });
  var strokes = [];
  if (page.data_json) {
    try {
      var parsed = JSON.parse(page.data_json);
      // 顺手把坏笔迹（坐标是 NaN/null 的）滤掉，历史数据里可能有
      if (parsed && Array.isArray(parsed.strokes)) strokes = parsed.strokes.filter(validStroke);
    } catch (e) { strokes = []; }
  }

  var stage = h('div', { class: 'page-stage' }, base, ink);
  var view = { page: page, img: img, base: base, ink: ink, stage: stage,
    strokes: strokes, W: W, H: H, rot: rot };
  bindDrawing(view);
  redraw(view);
  return view;
}

function bindDrawing(view) {
  var ink = view.ink;
  var drawing = null;

  function pos(evt) {
    var rect = ink.getBoundingClientRect();
    // 画布还没排好版时宽高可能是 0，除下去会得到 NaN，存进库就是一笔坏笔迹。
    // 宁可丢掉这一下，也不能存 NaN。
    if (!rect.width || !rect.height) return null;
    return [
      Math.min(1, Math.max(0, (evt.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (evt.clientY - rect.top) / rect.height))
    ];
  }

  ink.addEventListener('pointerdown', function (evt) {
    if (evt.button !== 0) return;
    activeView = view;
    var p = pos(evt);
    if (!p) return;
    if (tool === 'erase') {
      for (var i = view.strokes.length - 1; i >= 0; i--) {
        if (strokeHit(view.strokes[i], p[0], p[1], 0.018)) {
          view.strokes.splice(i, 1);
          redraw(view);
          scheduleSave(view);
          return;
        }
      }
      return;
    }
    if (tool === 'check' || tool === 'cross') {
      view.strokes.push(newStroke(tool, { p: [p] }));
      redraw(view);
      scheduleSave(view);
      return;
    }
    if (tool === 'text') {
      var input = h('textarea', { rows: '2', placeholder: '写点什么，例如：步骤不完整' });
      modal('在卷面上写字', input, { okText: '写上去',
        getValue: function () { return input.value; } }).then(function (text) {
          if (!text || !String(text).trim()) return;
          view.strokes.push(newStroke('text', { p: [p], s: String(text).trim() }));
          redraw(view);
          scheduleSave(view);
        });
      return;
    }
    drawing = newStroke(tool, { p: [p] });
    view.strokes.push(drawing);
    ink.setPointerCapture(evt.pointerId);
    evt.preventDefault();
  });

  ink.addEventListener('pointermove', function (evt) {
    if (!drawing) return;
    var p = pos(evt);
    if (!p) return;
    if (drawing.t === 'circle') drawing.p[1] = p;
    else drawing.p.push(p);
    redraw(view);
  });

  function finish() {
    if (!drawing) return;
    if (drawing.t === 'pen' && drawing.p.length < 2) view.strokes.pop();
    if (drawing.t === 'circle' && drawing.p.length < 2) view.strokes.pop();
    drawing = null;
    redraw(view);
    scheduleSave(view);
  }
  ink.addEventListener('pointerup', finish);
  ink.addEventListener('pointercancel', finish);
  ink.addEventListener('pointerleave', function () { if (drawing) finish(); });
}

function applyZoom() {
  pageViews.forEach(function (v) {
    // 宽高都写成具体像素。用 100% / auto 的话，绝对定位的墨迹层算不出高度（会变成 0），
    // 画上去的红勾就看不见了 —— 踩过这个坑。
    var w = Math.max(1, Math.round(v.W * zoom));
    var hgt = Math.max(1, Math.round(v.H * zoom));
    [v.stage, v.base, v.ink].forEach(function (el) {
      el.style.width = w + 'px';
      el.style.height = hgt + 'px';
    });
  });
  $('#zoom-label').textContent = Math.round(zoom * 100) + '%';
}

function fitWidth() {
  if (!pageViews.length) return;
  var avail = $('#paper-scroll').clientWidth - 40;
  zoom = Math.max(0.15, Math.min(3, avail / pageViews[0].W));
  applyZoom();
}

function renderPages() {
  var box = $('#pages');
  box.innerHTML = '';
  pageViews = [];
  activeView = null;
  var pages = data.pages || [];
  $('#page-info').textContent = pages.length ? (pages.length + ' 页') : '';
  if (!pages.length) {
    box.appendChild(h('div', { style: 'color:#cbd2d9;padding:60px 20px' },
      '这个学生还没有导入答卷。到「学生与答卷」页给他传照片或扫描件。'));
    return Promise.resolve();
  }
  return Promise.all(pages.map(function (p) {
    return loadImage(fileUrl(p.image_path)).then(function (img) {
      return { page: p, img: img };
    }).catch(function () { return null; });
  })).then(function (loaded) {
    loaded.forEach(function (item, i) {
      if (!item) {
        box.appendChild(h('div', { class: 'page-label' },
          '第 ' + (i + 1) + ' 页图片读不出来'));
        return;
      }
      var view = buildPageView(item.page, item.img);
      pageViews.push(view);
      box.appendChild(view.stage);
      box.appendChild(h('div', { class: 'page-label' }, '第 ' + item.page.page_no + ' 页'));
    });
    activeView = pageViews[0] || null;
    fitWidth();
  });
}

/* ================= 右侧题卡 ================= */

function questionCard(q) {
  var card = h('div', { class: 'q-card' + (q.score !== null && q.score !== undefined ? ' scored' : '') });
  var scoreInput = h('input', {
    type: 'number', class: 'score-input', step: '0.5', min: '0',
    max: String(q.max_score), value: (q.score === null || q.score === undefined) ? '' : q.score,
    'data-qid': q.id
  });

  function setScore(value) {
    scoreInput.value = value;
    saveScore(q, value);
  }

  scoreInput.addEventListener('change', function () { saveScore(q, this.value); });
  scoreInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveScore(q, this.value);
      focusNext(q.id);
    } else if (e.key === ' ') {
      e.preventDefault();
      setScore(q.max_score);
      focusNext(q.id);
    }
  });
  scoreInput.addEventListener('focus', function () {
    $$('.q-card').forEach(function (c) { c.classList.remove('active'); });
    card.classList.add('active');
  });

  var quick = h('div', { class: 'quick' },
    h('button', { class: 'btn small ghost', onclick: function () { setScore(q.max_score); } }, '满分'),
    h('button', { class: 'btn small ghost', onclick: function () { setScore(0); } }, '0 分'),
    h('button', { class: 'btn small ghost',
      onclick: function () { setScore(''); } }, '清空'));

  var refBits = [];
  if (q.stem) refBits.push(h('div', {}, h('b', {}, '题干：'), q.stem));
  if (q.answer_key) refBits.push(h('div', { style: 'margin-top:4px' },
    h('b', {}, '参考答案：'), q.answer_key));
  if (q.rubric) refBits.push(h('div', { style: 'margin-top:4px' },
    h('b', {}, '评分要点：'), q.rubric));

  var answerBox = h('textarea', { rows: '3', value: q.student_answer || '',
    placeholder: '学生作答文字（用能看图的模型时可以不填）' });
  answerBox.addEventListener('change', function () {
    API.post('/api/scores', { paper_id: data.paper.id, question_id: q.id,
      student_answer: this.value }).catch(function (err) {
        toast('保存作答文本失败：' + err.message, 'err');
      });
  });

  var aiBox = h('div', {});
  function renderAI() {
    aiBox.innerHTML = '';
    if (q.ai_suggested_score === null || q.ai_suggested_score === undefined) {
      if (!q.ai_comment) return;
    }
    var box = h('div', { class: 'ai-box' },
      h('div', {},
        h('span', { class: 'ai-score' },
          'AI 建议：' + (q.ai_suggested_score === null || q.ai_suggested_score === undefined
            ? '看不清，没给分' : fmt(q.ai_suggested_score) + ' / ' + fmt(q.max_score))),
        q.ai_accepted ? h('span', { class: 'tag done', style: 'margin-left:6px' }, '已采纳') : null),
      q.ai_comment ? h('div', { style: 'white-space:pre-wrap;margin-top:4px' }, q.ai_comment) : null,
      h('div', { style: 'margin-top:6px;display:flex;gap:6px' },
        (q.ai_suggested_score === null || q.ai_suggested_score === undefined) ? null :
          h('button', { class: 'btn small', onclick: function () {
            scoreInput.value = q.ai_suggested_score;
            saveScore(q, q.ai_suggested_score, true);
          } }, '采纳这个分'),
        h('button', { class: 'btn small ghost', onclick: function () { askAI(q, card); } },
          '重新问 AI')));
    aiBox.appendChild(box);
  }
  card._renderAI = renderAI;
  card._question = q;

  card.appendChild(h('div', { class: 'q-top' },
    h('span', { class: 'q-no' }, q.no_label || ('第 ' + q.id + ' 题')),
    h('span', { class: 'q-type' }, q.qtype_label),
    h('span', { class: 'q-full' }, '满分 ' + fmt(q.max_score))));
  card.appendChild(h('div', { class: 'q-score-row' }, scoreInput,
    h('span', { style: 'color:#6b7785' }, '/ ' + fmt(q.max_score)), quick));
  card.appendChild(h('div', { style: 'margin-top:6px' },
    h('button', { class: 'btn small', id: 'ai-btn-' + q.id,
      onclick: function () { askAI(q, card); } }, '🤖 让 AI 批这道题')));
  card.appendChild(aiBox);
  if (refBits.length) {
    card.appendChild(h('details', { class: 'q-extra' },
      h('summary', {}, '参考答案 / 评分要点'),
      h('div', { style: 'font-size:13px;white-space:pre-wrap;margin-top:4px' }, refBits),
      h('div', { style: 'font-size:12px;color:#6b7785;margin-top:6px' },
        '判分标准：意思相近就给分，不要求和参考答案一模一样。')));
  }
  card.appendChild(h('details', { class: 'q-extra' },
    h('summary', {}, '学生作答文字（给 AI 用，可不填）'), answerBox));
  card._answerBox = answerBox;
  renderAI();
  return card;
}

function focusNext(qid) {
  var inputs = $$('.score-input');
  for (var i = 0; i < inputs.length; i++) {
    if (String(inputs[i].getAttribute('data-qid')) === String(qid)) {
      if (inputs[i + 1]) { inputs[i + 1].focus(); inputs[i + 1].select(); }
      else { toast('这是最后一题了', 'ok', 1200); }
      return;
    }
  }
}

function saveScore(q, value, fromAI) {
  var body = { paper_id: data.paper.id, question_id: q.id, score: value === '' ? null : Number(value) };
  if (fromAI) body.ai_accepted = 1;
  API.post('/api/scores', body).then(function (r) {
    q.score = r.score.score;
    q.ai_accepted = r.score.ai_accepted;
    data.paper.total_score = r.recalc.total_score;
    data.paper.status = r.recalc.status;
    updateTotals(r.recalc);
    $$('.q-card').forEach(function (c) {
      if (c._question && c._question.id === q.id) {
        c.classList.toggle('scored', q.score !== null && q.score !== undefined);
        if (fromAI && c._renderAI) c._renderAI();
      }
    });
  }).catch(function (err) { toast('保存分数失败：' + err.message, 'err'); });
}

function updateTotals(recalc) {
  $('#total').textContent = fmt(recalc.total_score, '0');
  $('#progress').textContent = '已批 ' + recalc.graded_count + ' / ' + recalc.question_count + ' 题';
}

function renderQuestions() {
  var box = $('#qlist');
  box.innerHTML = '';
  if (!data.questions.length) {
    box.appendChild(h('p', { class: 'empty' },
      '这场考试还没登记题目。先到「考试设置 / 题卡」页把要批的主观题加上。'));
    return;
  }
  data.questions.forEach(function (q) { box.appendChild(questionCard(q)); });
}

/* ================= AI ================= */

function collectImages(maxWidth) {
  return pageViews.map(function (v) {
    return v.base.toDataURL('image/jpeg', 0.8);
  });
}

function collectKeyImages() {
  var pages = data.answer_pages || [];
  if (!pages.length) return Promise.resolve([]);
  return Promise.all(pages.map(function (p) {
    return loadImage(fileUrl(p.image_path)).then(function (img) {
      return shrinkImage(img, 1400, 0.8);
    }).catch(function () { return null; });
  })).then(function (list) {
    return list.filter(function (x) { return !!x; });
  });
}

function aiPreflight() {
  if (!data.ai_ready) {
    toast('AI 还没配好。点右上角「AI 设置」，填上智谱 GLM-4V 的 API Key 并打开开关。', 'warn', 8000);
    return false;
  }
  return true;
}

function askAI(q, card) {
  if (!aiPreflight()) return;
  var btn = $('#ai-btn-' + q.id);
  var answer = card._answerBox ? card._answerBox.value : '';
  if (!data.ai_vision && !answer.trim()) {
    toast('当前模型看不了卷子图。请先在这道题的「学生作答文字」里粘上学生写的内容。\n' +
      '想让 AI 直接看卷子，到「AI 设置」换成 GLM-4V 这类能看图的模型。', 'warn', 9000);
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'AI 批改中…'; }
  collectKeyImages().then(function (keyImgs) {
    return API.post('/api/ai/suggest', {
      paper_id: data.paper.id,
      question_id: q.id,
      student_answer: answer,
      images: data.ai_vision ? collectImages() : [],
      key_images: data.ai_vision ? keyImgs : []
    });
  }).then(function (r) {
    q.ai_suggested_score = r.score;
    q.ai_comment = r.comment + (r.reasons && r.reasons.length
      ? '\n' + r.reasons.map(function (x) { return '· ' + x; }).join('\n') : '');
    q.ai_accepted = 0;
    if (card._renderAI) card._renderAI();
    toast('AI 给了建议分，确认后点「采纳」', 'ok');
  }).catch(function (err) {
    toast(err.message, 'err', 10000);
  }).then(function () {
    if (btn) { btn.disabled = false; btn.textContent = '🤖 让 AI 批这道题'; }
  });
}

$('#btn-ai-whole').addEventListener('click', function () {
  if (!aiPreflight()) return;
  if (!data.ai_vision) {
    toast('整卷预批需要能看图的模型。到「AI 设置」换成智谱 GLM-4V 或通义千问 qwen-vl-max。',
      'warn', 9000);
    return;
  }
  if (!pageViews.length) { toast('这个学生还没有答卷图片。', 'warn'); return; }
  var btn = this;
  btn.disabled = true;
  btn.textContent = 'AI 批改中，请等一会…';
  collectKeyImages().then(function (keyImgs) {
    return API.post('/api/ai/grade_paper', {
      paper_id: data.paper.id,
      images: collectImages(),
      key_images: keyImgs
    });
  }).then(function (r) {
    toast('AI 批了 ' + r.items.length + ' 道题，逐题确认后点「采纳」', 'ok', 5000);
    return reloadCurrent();
  }).catch(function (err) {
    toast(err.message, 'err', 12000);
  }).then(function () {
    btn.disabled = false;
    btn.textContent = '🤖 AI 预批整卷';
  });
});

/* ================= 工具条 ================= */

$$('.paper-toolbar button[data-tool]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    tool = btn.getAttribute('data-tool');
    $$('.paper-toolbar button[data-tool]').forEach(function (b) {
      b.classList.toggle('on', b === btn);
    });
  });
});

$('#btn-undo').addEventListener('click', function () {
  var v = activeView || pageViews[0];
  if (!v || !v.strokes.length) return;
  v.strokes.pop();
  redraw(v);
  scheduleSave(v);
});

$('#btn-clear-ink').addEventListener('click', function () {
  var v = activeView || pageViews[0];
  if (!v || !v.strokes.length) return;
  confirmBox('清空本页批注', '这一页画的红勾红叉和文字都会没掉，确定吗？',
    { okText: '清空', danger: true }).then(function (ok) {
      if (!ok) return;
      v.strokes = [];
      redraw(v);
      scheduleSave(v);
    });
});

$('#btn-zoom-in').addEventListener('click', function () {
  zoom = Math.min(3, zoom * 1.2); applyZoom();
});
$('#btn-zoom-out').addEventListener('click', function () {
  zoom = Math.max(0.15, zoom / 1.2); applyZoom();
});
$('#btn-zoom-fit').addEventListener('click', fitWidth);

$('#btn-rotate').addEventListener('click', function () {
  var v = activeView || pageViews[0];
  if (!v) return;
  guard(API.post('/api/pages/' + v.page.id + '/rotate', { delta: 90 }), '旋转失败')
    .then(function (r) {
      if (!r) return;
      // 笔迹跟着一起转，不然位置就错了
      rotatePoints(v.strokes, v.W, v.H, 90);
      API.post('/api/pages/' + v.page.id + '/annotation',
        { data: { strokes: v.strokes } }).catch(function () {});
      v.page.rotate = r.rotate;
      v.page.data_json = JSON.stringify({ strokes: v.strokes });
      return reloadCurrent();
    });
});

$('#btn-key').addEventListener('click', function () {
  var pages = data.answer_pages || [];
  if (!pages.length) {
    toast('这场考试还没上传标准答案卷。到「考试设置 / 题卡」页底部可以传。', 'warn', 7000);
    return;
  }
  var body = h('div', {});
  pages.forEach(function (p) {
    body.appendChild(h('img', {
      src: fileUrl(p.image_path),
      style: 'width:100%;margin-bottom:10px;border:1px solid #dde3ea' +
        (p.rotate ? ';transform:rotate(' + p.rotate + 'deg)' : '')
    }));
  });
  modal('标准答案', body, { okText: '关闭', cancelText: '关闭' });
});

$('#btn-save-marked').addEventListener('click', function () {
  if (!pageViews.length) { toast('没有可保存的页面。', 'warn'); return; }
  var btn = this;
  btn.disabled = true;
  btn.textContent = '保存中…';
  var jobs = pageViews.map(function (v) {
    var out = document.createElement('canvas');
    out.width = v.W;
    out.height = v.H;
    var ctx = out.getContext('2d');
    ctx.drawImage(v.base, 0, 0);
    ctx.drawImage(v.ink, 0, 0);
    return API.post('/api/pages/' + v.page.id + '/annotated',
      { image: out.toDataURL('image/jpeg', 0.9) });
  });
  Promise.all(jobs).then(function () {
    toast('批注图已保存，可以在「成绩分析」页打包导出给学生', 'ok', 5000);
  }).catch(function (err) {
    toast('保存失败：' + err.message, 'err');
  }).then(function () {
    btn.disabled = false;
    btn.textContent = '存批注图';
  });
});

/* ================= 学生导航 ================= */

function updateHead() {
  var exam = data.exam;
  $('#stu-name').textContent = data.student.name;
  $('#stu-meta').textContent = '　' + (data.student.student_no ? data.student.student_no + '　' : '') +
    '第 ' + (index + 1) + ' / ' + students.length + ' 人';
  $('#full-label').textContent = '/ ' + fmt(exam.full_score);
  $('#paper-comment').value = data.paper.comment || '';

  var objFull = Number(exam.objective_full) || 0;
  $('#obj-row').hidden = objFull <= 0;
  if (objFull > 0) {
    $('#obj-score').value = data.paper.objective_score;
    $('#obj-score').max = String(objFull);
    $('#obj-full').textContent = '/ ' + fmt(objFull);
  }
  var note = '';
  if (!data.ai_ready) note = 'AI 未配置：点右上角「AI 设置」填 GLM-4V 的 Key。';
  else if (!data.ai_vision) note = '当前模型看不了卷子图，AI 只能批你粘贴的文字。';
  else if (!(data.answer_pages || []).length) note = '还没传标准答案卷，AI 只按题卡里的参考答案判。';
  $('#ai-note').textContent = note;

  $('#btn-prev').disabled = index <= 0;
  $('#btn-next').disabled = index >= students.length - 1;
  document.title = data.student.name + ' — 批改台';
}

$('#obj-score').addEventListener('change', function () {
  API.post('/api/papers/' + data.paper.id + '/meta',
    { objective_score: this.value === '' ? 0 : Number(this.value) })
    .then(function (r) {
      data.paper = r.paper;
      updateTotals(r.recalc);
    }).catch(function (err) { toast('保存失败：' + err.message, 'err'); });
});

$('#paper-comment').addEventListener('change', function () {
  API.post('/api/papers/' + data.paper.id + '/meta', { comment: this.value })
    .catch(function (err) { toast('保存评语失败：' + err.message, 'err'); });
});

function loadStudent(i) {
  if (i < 0 || i >= students.length) return Promise.resolve();
  index = i;
  var sid = students[i].id;
  history.replaceState(null, '', 'grade.html?exam=' + EXAM_ID + '&student=' + sid);
  return API.get('/api/students/' + sid + '/paper').then(function (d) {
    data = d;
    mountHeader({ tab: 'grade', exam: d.exam });
    updateHead();
    renderQuestions();
    updateTotals({
      total_score: d.paper.total_score,
      graded_count: d.questions.filter(function (q) {
        return q.score !== null && q.score !== undefined;
      }).length,
      question_count: d.questions.length
    });
    return renderPages();
  }).catch(function (err) {
    toast('打开这个学生失败：' + err.message, 'err');
  });
}

function reloadCurrent() { return loadStudent(index); }

$('#btn-prev').addEventListener('click', function () { loadStudent(index - 1); });
$('#btn-next').addEventListener('click', function () { loadStudent(index + 1); });

document.addEventListener('keydown', function (e) {
  var tag = (e.target.tagName || '').toLowerCase();
  var typing = tag === 'input' || tag === 'textarea' || tag === 'select';
  if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) {
    e.preventDefault();
    $('#btn-undo').click();
    return;
  }
  if (typing) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); loadStudent(index - 1); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); loadStudent(index + 1); }
  else if (e.key >= '1' && e.key <= '6') {
    var btns = $$('.paper-toolbar button[data-tool]');
    var target = btns[Number(e.key) - 1];
    if (target) target.click();
  }
});

window.addEventListener('resize', function () {
  clearTimeout(window._fitTimer);
  window._fitTimer = setTimeout(fitWidth, 200);
});

/* ================= 启动 ================= */

API.get('/api/exams/' + EXAM_ID + '/students').then(function (r) {
  students = r.items;
  if (!students.length) {
    toast('这场考试还没有学生。先去「学生与答卷」页导入名单。', 'warn', 6000);
    setTimeout(function () { location.href = 'students.html?exam=' + EXAM_ID; }, 2000);
    return;
  }
  var want = param('student');
  var start = 0;
  if (want) {
    for (var i = 0; i < students.length; i++) {
      if (String(students[i].id) === String(want)) { start = i; break; }
    }
  }
  return loadStudent(start);
}).catch(function (err) {
  toast('打不开这场考试：' + err.message, 'err');
  setTimeout(function () { location.href = 'index.html'; }, 1500);
});
