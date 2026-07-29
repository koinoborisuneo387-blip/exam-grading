/* 成绩分析：班级统计、题目分析、知识点、成绩总表、导出 */
'use strict';

var EXAM_ID = param('exam');
if (!EXAM_ID) location.href = 'index.html';

function statBox(label, value, unit) {
  return h('div', { class: 'stat-box' },
    h('div', { class: 'k' }, label),
    h('div', { class: 'v' }, value, unit ? h('span',
      { style: 'font-size:13px;font-weight:400;color:#6b7785' }, unit) : null));
}

function rateBar(rate) {
  var cls = rate < 60 ? 'low' : (rate < 80 ? 'mid' : '');
  return h('div', { style: 'display:flex;align-items:center;gap:6px' },
    h('div', { class: 'rate-bar ' + cls },
      h('i', { style: 'width:' + Math.max(0, Math.min(100, rate)) + '%' })),
    h('span', { style: 'font-variant-numeric:tabular-nums' }, fmt(rate) + '%'));
}

function renderStats(stats, exam) {
  $('#title').textContent = exam.name + (exam.klass ? '　' + exam.klass : '') +
    (exam.subject ? '　' + exam.subject : '');
  var box = $('#stats');
  box.innerHTML = '';
  if (!stats.graded_count) {
    box.appendChild(h('p', { class: 'empty', style: 'grid-column:1/-1' },
      '还没有批完的答卷，暂时没有统计数据。批完一个学生的全部主观题，他才会计入统计。'));
  } else {
    box.appendChild(statBox('已批 / 应考', stats.graded_count + ' / ' + stats.total_students, ' 人'));
    box.appendChild(statBox('平均分', fmt(stats.mean)));
    box.appendChild(statBox('最高分', fmt(stats.max)));
    box.appendChild(statBox('最低分', fmt(stats.min)));
    box.appendChild(statBox('中位数', fmt(stats.median)));
    box.appendChild(statBox('标准差', fmt(stats.stdev)));
    box.appendChild(statBox('及格率', fmt(stats.pass_rate) + '%',
      '　' + stats.pass_count + ' 人 ≥ ' + fmt(stats.pass_line)));
    box.appendChild(statBox('优秀率', fmt(stats.good_rate) + '%',
      '　' + stats.good_count + ' 人 ≥ ' + fmt(stats.good_line)));
  }

  var hist = $('#hist');
  var labels = $('#hist-labels');
  hist.innerHTML = '';
  labels.innerHTML = '';
  var buckets = stats.buckets || [];
  var maxCount = 1;
  buckets.forEach(function (b) { if (b.count > maxCount) maxCount = b.count; });
  buckets.forEach(function (b) {
    hist.appendChild(h('div', { class: 'bar-wrap' },
      h('div', { class: 'bar-n' }, b.count || ''),
      h('div', { class: 'bar', style: 'height:' + (b.count / maxCount * 100) + '%',
        title: b.label + ' 分：' + b.count + ' 人' })));
    labels.appendChild(h('div', {}, b.label));
  });
}

function renderQuestionStats(items) {
  var box = $('#qstats');
  box.innerHTML = '';
  if (!items.length) {
    box.appendChild(h('p', { class: 'empty' }, '还没有题目。'));
    return;
  }
  var tbody = h('tbody');
  items.forEach(function (q) {
    tbody.appendChild(h('tr', {},
      h('td', {}, q.no_label || '—'),
      h('td', {}, q.qtype_label),
      h('td', {}, q.knowledge_point || '—'),
      h('td', { class: 'num' }, fmt(q.max_score)),
      h('td', { class: 'num' }, fmt(q.mean)),
      h('td', { style: 'width:180px' }, rateBar(q.rate)),
      h('td', { class: 'num' }, q.full_count),
      h('td', { class: 'num' }, q.zero_count),
      h('td', { class: 'num' }, q.graded_count)));
  });
  box.appendChild(h('div', { class: 'table-scroll' },
    h('table', {},
      h('thead', {}, h('tr', {},
        h('th', {}, '题号'), h('th', {}, '题型'), h('th', {}, '知识点'),
        h('th', { class: 'num' }, '满分'), h('th', { class: 'num' }, '平均分'),
        h('th', {}, '得分率'), h('th', { class: 'num' }, '满分人数'),
        h('th', { class: 'num' }, '零分人数'), h('th', { class: 'num' }, '已批人数'))),
      tbody)));
}

