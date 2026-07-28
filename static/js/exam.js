/* 考试设置 + 题卡 + 标准答案卷 */
'use strict';

var EXAM_ID = param('exam');
var exam = null;
var qtypes = [];

if (!EXAM_ID) location.href = 'index.html';

function fillForm() {
  $('#f-name').value = exam.name || '';
  $('#f-klass').value = exam.klass || '';
  $('#f-subject').value = exam.subject || '';
  $('#f-date').value = exam.exam_date || '';
  $('#f-full').value = exam.full_score;
  $('#f-pass').value = exam.pass_score;
  $('#f-good').value = exam.excellent_score;
  $('#f-obj').value = exam.objective_full;
  $('#btn-next').href = 'students.html?exam=' + exam.id;
}

function updateSum(questions) {
  var sub = 0;
  questions.forEach(function (q) { sub += Number(q.max_score) || 0; });
  var obj = Number(exam.objective_full) || 0;
  var full = Number(exam.full_score) || 0;
  var line = '主观题合计 ' + fmt(sub) + ' 分';
  if (obj) line += ' ＋ 客观题 ' + fmt(obj) + ' 分 ＝ ' + fmt(sub + obj) + ' 分';
  if (full) {
    var diff = Math.round((sub + obj - full) * 100) / 100;
    line += '；试卷满分填的是 ' + fmt(full) + ' 分';
    if (diff !== 0) {
      line += '，' + (diff > 0 ? '多出 ' + fmt(diff) : '还差 ' + fmt(-diff)) +
        ' 分，检查一下是不是漏了题或者满分填错了。';
    } else {
      line += '，对得上。';
    }
  }
  $('#sum-hint').textContent = line;
}

/* ---------- 题卡 ---------- */
function typeSelect(value, onchange) {
  var sel = h('select', { onchange: onchange });
  qtypes.forEach(function (t) {
    sel.appendChild(h('option', { value: t.value, selected: t.value === value }, t.label));
  });
  return sel;
}

function renderQuestions(items) {
  var box = $('#qlist');
  box.innerHTML = '';
  updateSum(items);
  if (!items.length) {
    box.appendChild(h('p', { class: 'empty' },
      '还没有题目。点「加一道题」或「批量加题」开始登记要批的主观题。'));
    return;
  }
  var table = h('table', {},
    h('thead', {}, h('tr', {},
      h('th', { style: 'width:96px' }, '题号'),
      h('th', { style: 'width:110px' }, '题型'),
      h('th', { style: 'width:80px' }, '满分'),
      h('th', {}, '题干 / 参考答案 / 评分要点'),
      h('th', { style: 'width:110px' }, '知识点'),
      h('th', { style: 'width:130px' }, ''))));
  var tbody = h('tbody');

  items.forEach(function (q, idx) {
    var patch = {};
    function mark(key, val) { patch[key] = val; }
    function save() {
      if (!Object.keys(patch).length) return Promise.resolve();
      var body = patch;
      patch = {};
      return guard(API.put('/api/questions/' + q.id, body), '保存失败')
        .then(function (r) { if (r) { load(); } });
    }

    var noInput = h('input', { type: 'text', value: q.no_label,
      onchange: function () { mark('no_label', this.value); save(); } });
    var typeSel = typeSelect(q.qtype, function () { mark('qtype', this.value); save(); });
    var scoreInput = h('input', { type: 'number', min: '0', step: '0.5',
      value: q.max_score,
      onchange: function () { mark('max_score', Number(this.value) || 0); save(); } });
    var kpInput = h('input', { type: 'text', value: q.knowledge_point,
      placeholder: '如：文言实词',
      onchange: function () { mark('knowledge_point', this.value); save(); } });

    var stem = h('textarea', { rows: '2', placeholder: '题干（可选，填了 AI 判得更准）',
      value: q.stem, onchange: function () { mark('stem', this.value); save(); } });
    var key = h('textarea', { rows: '3', placeholder: '参考答案（AI 会照这个判，意思相近即给分）',
      value: q.answer_key, onchange: function () { mark('answer_key', this.value); save(); } });
    var rubric = h('textarea', { rows: '2', placeholder: '评分要点（如：答出A得2分，答出B得3分）',
      value: q.rubric, onchange: function () { mark('rubric', this.value); save(); } });

    var detail = h('details', { open: !q.answer_key && !q.stem ? null : true },
      h('summary', { style: 'color:#6b7785;font-size:13px' },
        q.answer_key ? '题干 / 参考答案 / 评分要点（已填）' : '展开填题干、参考答案、评分要点'),
      h('div', { style: 'margin-top:6px' },
        h('label', { style: 'font-size:12px;color:#6b7785' }, '题干'), stem,
        h('label', { style: 'font-size:12px;color:#6b7785' }, '参考答案'), key,
        h('label', { style: 'font-size:12px;color:#6b7785' }, '评分要点'), rubric));

    tbody.appendChild(h('tr', {},
      h('td', {}, noInput),
      h('td', {}, typeSel),
      h('td', {}, scoreInput),
      h('td', {}, detail),
      h('td', {}, kpInput),
      h('td', { style: 'white-space:nowrap' },
        h('button', { class: 'btn small ghost', title: '上移',
          disabled: idx === 0, onclick: function () { move(items, idx, -1); } }, '↑'),
        ' ',
        h('button', { class: 'btn small ghost', title: '下移',
          disabled: idx === items.length - 1,
          onclick: function () { move(items, idx, 1); } }, '↓'),
        ' ',
        h('button', { class: 'btn small danger',
          onclick: function () { removeQuestion(q); } }, '删'))));
  });
  table.appendChild(tbody);
  box.appendChild(h('div', { class: 'table-scroll' }, table));
}

