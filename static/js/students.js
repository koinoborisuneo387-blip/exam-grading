/* 学生名单 + 答卷导入 + 页面绑定 */
'use strict';

var EXAM_ID = param('exam');
var exam = null;
var students = [];
var stageItems = [];
var selected = {};

if (!EXAM_ID) location.href = 'index.html';

/* ---------- 学生名单 ---------- */
function renderStudents() {
  var box = $('#stulist');
  box.innerHTML = '';
  if (!students.length) {
    box.appendChild(h('p', { class: 'empty' }, '还没有学生。在上面粘贴名单加进来。'));
    return;
  }
  var table = h('table', {},
    h('thead', {}, h('tr', {},
      h('th', { style: 'width:60px' }, '序'),
      h('th', { style: 'width:110px' }, '学号'),
      h('th', { style: 'width:130px' }, '姓名'),
      h('th', { class: 'num', style: 'width:80px' }, '答卷页'),
      h('th', { style: 'width:90px' }, '批改'),
      h('th', { class: 'num', style: 'width:80px' }, '总分'),
      h('th', {}, ''))));
  var tbody = h('tbody');
  students.forEach(function (s, i) {
    var fileInput = h('input', {
      type: 'file', multiple: true, accept: 'image/*,.pdf', hidden: true,
      onchange: function () { uploadFor(s.id, this.files); this.value = ''; }
    });
    tbody.appendChild(h('tr', {},
      h('td', {}, i + 1),
      h('td', {}, h('input', { type: 'text', value: s.student_no || '',
        onchange: function () { saveStudent(s, { student_no: this.value }); } })),
      h('td', {}, h('input', { type: 'text', value: s.name,
        onchange: function () { saveStudent(s, { name: this.value }); } })),
      h('td', { class: 'num' }, s.page_count || 0),
      h('td', {}, statusTag(s.status || 'todo')),
      h('td', { class: 'num' }, s.paper_id ? fmt(s.total_score) : '—'),
      h('td', { style: 'white-space:nowrap' },
        fileInput,
        h('button', { class: 'btn small ghost',
          onclick: function () { fileInput.click(); } }, '传答卷'),
        ' ',
        h('a', { class: 'btn small ghost',
          href: 'grade.html?exam=' + EXAM_ID + '&student=' + s.id }, '批改'),
        ' ',
        h('button', { class: 'btn small danger',
          onclick: function () { removeStudent(s); } }, '删'))));
  });
  table.appendChild(tbody);
  box.appendChild(h('div', { class: 'table-scroll' }, table));

  var sel = $('#bind-student');
  sel.innerHTML = '';
  students.forEach(function (s) {
    sel.appendChild(h('option', { value: s.id },
      (s.student_no ? s.student_no + ' ' : '') + s.name +
      (s.page_count ? '（已有 ' + s.page_count + ' 页）' : '')));
  });
}

function saveStudent(s, patch) {
  guard(API.put('/api/students/' + s.id, patch), '保存失败')
    .then(function (r) { if (r) loadStudents(); });
}

function removeStudent(s) {
  confirmBox('删除学生', '删掉「' + s.name + '」，他的答卷图片、得分和批注都会一起没掉。确定吗？',
    { okText: '删除', danger: true }).then(function (ok) {
      if (!ok) return;
      guard(API.del('/api/students/' + s.id), '删除失败').then(function (r) {
        if (r) { toast('已删除', 'ok'); loadStudents(); }
      });
    });
}

$('#btn-roster').addEventListener('click', function () {
  var text = $('#roster').value.trim();
  if (!text) { toast('先把名单粘进来', 'warn'); return; }
  guard(API.post('/api/exams/' + EXAM_ID + '/students', { text: text }), '加入名单失败')
    .then(function (r) {
      if (!r) return;
      toast('加了 ' + r.created + ' 个学生', 'ok');
      $('#roster').value = '';
      loadStudents();
    });
});

