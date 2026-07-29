# 试卷批改系统

给一位老师用的**单机版电子试卷批改工具**。导入学生答卷（扫描件/照片/PDF），
在屏幕上逐题打分、用红笔在卷面上圈画写评语，自动算总分、出班级分析、导成绩表，
批注过的卷子可以按人或按全班打包导出发回给学生。批改台自动接到上次没批完的学生；
设置页可一键把整个 `data/` 打包备份。可选接入智谱 GLM-5V，让 AI 对着卷子先批一遍。

> **只批主观题。** 选择题、判断题不进这个系统 —— 老师在系统外已经改好了。
> 想在成绩表里算总分，填一个「客观题得分」合计分即可。
>
> **判分标准是「意思相近就给分」**，不要求学生写得和参考答案字面一致。

- 代码仓：https://github.com/koinoborisuneo387-blip/exam-grading
- 需求与设计的权威文档：[SPEC.md](SPEC.md)
- 改代码前必读：[CLAUDE.md](CLAUDE.md)（与 [AGENTS.md](AGENTS.md) 内容一致）
- 给老师的说明书：[使用说明.md](使用说明.md)

---

## 核心约束：零第三方依赖

老师的机器是 **华为擎云 L540-031**，2026-07-29 实机确认是
**统信 UOS Desktop 20 Pro**（代号 eagle，基于 Debian 10 → 自带 **Python 3.7.3**），
**git 2.20.1 已装**，CPU 架构未确认但可能是 ARM64。
在那台机器上 `pip install` 任何需要编译的包（numpy / Pillow / PyMuPDF / Flask / requests /
openpyxl / reportlab）都会现场编译并大概率失败，远程救不回来。

**所以这个项目只用 Python 标准库，一个 pip 包都不装，也没有 `requirements.txt`。**
前端同理：原生 HTML/CSS/JS，无框架、无构建、不引 CDN（离线要能用）。

自己造的轮子：

| 常规做法 | 这里的做法 |
|---|---|
| PyMuPDF 渲染 PDF | [`server/pdfimg.py`](server/pdfimg.py) 手写 PDF 解析，把扫描件内嵌的图像流原样抠出来 |
| Pillow 读写图片 | [`server/imgutil.py`](server/imgutil.py) 手写 PNG 编码、JPEG/PNG 尺寸解析 |
| openpyxl 导 Excel | [`server/xlsx.py`](server/xlsx.py) zipfile + 手拼 XML |
| reportlab 导 PDF | 浏览器打印（CSS `@page`） |
| requests 调 API | `urllib.request` |
| Flask | `http.server.ThreadingHTTPServer` |
| Pillow 合成批注图 | 前端 Canvas 合成后回传 |
| `cgi.FieldStorage` | [`server/multipart.py`](server/multipart.py)（cgi 在 3.13 已删除） |

**语法基线**：Python **3.7**、JavaScript **ES2017**、保守 CSS。
开发机是 Python 3.12，能跑不代表老师机器能跑 —— 细则见 CLAUDE.md 第 1 节。

这条**由测试守着**：[`tests/test_baseline.py`](tests/test_baseline.py) 用
`ast.parse(feature_version=(3,7))` 真按 3.7 的规则解析全部产品代码，
并扫掉 3.8+/3.9+ 的标准库 API、ES2018+ 的 JS 写法、页面外链、以及任何第三方 import。
越线就红，不用靠记性。

---

## 本地开发

```bash
python app.py
```

| 项 | 值 |
|---|---|
| 本地地址 | http://127.0.0.1:8899 |
| 启动 | `python app.py`，或双击 `启动.bat` |
| 测试 | `python -m unittest discover -s tests -t tests` |
| 数据目录 | `data/`（可用环境变量 `PJPG_DATA_DIR` 挪走） |
| 端口 | 默认 8899，`--port` 或环境变量 `PJPG_PORT` 可改；被占用会自动往后找 |

改完 Python **必须重启进程**（不热加载）；改 `static/` 刷新浏览器即可。
**改了代码就抬 `VERSION`** —— 老师那边全靠页面右上角的版本号确认更新生效没有。

## 目录结构

