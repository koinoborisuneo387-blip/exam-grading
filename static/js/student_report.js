/* 个人成绩报告：总分、名次、逐题得分（带班平均对照）、错题清单、评语。
   整页是给打印设计的：顶部工具条 no-print，剩下的就是一页 A4。 */
'use strict';

var SID = param('student');
if (!SID) location.href = 'index.html';

function statBox(label, value, unit) {
  return h('div', { class: 'stat-box' },
    h('div', { class: 'k' }, label),
    h('div', { class: 'v' }, value, unit ? h('span',
      { style: 'font-size:13px;font-weight:400;color:#6b7785' }, unit) : null));
}

function render(rep) {
  var exam = rep.exam;
  var stu = rep.student;
  var paper = rep.paper || {};
  var done = paper.status === 'done';

  $('#back').href = 'report.html?exam=' + exam.id;
  document.title = stu.name + ' — 个人成绩报告';

  var box = $('#sheet');
  box.innerHTML = '';

  var subParts = [];
  if (exam.klass) subParts.push(exam.klass);
  if (exam.subject) subParts.push(exam.subject);
  if (exam.exam_date) subParts.push(exam.exam_date);
  box.appendChild(h('div', { class: 'rp-head' },
    h('h1', {}, exam.name + ' · 个人成绩报告'),
    h('p', { class: 'rp-sub' }, subParts.join('　'))));
  box.appendChild(h('p', { class: 'rp-stu' },
    stu.name + (stu.student_no ? '　学号 ' + stu.student_no : '')));

  if (!done) {
    box.appendChild(h('p', { class: 'rp-warn' },
      '注意：这份卷子还没批完，下面的分数不是最终成绩，名次也还排不了。'));
  }

  var grid = h('div', { class: 'stat-grid' });
  grid.appendChild(statBox('总分', fmt(paper.total_score, '0'),
    ' / ' + fmt(exam.full_score)));
  grid.appendChild(statBox('名次', rep.rank ? '第 ' + rep.rank + ' 名' : '—',
    rep.rank ? '　已批 ' + rep.graded_count + ' 人' : ''));
  grid.appendChild(statBox('班级平均', fmt(rep.class_mean)));
  if (Number(exam.objective_full) > 0) {
    grid.appendChild(statBox('客观题得分', fmt(paper.objective_score, '0'),
      ' / ' + fmt(exam.objective_full)));
  }
  box.appendChild(grid);

  /* 逐题得分 */
  var hasComment = rep.items.some(function (it) { return !!it.comment; });
  var tbody = h('tbody');
  rep.items.forEach(function (it) {
    var tr = h('tr', {},
      h('td', {}, it.no_label || '—'),
      h('td', {}, it.qtype_label),
      h('td', { class: 'num' }, fmt(it.max_score)),
      h('td', { class: 'num' },
        it.score === null || it.score === undefined ? '—' : h('b', {}, fmt(it.score))),
      h('td', { class: 'num' }, fmt(it.class_mean)));
    if (hasComment) tr.appendChild(h('td', {}, it.comment || ''));
    tbody.appendChild(tr);
  });
  var head = h('tr', {},
    h('th', {}, '题号'), h('th', {}, '题型'),
    h('th', { class: 'num' }, '满分'), h('th', { class: 'num' }, '得分'),
    h('th', { class: 'num' }, '班级平均'));
  if (hasComment) head.appendChild(h('th', {}, '评语'));
  box.appendChild(h('div', { class: 'rp-section' },
    h('b', {}, '各题得分'),
    h('div', { class: 'table-scroll' },
      h('table', {}, h('thead', {}, head), tbody))));

  /* 错题清单 */
  var wrongSec = h('div', { class: 'rp-section' }, h('b', {}, '需要补强的题'));
  if (rep.wrong.length) {
    var wbody = h('tbody');
    rep.wrong.forEach(function (w) {
      wbody.appendChild(h('tr', {},
        h('td', {}, w.no_label || '—'),
        h('td', {}, w.knowledge_point || '—'),
        h('td', { class: 'num' }, fmt(w.score) + ' / ' + fmt(w.max_score)),
        h('td', { class: 'num' }, '扣 ' + fmt(w.lost) + ' 分')));
    });
    wrongSec.appendChild(h('div', { class: 'table-scroll' },
      h('table', {},
        h('thead', {}, h('tr', {},
          h('th', {}, '题号'), h('th', {}, '知识点'),
          h('th', { class: 'num' }, '得分'), h('th', { class: 'num' }, '扣分'))),
        wbody)));
    wrongSec.appendChild(h('p', { class: 'hint', style: 'margin:6px 0 0' },
      '列的是得分率低于 60% 的题，按扣分多少排，越靠前越值得回头看。'));
  } else {
    wrongSec.appendChild(h('p', { style: 'margin:0;color:#15803d' },
      done ? '没有得分率低于 60% 的题。' : '（批完之后这里会列出需要补强的题）'));
  }
  box.appendChild(wrongSec);

  /* 老师总评 */
  if (paper.comment) {
    box.appendChild(h('div', { class: 'rp-section' },
      h('b', {}, '老师评语'),
      h('div', { class: 'rp-comment' }, paper.comment)));
  }

  box.appendChild(h('p', { class: 'rp-foot' },
    '试卷批改系统 · 打印于 ' + new Date().toLocaleDateString('zh-CN')));
}

API.get('/api/students/' + SID + '/report').then(render).catch(function (err) {
  toast('读取个人报告失败：' + err.message, 'err');
  setTimeout(function () { location.href = 'index.html'; }, 1500);
});