/* ---------- 上传 ---------- */
function uploadFiles(files, studentId) {
  if (!files || !files.length) return;
  var fd = new FormData();
  for (var i = 0; i < files.length; i++) fd.append('file' + i, files[i]);
  var url = '/api/exams/' + EXAM_ID + '/upload';
  if (studentId) url += '?student_id=' + studentId;
  toast('正在导入，扫描件多的话要等十几秒…');
  guard(API.upload(url, fd), '导入失败').then(function (r) {
    if (!r) return;
    toast('导进来 ' + r.count + ' 页' + (r.bound ? '，已归到该学生名下' : '，请在下面分给学生'), 'ok');
    if (r.problems && r.problems.length) toast(r.problems.join('\n'), 'warn', 12000);
    loadStudents();
    loadStage();
  });
}

function uploadFor(studentId, files) { uploadFiles(files, studentId); }

var drop = $('#drop');
$('#file').addEventListener('change', function () { uploadFiles(this.files); this.value = ''; });
['dragenter', 'dragover'].forEach(function (ev) {
  drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('over'); });
});
['dragleave', 'drop'].forEach(function (ev) {
  drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('over'); });
});
drop.addEventListener('drop', function (e) {
  if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
});

/* ---------- 暂存区绑定 ---------- */
function renderStage() {
  $('#stage-panel').hidden = stageItems.length === 0;
  // 暂存区一变，之前那份确认草稿就作废了，别让老师对着旧列表点确认
  if ($('#confirm-box') && !$('#confirm-box').hidden) {
    setManualVisible(true);
    $('#confirm-list').innerHTML = '';
    $('#identify-status').textContent = '';
  }
  var box = $('#stage');
  box.innerHTML = '';
  stageItems.forEach(function (item, i) {
    var card = h('div', { class: 'thumb' + (selected[item.rel] ? ' sel' : ''),
      onclick: function () {
        if (selected[item.rel]) delete selected[item.rel];
        else selected[item.rel] = true;
        renderStage();
      } },
      h('img', { src: fileUrl(item.rel), alt: item.name, loading: 'lazy' }),
      h('div', { class: 'cap', title: item.name }, item.name),
      selected[item.rel] ? h('span', { class: 'badge' }, '✓') :
        h('span', { class: 'badge', style: 'background:#94a3b8' }, i + 1));
    box.appendChild(card);
  });
}

$('#btn-sel-all').addEventListener('click', function () {
  stageItems.forEach(function (it) { selected[it.rel] = true; });
  renderStage();
});
$('#btn-sel-none').addEventListener('click', function () { selected = {}; renderStage(); });

$('#btn-bind').addEventListener('click', function () {
  var rels = stageItems.filter(function (it) { return selected[it.rel]; })
    .map(function (it) { return it.rel; });
  if (!rels.length) { toast('先点选要绑定的页面', 'warn'); return; }
  var sid = Number($('#bind-student').value);
  if (!sid) { toast('先选学生', 'warn'); return; }
  guard(API.post('/api/exams/' + EXAM_ID + '/bind',
    { assignments: [{ student_id: sid, rels: rels }] }), '绑定失败')
    .then(function (r) {
      if (!r) return;
      toast('绑定了 ' + r.bound + ' 页', 'ok');
      selected = {};
      loadStudents();
      loadStage();
    });
});