function move(items, idx, delta) {
  var ids = items.map(function (q) { return q.id; });
  var target = idx + delta;
  if (target < 0 || target >= ids.length) return;
  var tmp = ids[idx]; ids[idx] = ids[target]; ids[target] = tmp;
  guard(API.post('/api/exams/' + EXAM_ID + '/questions/reorder', { ids: ids }),
    '调整顺序失败').then(function (r) { if (r) renderQuestions(r.items); });
}

function removeQuestion(q) {
  confirmBox('删除题目', '删掉「' + (q.no_label || '这道题') +
    '」，所有学生这道题的得分和 AI 建议也会一起没掉。确定吗？',
    { okText: '删除', danger: true }).then(function (ok) {
      if (!ok) return;
      guard(API.del('/api/questions/' + q.id), '删除失败').then(function (r) {
        if (r) { toast('已删除', 'ok'); load(); }
      });
    });
}

$('#btn-add').addEventListener('click', function () {
  guard(API.post('/api/exams/' + EXAM_ID + '/questions',
    { items: [{ no_label: '', qtype: 'essay', max_score: 10 }] }), '添加失败')
    .then(function (r) { if (r) renderQuestions(r.items); });
});

$('#btn-batch').addEventListener('click', function () {
  var count = h('input', { type: 'number', value: '5', min: '1', max: '80' });
  var score = h('input', { type: 'number', value: '12', min: '0', step: '0.5' });
  var prefix = h('input', { type: 'text', value: '三、', placeholder: '如：三、' });
  var start = h('input', { type: 'number', value: '1', min: '1' });
  var type = typeSelect('essay');
  var body = h('div', {},
    h('div', { class: 'row' },
      h('div', { class: 'field' }, h('label', {}, '加几道'), count),
      h('div', { class: 'field' }, h('label', {}, '每题满分'), score),
      h('div', { class: 'field' }, h('label', {}, '题型'), type)),
    h('div', { class: 'row', style: 'margin-top:10px' },
      h('div', { class: 'field' }, h('label', {}, '题号前缀'), prefix),
      h('div', { class: 'field' }, h('label', {}, '起始编号'), start)),
    h('p', { class: 'hint', style: 'margin-top:10px' },
      '会生成「三、1」「三、2」这样的题号，之后可以逐个改。'));
  modal('批量加题', body, { okText: '生成' }).then(function (ok) {
    if (!ok) return;
    var n = Math.max(1, Math.min(80, Number(count.value) || 1));
    var from = Number(start.value) || 1;
    var items = [];
    for (var i = 0; i < n; i++) {
      items.push({
        no_label: prefix.value + (from + i),
        qtype: type.value,
        max_score: Number(score.value) || 0
      });
    }
    guard(API.post('/api/exams/' + EXAM_ID + '/questions', { items: items }), '批量加题失败')
      .then(function (r) { if (r) { toast('加了 ' + n + ' 道题', 'ok'); renderQuestions(r.items); } });
  });
});

