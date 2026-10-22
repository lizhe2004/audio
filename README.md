# 音效试听库 (Sound Effects Library)

纯静态音效试听库：单文件 `index.html` + `sounds/` 音频资源，无构建步骤，可直接双击打开或用任意静态服务器托管。

- 378 个音频（meme / UI / 动效 / 动漫 / 游戏 / 打斗 / 武器 / 和风 / 测试音频）
- 分类筛选、关键词搜索（含 AI 复核内容）、时长/大小排序、收藏、独奏/叠音播放模式、循环、倍速
- 悬停音效卡片可查看 Qwen3.8-Omni 的纯音频复核结果（可听内容、可能声源、适用场景、不确定性）

## 本地运行

```bash
# 直接双击 index.html 也可（file:// 下频谱可视化自动降级为程序动画）
python3 -m http.server 8000
# 打开 http://localhost:8000
```

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `index.html` | 页面本体，内嵌 57 条精编元数据 `SOUNDS_DATA` |
| `sound_analysis_data.js` | 全部 378 个音频的 AI 复核数据（运行时与精编元数据合并） |
| `sounds_meta.json` | 精编元数据的 JSON 副本（与 `index.html` 内嵌数据保持同步） |
| `sounds/` | 音频文件（根目录精编音效 + 5 个分类子目录 + samples） |
| `reanalyze_qwen_omni.py` | AI 复核脚本（DashScope Qwen3.8-Omni，需 `DASHSCOPE_API_KEY`） |
| `audio_analysis*.md` / `Qwen-Omni.md` | 全量音频分析报告 |