function renderKnowledge(items) {
  var box = $('#kstats');
  box.innerHTML = '';
  if (!items.length) { box.appendChild(h('p', { class: 'empty' }, '暂无数据。')); return; }
  var tbody = h('tbody');
  items.forEach(function (k) {
    tbody.appendChild(h('tr', {},
      h('td', {}, k.knowledge_point),
      h('td', {}, k.questions.join('、')),
      h('td', { class: 'num' }, fmt(k.full)),
      h('td', { class: 'num' }, fmt(k.got)),
      h('td', { style: 'width:200px' }, rateBar(k.rate))));
  });
  box.appendChild(h('div', { class: 'table-scroll' },
    h('table', {},
      h('thead', {}, h('tr', {},
        h('th', {}, '知识点'), h('th', {}, '涉及题号'),
        h('th', { class: 'num' }, '合计满分'), h('th', { class: 'num' }, '平均得分'),
        h('th', {}, '掌握程度'))),
      tbody)));
}

function renderTable(table) {
  var box = $('#table');
  box.innerHTML = '';
  var exam = table.exam;
  var questions = table.questions;
  var showObj = Number(exam.objective_full) > 0;

  var head = h('tr', {},
    h('th', {}, '学号'), h('th', {}, '姓名'));
  questions.forEach(function (q) {
    head.appendChild(h('th', {
      class: 'num',
      title: (q.no_label || '题') + '　满分 ' + fmt(q.max_score)
    }, (q.no_label || '题') + '(' + fmt(q.max_score) + ')'));
  });
  if (showObj) head.appendChild(h('th', { class: 'num' }, '客观题'));
  head.appendChild(h('th', { class: 'num' }, '总分'));
  head.appendChild(h('th', { class: 'num' }, '名次'));
  head.appendChild(h('th', {}, '状态'));
  head.appendChild(h('th', { class: 'no-print' }, ''));

  var tbody = h('tbody');
  table.rows.forEach(function (r) {
    var tr = h('tr', {},
      h('td', {}, r.student_no || '—'),
      h('td', {}, r.name));
    r.scores.forEach(function (s) {
      tr.appendChild(h('td', { class: 'num' }, s === null || s === undefined ? '—' : fmt(s)));
    });
    if (showObj) tr.appendChild(h('td', { class: 'num' }, fmt(r.objective_score)));
    tr.appendChild(h('td', { class: 'num' }, h('b', {}, fmt(r.total_score))));
    tr.appendChild(h('td', { class: 'num' }, r.rank || '—'));
    tr.appendChild(h('td', {}, statusTag(r.status)));
    tr.appendChild(h('td', { class: 'no-print', style: 'white-space:nowrap' },
      h('a', { class: 'btn small ghost',
        href: 'grade.html?exam=' + EXAM_ID + '&student=' + r.student_id }, '批改'),
      r.paper_id ? h('a', { class: 'btn small ghost', style: 'margin-left:4px',
        href: '/api/papers/' + r.paper_id + '/export/marked.zip' }, '下载批注卷') : null));
    tbody.appendChild(tr);
  });
  box.appendChild(h('div', { class: 'table-scroll' },
    h('table', {}, h('thead', {}, head), tbody)));
}

$('#dl-xlsx').href = '/api/exams/' + EXAM_ID + '/export/scores.xlsx';
$('#dl-csv').href = '/api/exams/' + EXAM_ID + '/export/scores.csv';
$('#dl-wrong').href = '/api/exams/' + EXAM_ID + '/export/wrong.xlsx';
$('#dl-marked').href = '/api/exams/' + EXAM_ID + '/export/marked.zip';

API.get('/api/exams/' + EXAM_ID + '/report').then(function (rep) {
  var exam = rep.table.exam;
  mountHeader({ tab: 'report', exam: exam });
  renderStats(rep.stats, exam);
  renderQuestionStats(rep.questions);
  renderKnowledge(rep.knowledge);
  renderTable(rep.table);
}).catch(function (err) {
  toast('读取成绩失败：' + err.message, 'err');
  setTimeout(function () { location.href = 'index.html'; }, 1500);
});