```
app.py                 启动入口（只做参数解析和版本检查）
server/
  config.py            路径、版本、AI 配置、API_KEY.txt 读取
  db.py                SQLite 建表 / 连接 / 加列迁移
  grading.py           题型定义与分数处理（纯函数）
  analysis.py          总分重算、班级统计、题目/知识点分析、错题清单
  pdfimg.py            从扫描 PDF 抠图（手写 PDF 解析）
  imgutil.py           PNG 编码、图片尺寸、类型嗅探
  xlsx.py              手写 xlsx / CSV 导出
  ai.py                智谱 GLM-5V 等 OpenAI 兼容接口
  multipart.py         文件上传解析
  api.py               路由与处理
  httpd.py             HTTP 服务与静态文件
static/                原生前端（index / exam / students / grade / report / settings）
tests/                 unittest，零依赖
start.sh install.sh update.sh    老师端（Linux）交付脚本
```

## 交付与更新

| 脚本 | 干什么 |
|---|---|
| `install.sh` | 老师机器上跑一次：查 Python、建 `data/` 和 `API_KEY.txt`、放桌面图标 |
| `start.sh` | 启动服务并打开浏览器（桌面图标指向它） |
| `update.sh` | 有 git 就 `git pull`，没有就下 zip 覆盖。**`data/` 一个字节都不碰**，更新前自动备份旧代码 |

`update.sh` 顶部的 `REPO_SLUG` 是代码仓地址，换仓库改那一行。

**zip 更新路线已实测**（2026-07-29）：从 `codeload.github.com` 下载 → 解压 → 按覆盖清单替换，
程序更新到新版本，`data/`（成绩库、答卷原图、`API_KEY.txt`）**逐字节未变**；
三个 `.sh` 在仓库里都是 LF 换行、shebang 完好（CRLF 会让 Linux 报"找不到解释器"）。

**Schema 变更**：只能往 `db.ADDED_COLUMNS` 里加列，`init()` 会在老师的旧库上自动
`ALTER TABLE`。**不许改列型、不许删列** —— 那边库里是真实成绩。

## AI 接入

默认预置**智谱 GLM-5V**（`glm-5v-turbo`），走 OpenAI 兼容的 `/chat/completions`。
选它是因为它**能看图**，可以直接对着扫描的答卷批改。

已用真接口验过：2 页答卷 + 1 页标准答案整卷批改，**耗时约 23 秒**，能准确认出卷面手写字，
学生措辞和参考答案不同也照样给分（"意思相近即得分"的提示词生效）。

⚠️ `glm-5v-turbo` 是**思考型**模型，返回里除了 `content` 还有 `reasoning_content`，
光推理就要烧一百多个 token。**不要给它设 `max_tokens`** —— 设小了推理会把额度吃光，
`content` 直接是空字符串。也因为要思考，它比普通模型慢，超时默认 180 秒。

三处用到 AI，**全部只产出草稿，都要老师点头才落库**：

| 功能 | 接口 | 产出 | 落库条件 |
|---|---|---|---|
| 整卷预批 | `/api/ai/grade_paper` | 每题建议分 + 评语 | 逐题点「采纳」 |
| 单题批改 | `/api/ai/suggest` | 一题的建议分 | 点「采纳」 |
| **读卷面姓名** | `/api/exams/<id>/stage/identify` | 姓名、学号、分页归组 | 「确认姓名」界面点确认 |

读姓名那条实测 2 页 7.8 秒，能准确读出「张三 / 01」，且**没有姓名栏的页会如实返回
`has_header=false` 而不是编一个名字**。匹配逻辑在 [`server/roster.py`](server/roster.py)：
学号 > 姓名精确 > 编辑距离 1（**仅限 3 字以上姓名**）。
两字姓名不做模糊匹配 —— 「张三」和「张山」距离也是 1，猜错就把分记到别人头上了。

- 老师只需把 API Key 粘进 `data/API_KEY.txt` 保存 —— 读到 key 就自动启用，不用点开关
- 换通义千问 VL / DeepSeek 只改设置页配置，不改代码（预设见 `config.AI_PRESETS`）
- 纯文本模型（DeepSeek）看不了卷子图，界面会明确提示要粘学生作答文字
- **AI 只给建议分**，写进 `ai_suggested_score`；老师点「采纳」才进 `score.score`
- Key 存 `data/config.json` 或 `data/API_KEY.txt`，两者都在 `.gitignore` 里

## 已知限制

- PDF 只支持**扫描件**（DCTDecode / FlateDecode）。CCITTFax、JBIG2、JPEG2000、
  CMYK 会明确报错并教老师怎么办，不静默失败
- 电脑生成的文字版 PDF 读不了（页面里没有整页图片），会提示改用拍照
- 每个 PDF 页只取面积最大的那张图（扫描件就是一页一图）
- v1 不做 OMR 答题卡识别、不做手写 OCR、不做学生端、不做多老师协作
