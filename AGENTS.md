# 本仓库视频规则

讲解类视频按 `.cursor/skills/make-video/SKILL.md` 制作，内容默认走 Opus 5.5 风格（高认知细节、前 10 秒钩子、成片不超过 5 分钟），细则在 `.cursor/rules/opus55-video-style.mdc`。成片渲染使用本机 NVIDIA GPU。

## 机器与结论

本机显卡是 NVIDIA RTX PRO 4500 Blackwell（约 32 GB）。HyperFrames 锁定 `0.8.143`。以前的成片命令没有 `--gpu`，编码停在 CPU 的 libx264，画质停在默认 `looks`（CRF 16，约 1.5 Mbps）。Chrome 的 GPU 模式默认是 auto，探测失败时会落到 SwiftShader 软件光栅。

同一段 6 秒、1080p、30 fps 的画面实测：

| 路径 | 抓帧 | 总耗时 | 视频码率 |
| --- | --- | --- | --- |
| `--no-browser-gpu` + `looks`（SwiftShader） | screenshot 14.1s | 47.1s（含首次编译） | 1.3 Mbps |
| 默认 auto + `looks`（已探到硬件 GPU，画面带 CSS blur） | screenshot 12.4s | 14.3s | 1.5 Mbps |
| `--browser-gpu`，画面没有 `filter: blur()` | drawelement 6.0s，自检 PSNR 90–95 dB | 8.5s | — |
| `--browser-gpu --gpu --quality delivery` | 硬件 GPU + NVENC preset p5 / CQ 15 | 15.3s | 4.9 Mbps |

硬件抓帧和软件抓帧的 SSIM 约 0.997。把成片档升到 `delivery` 后，NVENC 把码率提到约 4.9 Mbps，渐变和文字更干净，耗时仍和 `looks` 的 CPU 编码同一量级。画面没有大面积 `filter: blur()` 时，硬件快速抓帧大约比软件截图快一倍，自检 PSNR 在 90 dB 以上，画质与截图一致。

`delivery` 在这条管线里对应 NVENC 的 preset `p5` 和 CQ 15（CPU 路径则是 libx264 `slow`、CRF 15）。HLS 不接受 `--gpu`。`--docker` 会强制软件 GL，本仓库成片不走 docker。

## 成片命令

在 `videos/<slug>` 目录执行：

```bash
npx -y hyperframes@0.8.143 render --browser-gpu --gpu --quality delivery -o ../../output/<slug>.mp4
```

- `--browser-gpu`：Chrome 用 RTX 的 D3D11 合成。日志里应出现 `ANGLE (NVIDIA, NVIDIA RTX PRO 4500 Blackwell`，摘要第二行应含 `hardware gpu`。
- `--gpu`：ffmpeg 走 NVENC。和 `--quality delivery` 一起用，成片是 p5 / CQ 15。
- 摘要里如果是 `software gpu`，或探测结果没有这块 RTX，先恢复显卡驱动再重跑。软件光栅的文件不当成最终成片。

检查和抽帧同样带上 `--browser-gpu`，让目检和成片走同一条合成路径：

```bash
npx -y hyperframes@0.8.143 check --browser-gpu
npx -y hyperframes@0.8.143 snapshot --browser-gpu --no-end --at <时间点>
```

`filter: blur()` 会关掉 drawelement 快速抓帧，日志会写 `falling back to screenshot`。光斑能用径向渐变表达时优先用渐变，把 blur 留在少数小元素上，抓帧才能走到约 2 倍的硬件快速路径。
