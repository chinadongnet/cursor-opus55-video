---
name: make-video
description: 在本仓库中制作带中文配音、字幕的讲解类视频（HyperFrames + Azure Speech 流水线）。当用户要求“做一个视频 / 创建视频 / 生成科普或讲解视频 / 再做一期”时使用。默认按 Opus 5.5 风格：高认知细节、前 10 秒强钩子、成片不超过 5 分钟。配音默认晓晓温柔。
---

# 制作视频（本仓库流水线）

本仓库用“代码生成视频”：Agent 写脚本和 HTML 动画，HyperFrames 在无头 Chrome 里逐帧渲染，ffmpeg 编码为 MP4。不调用任何付费视频生成 API。

所有新视频默认按 **Opus 5.5 风格** 写。用户当次明确改口（加长、换调性、不要钩子）时，以当次要求为准。画面沿用本仓库的纸色底、点阵、衬线大标题和珊瑚 / 薄荷 / 天蓝 / 薰衣四色，不另起一套视觉。

## Opus 5.5 风格

写 `script.json` 之前先想完这三问，再落笔：观众以为自己已经懂的那一句是什么；真正决定结论、却常被省掉的机制是什么；这条机制成立的话，下一步必然是什么。

- **认知**：用 2～4 个重要、但容易被一笔带过的细节当骨架。每一点讲清它为什么成立、大家为什么会漏掉、漏掉之后会怎样。场景之间是一条因果链。少做常识复述，也少把平级建议铺成清单。
- **前 10 秒**：这是全片最用力的一段，目标是让人停下划走。绝对时间 0–10 秒内，旁白和画面一起给出一个具体、可感、略反直觉的事实、对比或问题。旁白约 4.6 字/秒，钩子落在开头约 40～50 字里。1 秒内画面就要动，10 秒内屏幕上必须出现钩子里的那个数字、尺度或矛盾。不要用「大家好」「今天我们来讲」或一张干等的标题卡开场。
- **时长**：成片含 3.6 秒片尾卡，默认不超过 **5 分 00 秒**。正文连同场景间停顿按这个上限倒推，一般不超过约 1100 字。写超了就删场景，不要靠加快语速把片子塞进去。

钩子示例：冰立方那期的第一句是「此刻，每一秒，都有数百亿个中微子，穿过你指甲盖大小的地方」，而不是「今天介绍诺贝尔物理学奖」。

## 目录约定

```
tools/make_video.py      构建：TTS → 时间轴 → 字幕 → BGM → 字体子集 → index.html
tools/new_video.py       新建视频项目脚手架
tools/starter/           新项目的最小模板（template.html + script.json）
videos/<slug>/
  script.json            旁白分镜（唯一需要写的“剧本”）
  source/template.html   画面模板（含占位符，绝不放在项目根目录）
  index.html             构建产物，勿手改
  assets/                vo/、narration.mp3、bgm.mp3、fonts/、gsap.min.js
output/<slug>.mp4        最终成片
```

参考成品：`videos/ai-era-g10-girl/`（12 个场景、约 4 分钟，可直接借鉴版式和动效写法）。

## 工作流程