$('#btn-auto').addEventListener('click', function () {
  var per = Math.max(1, Number($('#per-student').value) || 1);
  if (!students.length) { toast('先加学生名单', 'warn'); return; }
  var need = Math.ceil(stageItems.length / per);
  if (need > students.length) {
    toast('按每人 ' + per + ' 页算需要 ' + need + ' 个学生，名单里只有 ' +
      students.length + ' 个。请检查页数或名单。', 'warn', 8000);
    return;
  }
  var assignments = [];
  for (var i = 0; i < students.length; i++) {
    var slice = stageItems.slice(i * per, (i + 1) * per);
    if (!slice.length) break;
    assignments.push({
      student_id: students[i].id,
      rels: slice.map(function (it) { return it.rel; })
    });
  }
  var preview = assignments.slice(0, 3).map(function (a) {
    var s = students.filter(function (x) { return x.id === a.student_id; })[0];
    return s.name + ' ← ' + a.rels.length + ' 页';
  }).join('\n');
  confirmBox('按顺序自动分配',
    '会按名单顺序，把暂存区的 ' + stageItems.length + ' 页依次每 ' + per +
    ' 页分给一个学生，一共分给 ' + assignments.length + ' 个人。\n\n前几个是：\n' + preview +
    '\n\n分错了可以到学生那里删掉重来。', { okText: '开始分配' })
    .then(function (ok) {
      if (!ok) return;
      guard(API.post('/api/exams/' + EXAM_ID + '/bind', { assignments: assignments }),
        '自动分配失败').then(function (r) {
          if (!r) return;
          toast('分配了 ' + r.bound + ' 页', 'ok');
          selected = {};
          loadStudents();
          loadStage();
        });
    });
});

$('#btn-clear').addEventListener('click', function () {
  confirmBox('清空暂存区', '暂存区里还没分给学生的 ' + stageItems.length +
    ' 页会被删掉（已经绑给学生的不受影响）。确定吗？',
    { okText: '清空', danger: true }).then(function (ok) {
      if (!ok) return;
      guard(API.post('/api/exams/' + EXAM_ID + '/stage/clear'), '清空失败')
        .then(function (r) { if (r) { selected = {}; loadStage(); } });
    });
});

/* ---------- AI 读姓名 + 确认导入 ---------- */
/* 铁律：AI 读出来的姓名只是草稿。老师在这一屏点了「确认导入」才会建名单、绑答卷。
   认错名字 = 分数记到别人头上，而且要等整班批完才会发现。 */

var confirmGroups = null;

function setManualVisible(show) {
  $('#manual-box').hidden = !show;
  $('#confirm-box').hidden = show;
}

function relName(rel) {
  var parts = String(rel).split('/');
  return parts[parts.length - 1];
}

