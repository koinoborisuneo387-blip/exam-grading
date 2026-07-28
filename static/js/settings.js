/* AI 设置页 */
'use strict';

mountHeader({});

var presets = [];
var keyTouched = false;

$('#f-key').addEventListener('input', function () { keyTouched = true; });

function applyPreset(p) {
  $('#f-base').value = p.base_url;
  $('#f-model').value = p.model;
  $('#f-vision').checked = !!p.vision;
  $('#preset-note').textContent = p.note || '';
  var list = $('#model-list');
  list.innerHTML = '';
  (p.models || [p.model]).forEach(function (m) {
    list.appendChild(h('option', { value: m }));
  });
  $('#apply-url').textContent = p.apply_url;
  $('#apply-url').title = p.apply_url;
}

function fill(cfg) {
  var ai = cfg.ai;
  presets = cfg.presets || [];
  var sel = $('#f-preset');
  sel.innerHTML = '';
  presets.forEach(function (p, i) {
    sel.appendChild(h('option', { value: i }, p.label));
  });
  sel.appendChild(h('option', { value: 'custom' }, '自己填（其它 OpenAI 兼容服务）'));
  sel.addEventListener('change', function () {
    if (this.value === 'custom') { $('#preset-note').textContent = ''; return; }
    applyPreset(presets[Number(this.value)]);
  });

  // 现有配置命中哪个预设就选哪个
  var matched = -1;
  presets.forEach(function (p, i) {
    if (p.base_url === ai.base_url) matched = i;
  });
  sel.value = matched >= 0 ? String(matched) : 'custom';
  if (matched >= 0) {
    $('#preset-note').textContent = presets[matched].note || '';
    $('#apply-url').textContent = presets[matched].apply_url;
    var list = $('#model-list');
    list.innerHTML = '';
    (presets[matched].models || []).forEach(function (m) {
      list.appendChild(h('option', { value: m }));
    });
  }

  $('#key-path').textContent = cfg.key_file || '';
  var hint = $('#key-hint');
  hint.innerHTML = '';
  if (cfg.key_source) {
    hint.appendChild(h('span', { style: 'color:#16a34a;font-weight:600' },
      '✓ 已从这个文件读到密钥，AI 批改可以用了。'));
  } else if (ai.has_key) {
    hint.appendChild(h('span', { style: 'color:#d97706' },
      '文件里还是空的，不过下面的输入框里已经存过一个密钥，也能用。'));
  } else {
    hint.appendChild(h('span', { style: 'color:#dc2626' },
      '还没填密钥。把智谱的 API Key 粘进上面那个文件保存，再刷新本页。'));
  }

  $('#f-enabled').checked = !!ai.enabled;
  $('#f-base').value = ai.base_url || '';
  $('#f-model').value = ai.model || '';
  $('#f-vision').checked = !!ai.vision;
  $('#f-timeout').value = ai.timeout || 90;
  $('#f-key').value = ai.has_key ? '●●●●●●●●●●' : '';
  $('#f-key').placeholder = ai.has_key ? '已保存，留着不动就是不改' : '把 API Key 粘到这里';
  $('#i-version').textContent = 'v' + cfg.version;
}

function collect() {
  var body = {
    ai: {
      enabled: $('#f-enabled').checked,
      base_url: $('#f-base').value.trim(),
      model: $('#f-model').value.trim(),
      vision: $('#f-vision').checked,
      timeout: Number($('#f-timeout').value) || 90
    }
  };
  // 没动过 Key 输入框就不提交，免得把已保存的 Key 覆盖成一串圆点
  if (keyTouched) body.ai.api_key = $('#f-key').value.trim();
  return body;
}

function save() {
  return API.post('/api/config', collect()).then(function (cfg) {
    keyTouched = false;
    fill(cfg);
    return cfg;
  });
}

$('#btn-save').addEventListener('click', function () {
  guard(save(), '保存失败').then(function (r) { if (r) toast('已保存', 'ok'); });
});

$('#btn-test').addEventListener('click', function () {
  var out = $('#test-result');
  out.textContent = '正在连接…';
  out.style.color = '#6b7785';
  save().then(function () {
    return API.post('/api/ai/test');
  }).then(function (r) {
    out.textContent = '✓ 连接正常，模型 ' + r.model + ' 回了：' + r.reply;
    out.style.color = '#16a34a';
  }).catch(function (err) {
    out.textContent = '✗ ' + err.message;
    out.style.color = '#dc2626';
  });
});

API.get('/api/config').then(fill).catch(function (err) {
  toast('读取设置失败：' + err.message, 'err');
});
