<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PySide6-6.11-green?logo=qt&logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/Selenium-4.x-brightgreen?logo=selenium&logoColor=white" alt="Selenium">
  <img src="https://img.shields.io/badge/ddddocr-1.6-red" alt="ddddocr">
  <img src="https://img.shields.io/badge/DeepSeek-V4--Flash-purple?logo=openai&logoColor=white" alt="DeepSeek">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License">
</p>

<h1 align="center">🎓 RPA · 党课自动学习助手</h1>

<p align="center">
  <i>Red Party Assistant</i>
</p>

<p align="center">
  <sub>基于 ddddocr 验证码识别 · Selenium 浏览器自动化 · DeepSeek V4-Flash AI 答题</sub>
</p>

---

## 📌 命名说明

`RPA` 的命名灵感来自 `Red Party Assistant`。

它巧妙借用了业界常见的 `Robotic Process Automation` 缩写，在保留“自动化助手”语义的同时，赋予项目“红色党务助手”的新含义，是这个项目最核心的品牌双关。

---

## 📸 功能概览

| 功能 | 描述 |
|-------------|-----------------|
| 🔐 自动登录 | ddddocr 识别验证码，SHA1 密码加密，PySide6 现代化 GUI |
| 📺 视频自动播放 | 遍历必修课程，静音播放所有视频片段，自动跳过已完成内容 |
| ⏸️ 卡死恢复 | 检测页面无响应，自动刷新重试；loop_pause 弹窗自动续播 |
| 📊 进度可视化 | 课程/视频/播放/解题 四行渐变色进度条，实时倒计时 |
| 🤖 AI 自动答题 | DeepSeek V4-Flash 解答单选/多选/判断题，断点续答 |
| 🔄 两阶段检测 | 先检测视频完成状态跳过已学课程，再检测自测状态跳过已通过课程 |

---

## 🚀 快速开始

```bash
# 1. 克隆项目
git clone https://github.com/FLmhp/RPA.git
cd RPA

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行主程序
python main.py
```

> 💡 首次运行需要 Chrome 浏览器和 chromedriver 已安装。

---

## 🔐 本地配置与隐私

- 项目不再内置任何默认 `DeepSeek API Key`、用户名、密码。
- 首次运行时，用户需要在 GUI 中手动输入：
  - 用户名
  - 密码
  - `DeepSeek API Key`
- 勾选“保存本地配置”后，程序会将信息写入本地 `settings.json`。
- `settings.json` 已加入 `.gitignore`，不会被提交到仓库。
- 仓库提供 `settings.example.json` 作为示例模板，便于查看配置结构。

---

## 📁 项目结构

```text
├── main.py                # 主程序：PySide6 GUI + Selenium 自动化
├── test_exam.py           # AI 答题测试脚本（读取本地配置）
├── settings_store.py      # 本地配置读写
├── settings.example.json  # 本地配置模板
├── discover.py            # 页面与接口探针
├── requirements.txt       # 依赖清单
├── colours.xml            # 配色参考
└── .gitignore
```

---

## 🔧 核心配置

本地 `settings.json` 结构如下：

```json
{
  "username": "",
  "password": "",
  "deepseek_key": "",
  "remember": false
}
```

说明：

| 字段 | 说明 |
|------|------|
| `username` | 平台登录用户名 |
| `password` | 平台登录密码 |
| `deepseek_key` | DeepSeek API Key |
| `remember` | 是否保存本地配置 |

---

## 🧠 AI 答题引擎

项目通过 DeepSeek V4-Flash API 自动解答党课自测题目：

| 题型 | 数量 | 分值 |
|-----------|-----------|-----------|
| 单选题 | 10 | 50 |
| 多选题 | 5 | 25 |
| 判断题 | 5 | 25 |

- **API**: `https://api.deepseek.com/chat/completions`
- **模型**: `deepseek-v4-flash`（temperature=0，thinking=disabled）
- **断点续答**: 自动检测 `.done` 已作答题，从第一个未答题目继续

```bash
# 独立测试 AI 答题
python test_exam.py
```

---

## 🎨 界面预览

- 深色主题（红汞红配色方案）
- Windows 亚克力模糊效果
- 四行渐变色进度条（课程 / 视频 / 播放 / 解题）
- 实时日志输出 + 按钮弹性动画

---

## 📋 运行流程

```text
登录 (ddddocr OCR)
  │
  ├─ Phase 1: _video_phase()
  │   ├─ 遍历课程列表 → _check_course_status()
  │   ├─ has_label (已通过) ──→ 跳过
  │   ├─ videos_done (必修完成) ──→ 跳过
  │   └─ 未完成 → _learn_videos_for_course()
  │
  ├─ Phase 2: _exam_phase()
  │   ├─ 遍历课程列表 → _check_course_status()
  │   ├─ has_label ──→ 跳过
  │   ├─ exam_clickable=False ──→ 回退 Phase 1 (≤3次)
  │   └─ exam_clickable=True → _solve_exam() [DeepSeek AI]
  │
  └─ ✅ 全部完成
```

---

## 🛠️ 依赖

```text
ddddocr>=1.6.0          # 验证码识别
selenium>=4.15.0         # 浏览器自动化
PySide6>=6.5.0           # GUI 框架
requests>=2.28.0         # HTTP 客户端
beautifulsoup4>=4.12.0   # HTML 解析
Pillow>=10.0.0           # 图像处理
```

---

## 📄 安全说明

- 本仓库默认不包含任何真实账号、密码或 API Key。
- 请勿提交本地 `settings.json`。
- 如果历史版本曾使用过真实凭据，建议对应凭据已经轮换后再公开分享仓库。

---

## 📄 许可

MIT © 2025

---

<p align="center">
  <sub>RPA = Red Party Assistant</sub>
</p>