function confirmRow(group, index) {
  var row = h('div', { class: 'confirm-row' });
  var thumbs = h('div', { class: 'thumbs' });
  group.rels.forEach(function (rel) {
    thumbs.appendChild(h('img', {
      src: fileUrl(rel), alt: relName(rel), title: relName(rel) + '（点开看大图）',
      loading: 'lazy',
      onclick: function () {
        modal(relName(rel), h('img', { src: fileUrl(rel), style: 'width:100%' }),
          { okText: '关闭', cancelText: '关闭' });
      }
    }));
  });

  var nameInput = h('input', { type: 'text', value: group.name || '',
    placeholder: '姓名' });
  var noInput = h('input', { type: 'text', value: group.student_no || '',
    placeholder: '学号', style: 'width:90px' });
  var pick = h('select', {});
  var status = h('div', { class: 'status' });

  function rebuildOptions() {
    var typed = nameInput.value.trim();
    var keep = pick.value;
    pick.innerHTML = '';
    pick.appendChild(h('option', { value: '' }, '— 请指定这是谁 —'));
    pick.appendChild(h('option', { value: 'new' },
      typed ? ('新建学生：' + typed) : '新建学生（先填姓名）'));
    students.forEach(function (s) {
      pick.appendChild(h('option', { value: String(s.id) },
        '绑到已有：' + (s.student_no ? s.student_no + ' ' : '') + s.name));
    });
    pick.value = keep;
    if (pick.value === '') pick.value = keep;
  }

  rebuildOptions();
  // 只有高置信度才敢替老师预选；低置信度和读不出的一律留空，逼他做决定
  if (group.confidence === 'high' && group.student_id) {
    pick.value = String(group.student_id);
  } else if (group.confidence === 'none' && group.name) {
    pick.value = 'new';
  } else {
    pick.value = '';
  }

  function refresh() {
    var v = pick.value;
    var typed = nameInput.value.trim();
    row.classList.remove('ok', 'warn', 'bad');
    if (v === '') {
      // 红色配的话必须是「接下来该干什么」，不能只甩一句识别结论
      row.classList.add('bad');
      if (!group.name) {
        status.textContent = '● 没读出姓名。请在左边填上姓名，或从下拉框里选一个学生。';
      } else if (group.confidence === 'low') {
        status.textContent = '● ' + group.reason + '（请从下拉框里确认）';
      } else {
        status.textContent = '● 请从下拉框里指定这份卷子是谁的。';
      }
    } else if (v === 'new' && !typed) {
      // 不能建一个没名字的学生，这行必须挡住
      row.classList.add('bad');
      status.textContent = '● 要新建学生，得先把姓名填上。';
    } else if (v === 'new') {
      row.classList.add('warn');
      status.textContent = '● 会新建学生「' + typed + '」';
    } else {
      var s = students.filter(function (x) { return String(x.id) === v; })[0];
      var same = group.confidence === 'high' && String(group.student_id) === v;
      row.classList.add(same ? 'ok' : 'warn');
      status.textContent = (same ? '● ' + (group.reason || '和名单对上了') + '：'
        : '● 你指定绑到：') + (s ? s.name : '');
    }
    updateConfirmHint();
  }

  nameInput.addEventListener('input', function () { rebuildOptions(); refresh(); });
  noInput.addEventListener('input', function () { refresh(); });
  pick.addEventListener('change', refresh);

  row.appendChild(thumbs);
  row.appendChild(h('div', { class: 'fields' },
    h('div', { class: 'line' },
      h('span', { style: 'color:#6b7785;font-size:13px' }, '第 ' + (index + 1) + ' 份'),
      h('span', { style: 'color:#6b7785;font-size:13px' }, group.rels.length + ' 页'),
      nameInput, noInput, pick),
    status));

  row._get = function () {
    var v = pick.value;
    if (v === '') return null;
    var item = { rels: group.rels };
    if (v === 'new') {
      item.name = nameInput.value.trim();
      item.student_no = noInput.value.trim();
      if (!item.name) return null;
    } else {
      item.student_id = Number(v);
    }
    return item;
  };
  refresh();
  return row;
}

function updateConfirmHint() {
  var rows = $$('#confirm-list .confirm-row');
  var bad = rows.filter(function (r) { return r.classList.contains('bad'); }).length;
  $('#btn-confirm-bind').disabled = bad > 0 || rows.length === 0;
  $('#confirm-hint').textContent = bad > 0
    ? ('还有 ' + bad + ' 份没指定是谁，处理完才能导入。')
    : (rows.length + ' 份答卷都指定好了，可以导入。');
}

function renderConfirm(groups) {
  confirmGroups = groups;
  var box = $('#confirm-list');
  box.innerHTML = '';
  groups.forEach(function (g, i) { box.appendChild(confirmRow(g, i)); });
  setManualVisible(false);
  updateConfirmHint();
}

$('#btn-identify').addEventListener('click', function () {
  if (!stageItems.length) { toast('暂存区里没有页面。', 'warn'); return; }
  var btn = this;
  btn.disabled = true;
  var status = $('#identify-status');
  status.textContent = '正在把 ' + stageItems.length + ' 页发给 AI 读姓名…';
  Promise.all(stageItems.map(function (it) {
    return loadImage(fileUrl(it.rel)).then(function (img) {
      // 只要看清表头就够了，压得比批改时更狠，快也省钱
      return { rel: it.rel, image: shrinkImage(img, 1100, 0.75) };
    }).catch(function () { return null; });
  })).then(function (pages) {
    pages = pages.filter(function (p) { return p && p.image; });
    if (!pages.length) throw new Error('页面图片读不出来');
    return API.post('/api/exams/' + EXAM_ID + '/stage/identify', { pages: pages });
  }).then(function (r) {
    status.textContent = '读完了，共 ' + r.groups.length + ' 份答卷。请核对下面每一行。';
    renderConfirm(r.groups);
  }).catch(function (err) {
    status.textContent = '';
    toast(err.message + '\n可以改用下面的手动分配。', 'err', 11000);
    setManualVisible(true);
  }).then(function () { btn.disabled = false; });
});

