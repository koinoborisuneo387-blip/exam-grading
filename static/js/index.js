/* 首页：考试列表 + 新建考试 */
'use strict';

mountHeader({});

function render(items) {
  var box = $('#list');
  box.innerHTML = '';
  if (!items.length) {
    box.appendChild(h('p', { class: 'empty' },
      '还没有考试。在上面建一场，然后依次：登记题目 → 导入学生和答卷 → 开始批改。'));
    return;
  }
  var table = h('table', {},
    h('thead', {}, h('tr', {},
      h('th', {}, '考试'), h('th', {}, '班级'), h('th', {}, '科目'),
      h('th', {}, '日期'), h('th', { class: 'num' }, '满分'),
      h('th', { class: 'num' }, '题目'), h('th', { class: 'num' }, '学生'),
      h('th', {}, '批改进度'), h('th', {}, ''))));
  var tbody = h('tbody');
  items.forEach(function (e) {
    var done = e.done_count, total = e.student_count;
    var progress = total ? (done + ' / ' + total) : '—';
    tbody.appendChild(h('tr', {},
      h('td', {}, h('a', { href: 'exam.html?exam=' + e.id }, e.name)),
      h('td', {}, e.klass || '—'),
      h('td', {}, e.subject || '—'),
      h('td', {}, e.exam_date || '—'),
      h('td', { class: 'num' }, fmt(e.full_score)),
      h('td', { class: 'num' }, e.question_count),
      h('td', { class: 'num' }, e.student_count),
      h('td', {}, progress,
        total && done === total ? h('span', { class: 'tag done',
          style: 'margin-left:6px' }, '已批完') : null),
      h('td', { style: 'white-space:nowrap' },
        h('a', { class: 'btn small ghost', href: 'grade.html?exam=' + e.id }, '批改'),
        ' ',
        h('a', { class: 'btn small ghost', href: 'report.html?exam=' + e.id }, '成绩'),
        ' ',
        h('button', {
          class: 'btn small danger',
          onclick: function () { removeExam(e); }
        }, '删除'))));
  });
  table.appendChild(tbody);
  box.appendChild(h('div', { class: 'table-scroll' }, table));
}

function removeExam(exam) {
  var input = h('input', { type: 'text', placeholder: exam.name });
  var body = h('div', {},
    h('p', {}, '这会连同这场考试的题目、学生名单、答卷图片、成绩和批注一起删掉，删了找不回来。'),
    h('p', {}, '确认的话，把考试名称原样打一遍：'),
    input);
  modal('删除考试「' + exam.name + '」', body, {
    okText: '确认删除', danger: true, getValue: function () { return input.value; }
  }).then(function (value) {
    if (value === null) return;
    if (value.trim() !== exam.name) { toast('名称没对上，没有删。', 'warn'); return; }
    guard(API.del('/api/exams/' + exam.id + '?confirm=' + encodeURIComponent(exam.name)),
      '删除失败').then(function (r) {
        if (!r) return;
        toast('已删除', 'ok');
        load();
      });
  });
}

function load() {
  guard(API.get('/api/exams'), '读取考试列表失败').then(function (data) {
    if (data) render(data.items);
  });
}

$('#btn-create').addEventListener('click', function () {
  var name = $('#f-name').value.trim();
  if (!name) { toast('先填考试名称', 'warn'); $('#f-name').focus(); return; }
  guard(API.post('/api/exams', {
    name: name,
    klass: $('#f-klass').value.trim(),
    subject: $('#f-subject').value.trim(),
    exam_date: $('#f-date').value,
    full_score: Number($('#f-full').value || 0),
    pass_score: Number($('#f-pass').value || 0),
    excellent_score: Number($('#f-good').value || 0),
    objective_full: Number($('#f-obj').value || 0)
  }), '建立考试失败').then(function (exam) {
    if (exam) location.href = 'exam.html?exam=' + exam.id;
  });
});

$('#f-name').addEventListener('keydown', function (e) {
  if (e.key === 'Enter') $('#btn-create').click();
});

load();