1. **立意与脚本**：先按上面的 Opus 5.5 三问写完骨架，再确定受众。结构是「10 秒钩子 → 2～4 个被忽略的关键细节 → 一个下一步 → 金句收尾」，每个场景 60～120 字。开场场景要在全片第 10 秒前把钩子演完。成片含片尾卡不超过 5 分钟，正文一般不超过约 1100 字。金句是最后一句旁白；网址不进旁白，留在片尾卡。
2. **脚手架**：`python tools/new_video.py <slug>`（竖屏加 `--portrait`）。
3. **写 `script.json`**：每个场景含 `id`、`text`，以及 `cues`（`名称: 旁白中的原文短语`），构建时会解析成该短语被读出的绝对秒数，用于让画面元素踩点出现。短语必须逐字出现在 `text` 中。配音默认晓晓温柔：`voice` 为 `zh-CN-XiaoxiaoNeural`，`style` 为 `gentle`，`rate` 为 `0.95`，`pitch` 为 `0%`。某个场景要换声音或语调时，在该场景上写 `voice` / `style` / `rate` / `pitch`，缺省就沿用整片。晓晓常用 style：`gentle`、`calm`、`cheerful`、`serious`、`sad`、`lyrical`、`chat`、`newscast`。换到云希 `zh-CN-YunxiNeural`、云扬 `zh-CN-YunyangNeural` 等声音时，不要沿用 `gentle`，改成该声音支持的 style，或把 `style` 设成空字符串。写稿仍按约 4.6 字/秒估算时长；成片偏慢时把 `rate` 调成 `+4%` 或 `1.04`。
4. **写 `source/template.html`**：每个场景一个 `<section class="clip scene" data-start="{{sid.start}}" data-duration="{{sid.dur}}">`；动画写在唯一的 paused GSAP 时间线里，用 `C("s03","math")` 取提示点时间，`T.s03.start/end/vo` 取场景时间。
5. **构建**：`python tools/make_video.py videos/<slug>`（TTS 有缓存，改文案只重配改动的场景；`--force-tts` 强制全部重配）。
6. **检查**：在项目目录运行 `npx -y hyperframes@0.8.143 check --browser-gpu`，错误必须清零；`nested_structure_needs_subcomposition` / `composition_file_too_large` 这类结构建议可忽略。
7. **抽帧目检**：`npx -y hyperframes@0.8.143 snapshot --browser-gpu --no-end --at <每个场景元素出齐的时间>`，逐张查看 `snapshots/*.png`，修溢出、遮挡、孤字换行。
8. **渲染**：`npx -y hyperframes@0.8.143 render --browser-gpu --gpu --quality delivery -o ../../output/<slug>.mp4`。必须走 GPU，见下面「GPU 渲染」。4 分钟 1080p 放后台执行。

## GPU 渲染

本机是 NVIDIA RTX PRO 4500 Blackwell。成片、检查、抽帧都带 `--browser-gpu`，成片再加 `--gpu --quality delivery`。规则和实测数字在仓库根目录 `AGENTS.md`。

- 摘要第二行要含 `hardware gpu`，探测日志要含 `NVIDIA RTX PRO 4500`。出现 `software gpu` 时先恢复驱动再重跑，软件光栅的文件不当成最终成片。
- `--quality delivery` 走 NVENC preset p5、CQ 15（约 5 Mbps 量级），渐变和文字比默认 `looks`（CRF 16、约 1.5 Mbps）更干净，编码时间仍能跟上抓帧。
- 大面积 CSS `filter: blur()` 会关掉 drawelement 快速抓帧（约 2 倍，自检 PSNR 90 dB 以上）。光斑优先用径向渐变。
- HLS 不要加 `--gpu`。本仓库成片不用 `--docker`。

## 必须遵守的坑

- 字体：渲染机没有系统中文字体。只能用 `make_video.py` 子集化的 `Noto Sans SC`(400/700/900)、`Noto Serif SC`(700/900)、`Ma Shan Zheng`(400)；不要写 500/600 等不存在的字重。
- 不要在根目录放第二个带 `data-composition-id` 的 HTML（模板必须在 `source/`）。
- 不要给有 GSAP 位移动画的元素写 CSS `transform`；初始状态写在 `fromTo` 里。
- 不要对 `.clip` 元素做 `visibility/autoAlpha` 动画；淡入淡出作用在内部 `.inner` 上。
- 不用 emoji（渲染机无彩色 emoji 字体），图标用内联 SVG。
- 正文区域保持在 y < 870px，底部留给字幕条。
- 白字底色用较深的主题色（`--coral #e8553f`、`--mint #17977f`、`--sky #3470e0`、`--lav #6b5be6`），黄色底配深色字，否则对比度检查不通过。
- 配音走 Azure Speech（region `eastasia`，输出 `audio-24khz-48kbitrate-mono-mp3`）。密钥放在 `tools/.azure-speech.local.json`（已 gitignore）或环境变量 `SPEECH_KEY` + `SPEECH_REGION`。失败会自动重试。Google Fonts 在本机证书校验失败，字体走 jsDelivr。

## 片尾

每一期成片在最后一幕结束后，固定接一张片尾卡，停留 **3.6 秒**（`script.json` 的 `outro` 更长时，按更长的那段）。`make_video.py` 构建时自动插入，模板里不要再手写第二张，也不要把网址写进旁白。

位置在画面正中，底边让开 8px 进度条。纸色底和点阵与正片同一套，横屏竖屏都居中：

- 小字「更多内容」，字距拉开
- 大字 `www.chinadong.net`（64px，两侧留白，竖屏也不会顶边）
- 底下一条短的珊瑚红线

最后一幕的金句在这张卡出现之前收完。卡片从略低于中线的位置淡入到位。