$('#btn-confirm-cancel').addEventListener('click', function () {
  setManualVisible(true);
  $('#identify-status').textContent = '';
});

$('#btn-confirm-bind').addEventListener('click', function () {
  var rows = $$('#confirm-list .confirm-row');
  var assignments = [];
  for (var i = 0; i < rows.length; i++) {
    var item = rows[i]._get();
    if (!item) { toast('第 ' + (i + 1) + ' 份还没指定是谁。', 'warn'); return; }
    assignments.push(item);
  }
  var btn = this;
  btn.disabled = true;
  guard(API.post('/api/exams/' + EXAM_ID + '/bind', { assignments: assignments }),
    '导入失败').then(function (r) {
      btn.disabled = false;
      if (!r) return;
      toast('导入了 ' + r.bound + ' 页' +
        (r.created_students ? '，新建了 ' + r.created_students + ' 个学生' : ''), 'ok', 5000);
      setManualVisible(true);
      $('#identify-status').textContent = '';
      selected = {};
      loadStudents();
      loadStage();
    });
});

$('#btn-copy-roster').addEventListener('click', function () {
  guard(API.get('/api/exams'), '读取考试列表失败').then(function (data) {
    if (!data) return;
    var others = data.items.filter(function (e) {
      return String(e.id) !== String(EXAM_ID) && e.student_count > 0;
    });
    if (!others.length) { toast('没有别的考试有名单可以复制。', 'warn'); return; }
    var sel = h('select', {});
    others.forEach(function (e) {
      sel.appendChild(h('option', { value: e.id },
        e.name + (e.klass ? '（' + e.klass + '）' : '') + ' — ' + e.student_count + ' 人'));
    });
    modal('从上次考试复制名单', h('div', {},
      h('p', { class: 'hint' }, '学号或姓名已经在本场名单里的会自动跳过，不会重复。'), sel),
      { okText: '复制' }).then(function (ok) {
        if (!ok) return;
        guard(API.post('/api/exams/' + EXAM_ID + '/students/copy',
          { from_exam_id: Number(sel.value) }), '复制失败').then(function (r) {
            if (!r) return;
            toast('复制了 ' + r.created + ' 个学生' +
              (r.skipped ? '，跳过 ' + r.skipped + ' 个已有的' : ''), 'ok');
            loadStudents();
          });
      });
  });
});

/* ---------- AI 预批全班 ---------- */
var batchStop = false;

function logLine(text, kind) {
  var color = kind === 'err' ? '#dc2626' : (kind === 'ok' ? '#16a34a' : '#6b7785');
  $('#batch-log').appendChild(h('div', { style: 'color:' + color }, text));
  $('#batch-log').scrollTop = $('#batch-log').scrollHeight;
}

function shrinkPages(pages, maxWidth) {
  return Promise.all(pages.map(function (p) {
    return loadImage(fileUrl(p.image_path)).then(function (img) {
      return shrinkImage(img, maxWidth, 0.8);
    }).catch(function () { return null; });
  })).then(function (list) {
    return list.filter(function (x) { return !!x; });
  });
}