$('#btn-copy').addEventListener('click', function () {
  guard(API.get('/api/exams'), '读取考试列表失败').then(function (data) {
    if (!data) return;
    var others = data.items.filter(function (e) {
      return String(e.id) !== String(EXAM_ID) && e.question_count > 0;
    });
    if (!others.length) { toast('没有别的考试有题卡可以复制。', 'warn'); return; }
    var sel = h('select', {});
    others.forEach(function (e) {
      sel.appendChild(h('option', { value: e.id },
        e.name + '（' + e.question_count + ' 道题）'));
    });
    modal('从别的考试复制题卡', h('div', {},
      h('p', { class: 'hint' }, '题目会追加到当前题卡后面，不会覆盖已有的题。'), sel),
      { okText: '复制' }).then(function (ok) {
        if (!ok) return;
        guard(API.post('/api/exams/' + EXAM_ID + '/questions/copy',
          { from_exam_id: Number(sel.value) }), '复制失败').then(function (r) {
            if (r) { toast('复制了 ' + r.copied + ' 道题', 'ok'); renderQuestions(r.items); }
          });
      });
  });
});

$('#btn-save').addEventListener('click', function () {
  guard(API.put('/api/exams/' + EXAM_ID, {
    name: $('#f-name').value.trim(),
    klass: $('#f-klass').value.trim(),
    subject: $('#f-subject').value.trim(),
    exam_date: $('#f-date').value,
    full_score: Number($('#f-full').value || 0),
    pass_score: Number($('#f-pass').value || 0),
    excellent_score: Number($('#f-good').value || 0),
    objective_full: Number($('#f-obj').value || 0)
  }), '保存失败').then(function (r) {
    if (!r) return;
    toast('已保存', 'ok');
    location.reload();
  });
});

/* ---------- 标准答案卷 ---------- */
function renderKeyPages(items) {
  var box = $('#key-list');
  box.innerHTML = '';
  if (!items.length) return;
  items.forEach(function (p) {
    box.appendChild(h('div', { class: 'thumb' },
      h('img', { src: fileUrl(p.image_path), alt: '标准答案第 ' + p.page_no + ' 页',
        style: p.rotate ? 'transform:rotate(' + p.rotate + 'deg)' : null }),
      h('div', { class: 'cap' }, '第 ' + p.page_no + ' 页'),
      h('div', { style: 'display:flex;gap:2px;padding:0 3px 4px' },
        h('button', { class: 'btn small ghost', style: 'flex:1',
          onclick: function () {
            guard(API.post('/api/answerkey/' + p.id + '/rotate', { delta: 90 }), '旋转失败')
              .then(function (r) { if (r) loadKey(); });
          } }, '旋转'),
        h('button', { class: 'btn small danger', style: 'flex:1',
          onclick: function () {
            guard(API.del('/api/answerkey/' + p.id), '删除失败')
              .then(function (r) { if (r) loadKey(); });
          } }, '删'))));
  });
}

function uploadKey(files) {
  if (!files || !files.length) return;
  var fd = new FormData();
  for (var i = 0; i < files.length; i++) fd.append('file' + i, files[i]);
  toast('正在导入标准答案…');
  guard(API.upload('/api/exams/' + EXAM_ID + '/answerkey', fd), '导入失败')
    .then(function (r) {
      if (!r) return;
      toast('导入了 ' + r.count + ' 页', 'ok');
      if (r.problems && r.problems.length) toast(r.problems.join('\n'), 'warn', 9000);
      renderKeyPages(r.items);
    });
}

var drop = $('#key-drop');
$('#key-file').addEventListener('change', function () { uploadKey(this.files); this.value = ''; });
['dragenter', 'dragover'].forEach(function (ev) {
  drop.addEventListener(ev, function (e) {
    e.preventDefault(); drop.classList.add('over');
  });
});
['dragleave', 'drop'].forEach(function (ev) {
  drop.addEventListener(ev, function (e) {
    e.preventDefault(); drop.classList.remove('over');
  });
});
drop.addEventListener('drop', function (e) {
  if (e.dataTransfer && e.dataTransfer.files) uploadKey(e.dataTransfer.files);
});

function loadKey() {
  guard(API.get('/api/exams/' + EXAM_ID + '/answerkey'), '读取标准答案失败')
    .then(function (r) { if (r) renderKeyPages(r.items); });
}

/* ---------- 加载 ---------- */
function load() {
  return API.get('/api/exams/' + EXAM_ID).then(function (data) {
    exam = data;
    mountHeader({ tab: 'exam', exam: exam });
    fillForm();
    renderQuestions(data.questions);
  }).catch(function (err) {
    toast('打不开这场考试：' + err.message, 'err');
    setTimeout(function () { location.href = 'index.html'; }, 1500);
  });
}

API.get('/api/config').then(function (cfg) { qtypes = cfg.qtypes; })
  .then(load).then(loadKey);