function runBatchAI() {
  var targets = students.filter(function (s) { return (s.page_count || 0) > 0; });
  if (!targets.length) {
    toast('还没有学生导入答卷图片，没得批。', 'warn');
    return;
  }
  confirmBox('AI 预批全班',
    '会对 ' + targets.length + ' 个已经导入答卷的学生，各调用一次智谱 GLM-5V。\n' +
    '按量计费，一个学生几分到一毛钱不等。\n\n' +
    'AI 给的只是建议分，需要你到批改台逐题「采纳」才会记进成绩。\n' +
    '中途想停随时点「停止」。', { okText: '开始' })
    .then(function (ok) {
      if (!ok) return;
      batchStop = false;
      $('#btn-batch-ai').disabled = true;
      $('#btn-batch-stop').hidden = false;
      $('#batch-log').innerHTML = '';

      var okCount = 0, failCount = 0;
      API.get('/api/exams/' + EXAM_ID + '/answerkey').then(function (r) {
        return shrinkPages(r.items || [], 1400);
      }).then(function (keyImgs) {
        if (keyImgs.length) logLine('标准答案 ' + keyImgs.length + ' 页，已随卷发送。');
        else logLine('没有标准答案卷，AI 只按题卡里的参考答案判。', 'warn');

        function step(i) {
          if (batchStop || i >= targets.length) {
            logLine('结束：成功 ' + okCount + ' 人，失败 ' + failCount + ' 人。' +
              (batchStop ? '（手动停止）' : ''), 'ok');
            $('#btn-batch-ai').disabled = false;
            $('#btn-batch-stop').hidden = true;
            $('#batch-progress').textContent = '';
            loadStudents();
            return Promise.resolve();
          }
          var s = targets[i];
          $('#batch-progress').textContent =
            '正在批 ' + s.name + '（' + (i + 1) + ' / ' + targets.length + ' 人）…';
          return API.get('/api/students/' + s.id + '/paper').then(function (d) {
            return shrinkPages(d.pages || [], 1500).then(function (imgs) {
              if (!imgs.length) throw new Error('答卷图片读不出来');
              return API.post('/api/ai/grade_paper', {
                paper_id: d.paper.id, images: imgs, key_images: keyImgs
              });
            });
          }).then(function (r) {
            okCount++;
            logLine('✓ ' + s.name + '：AI 批了 ' + r.items.length + ' 道题', 'ok');
          }).catch(function (err) {
            failCount++;
            logLine('✗ ' + s.name + '：' + err.message, 'err');
          }).then(function () { return step(i + 1); });
        }
        return step(0);
      }).catch(function (err) {
        logLine('出错了：' + err.message, 'err');
        $('#btn-batch-ai').disabled = false;
        $('#btn-batch-stop').hidden = true;
      });
    });
}

$('#btn-batch-ai').addEventListener('click', runBatchAI);
$('#btn-batch-stop').addEventListener('click', function () {
  batchStop = true;
  logLine('收到，批完当前这个学生就停。', 'warn');
});

/* ---------- 加载 ---------- */
function loadStudents() {
  return guard(API.get('/api/exams/' + EXAM_ID + '/students'), '读取学生名单失败')
    .then(function (r) { if (r) { students = r.items; renderStudents(); } });
}

function loadStage() {
  return guard(API.get('/api/exams/' + EXAM_ID + '/stage'), '读取暂存区失败')
    .then(function (r) { if (r) { stageItems = r.items; renderStage(); } });
}

function checkAI() {
  return API.get('/api/config').then(function (cfg) {
    var ai = cfg.ai;
    var ready = ai.enabled && ai.has_key && ai.base_url && ai.model;
    if (!ready || !ai.vision) {
      $('#btn-identify').disabled = true;
      $('#btn-batch-ai').disabled = true;
      $('#identify-status').textContent = !ready
        ? 'AI 还没配好，读不了姓名。点右上角「AI 设置」填好 Key 就能用；现在可以用下面的手动分配。'
        : '当前模型看不了图片，读不了姓名。到「AI 设置」换成智谱 GLM-5V 这类能看图的模型。';
    }
  }).catch(function () { /* 配置读不到不影响手动流程 */ });
}

API.get('/api/exams/' + EXAM_ID).then(function (data) {
  exam = data;
  mountHeader({ tab: 'students', exam: exam });
  $('#btn-next').href = 'grade.html?exam=' + exam.id;
  return loadStudents();
}).then(loadStage).then(checkAI).catch(function (err) {
  toast('打不开这场考试：' + err.message, 'err');
  setTimeout(function () { location.href = 'index.html'; }, 1500);
});
