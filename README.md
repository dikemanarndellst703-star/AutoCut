# AutoCut · 本地视频剪辑工作台

把内部课程视频拆成「知 / 行 / 无关」，通过文字定位画面，按保留标记导出成片。转写可使用本地 Faster Whisper，也可使用阿里云百炼 DashScope `paraformer-v2` 批量并行处理。

## 已实现

- 淡蓝天空与缓慢白云的日间模式、星光闪烁和偶发流星的夜间模式；不含太阳或月亮，顶部一键切换，主题自动保存。背景不拦截操作；系统启用减少动态效果时自动停止动画。

- 本地素材目录、多个视频导入、统一素材列表与文件名搜索。
- **开始分析 / 批量分析**：本地 Faster Whisper 支持 1–3 个并行任务；阿里云百炼 `paraformer-v2` 支持 1–50 个并行云端任务。两种方式均提供进度、取消、重试和独立视频导出队列。
- 视频或纯音频均可分析。云端结果额外提供统一 `transcription.json`，时间戳保留毫秒精度。
- **完整时间轴**：未识别文字的区间也分段，不将其等同于静音或无用内容；知与行默认保留，无关默认排除；无文字片段沿用前段分类并默认保留，独立审查列表支持预览、剪掉及恢复。
- 中央文字审阅区占主工作区 80% 宽度，全界面默认使用简体中文宋体，可选择宋体、黑体、微软雅黑、楷体、仿宋；字体及 14–32px 字号设置自动保存（使用本机字体，未安装时自动回退到相近的简体中文字体），文字换行、片段高度与间距随字号自适应；素材库改为顶部抽屉；点击文字跳转到片段；右侧播放器、倍速、循环或单段播放；支持全部、知、行、无关、待分类筛选。
- 人工分类、修改文字、调整相邻分段边界、在播放位置拆分、按精确起止时间增加遗漏片段、勾选保留、撤销本次会话的编辑。
- 自动保存本机工程；下载 / 恢复工程 JSON；直接导出完整逐字稿 SRT、成片字幕 SRT 或纯文字稿 TXT；多选时自动打包 ZIP。
- 按原片顺序输出全部保留片段、仅「知」或仅「行」；批量导出时每个源视频分别生成 MP4、成片字幕、原片字幕、文字稿、剪辑清单和工程。

## 安装与启动

需要 **Python 3.10–3.12** 和 **FFmpeg（含 FFprobe）**。已在 macOS Apple Silicon / Python 3.10 上验证。其他平台提供启动脚本，尚未实机验证。

1. 安装 Python 和 FFmpeg，并确认 `python`（或 `python3`）、`ffmpeg`、`ffprobe` 可在终端使用。
2. 下载本仓库，进入项目目录。
3. macOS / Linux 运行：

```sh
bash start.sh --media-dir "/你的素材文件夹"
```

Windows 在 PowerShell 中运行：

```powershell
.\start.ps1 --media-dir "D:\课程视频"
```

脚本首次运行创建 `.venv` 并安装依赖。打开 [本机工作台](http://127.0.0.1:8765)。不指定 `--media-dir` 时从空素材库开始，点击「导入视频」即可。大文件可直接指定目录，避免额外复制。

也可手动安装：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py --media-dir "/你的素材文件夹"
```

Windows 的虚拟环境解释器是 `.venv\Scripts\python.exe`。可用 `--port 8766` 改端口，`--data-dir /path/to/data` 指定数据存储位置。服务仅监听 `127.0.0.1`，不面向公网部署。

## 阿里云百炼长音频转写

复制 `.env.example` 为项目根目录的 `.env`，填入自己的 DashScope 与 OSS 配置。`start.sh` 和 `start.ps1` 启动时会自动加载它；`.env` 已被 Git 忽略，请勿改动忽略规则或把凭证粘贴到网页、源码、日志和提交记录中。

```text
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_ASR_MODEL=paraformer-v2
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_ENDPOINT=
OSS_BUCKET=
DASHSCOPE_ASR_POLL_INTERVAL=3
DASHSCOPE_ASR_TIMEOUT=600
```

`DASHSCOPE_ASR_MODEL` 必须为 `paraformer-v2`。轮询间隔与超时可省略，默认分别为 3 秒和 600 秒。程序把每个本地媒体上传到配置的 OSS Bucket，生成 72 小时签名读取地址，异步提交 DashScope，任务结束后尽力删除临时 OSS 对象。批量任务相互独立；并发上限可在“分析设置”中设为 1–50，实际吞吐还受阿里云账号配额限制。

选择“分析设置 → 阿里云百炼”，界面只会显示“已配置/未配置”和缺失变量名，不会读取或展示凭证值。请求固定使用中文提示、数字文本归一化、标点，并关闭说话人识别。成功后统一 JSON 为：

```json
{
  "video": "课程.mp4",
  "duration": 12.345,
  "language": "zh",
  "model": "paraformer-v2",
  "segments": [
    {"start": 0.000, "end": 2.500, "text": "示例文字。"}
  ]
}
```

代码按 `begin_time / 1000.0` 和 `end_time / 1000.0` 转换毫秒，不会转成整数。缺失环境变量、OSS 上传或签名失败、DashScope HTTP 错误、任务失败、超时、结果缺失和空字幕都会在对应任务中显示明确原因。

不泄露密钥的最小验证方式（先把私有配置写入 `.env`）：

```sh
set -a; source .env; set +a
.venv/bin/python verify_dashscope.py "/path/to/media.mp4" -o transcription.json
```

验证脚本只打印阶段、百分比、字幕段数和首段时间，不打印 Key 或签名 URL；它会检查顶层字段、片段字段、固定模型、中文语言和毫秒时间网格。

## 使用

1. 选择媒体，点击 **开始分析**。「分析设置」可切换本地 Faster Whisper 或阿里云百炼。默认本地模型为 Whisper large-v3，也可切换 Tiny、Base、Small、Medium。下拉框显示每个本地模型的实际下载状态，选中后可一键提前下载，查看已下载 / 总字节数和每 1% 的真实进度、取消或重试；关闭窗口下载仍在后台继续。设置自动保存，对下一次单个分析、批量分析及重试生效，不影响正在运行的任务与已有结果。识别结果仍须复核。
2. 在片段列表审阅转写，点击文字播放对应画面。蓝色为「知」，绿色为「行」，灰色为「无关」。无文字片段默认沿用前段分类并保留；开头没有前段时暂归为知。点击“无文字片段审查”单独查看时间范围、时长和保留状态，可预览、剪掉或恢复。
3. 修正分类和文字。自动分析后知与行勾选保留，无关不勾选；直接修改勾选即可，无需逐条标记复核。筛选只影响列表显示；「移除当前筛选」会将当前显示的片段设为不保留，可撤销。
4. 右侧以秒调整边界：同时更新前后相邻片段，确保时间轴没有缺口。点击「在播放处拆分」将当前片段拆为两段。边界 / 拆分后的文字按已有词级时间戳分配，需复核。手动编辑后的整段文字没有词级对齐，拆分时文字归入其中一段，需手动校正。
普通播放会跳过未勾选片段；点击“从头预览成片”查看保留效果。“查看原片段”可单独检查被排除的内容。

5. 点击 **导出…**，可直接下载完整逐字稿 SRT、成片字幕 SRT 或纯文字稿 TXT；多选素材自动打包 ZIP。选择剪辑视频 MP4 时，任务完成后在右侧队列下载。保留状态优先：即使分类为「行」，未保留的片段也不参与“仅行”导出。

重新分析会备份旧工程并替换时间轴及人工编辑；备份在 `data/backups/`，可通过「恢复工程」恢复。分析期间暂停对该视频的编辑；导出使用开始导出时的工程快照。多个窗口同时修改发生版本冲突时会拒绝覆盖，请重新选择视频载入最新工程。

## 分类方法和限制

分类使用本地关键词规则以及开头 / 结尾位置，而非语义大模型。原因说明优先为「知」；行动或读秒词语建议为「行」；片头片尾或推广用语建议为「无关」。短句、混合讲解和动作、同音词、口音可能误判，不能保证全自动准确剪辑。

无文字区间目前按最长 30 秒分段，未做视觉动作识别 / 镜头检测；可能是跟练、音乐、静音或语音漏识别。无音轨视频也能分段、手动剪辑。浏览器预览受视频编码支持影响，H.264/AAC MP4 兼容性较好。

导出重编码为 H.264/AAC，以支持非关键帧位置切割；耗时与素材时长、分辨率、硬件有关。切点受视频帧和音频采样粒度限制，成片时长可能有小幅偏差，真实时长记录在 `cuts.json` 中。成片 SRT 使用剪辑时间轴，`transcript-original.srt` 使用原始时间轴。

## 本地文件与开源

```text
server.py                HTTP 服务
workbench.py             工程持久化、队列、转写及剪辑
dashscope_asr.py         OSS 上传、DashScope 异步轮询与统一字幕转换
core.py                  分类、时间轴和字幕映射
transcribe_worker.py     可取消的独立转写进程
verify_dashscope.py      不输出凭证的云端连通性与结果结构验证
model_downloads.py       本地模型检测与后台下载
download_progress.py     官方文件清单与准确字节进度
web/                     原生 HTML / CSS / JavaScript 界面
requirements.txt         固定版本的转写依赖
requirements-lock.txt    本机验证环境的完整版本记录（跨平台优先 requirements.txt）
.env.example             云端配置变量模板（不含凭证）
tests/                   合成媒体与时间轴测试

data/                    私有运行数据，不应提交
  uploads/               通过页面导入的视频副本
  models/                下载的模型缓存
  projects/              工程与转写文字
  backups/               重分析前的工程备份
  exports/               每次导出的独立结果
  jobs.json              任务历史
```

**只上传本程序目录**，不要上传包含公司素材的父目录。`.gitignore` 已忽略运行数据、视频、音频、字幕、模型和虚拟环境。测试使用运行时生成的合成视频，不包含公司素材。程序使用 MIT 许可证；媒体版权和模型 / 第三方依赖遵循各自许可。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node --check web/app.js
```

测试覆盖完整时间轴、无语音、重叠转写、解释与行动规则、字幕时间映射、工程持久化、版本冲突、实际 FFmpeg 导出、DashScope 请求参数、毫秒转换、OSS 清理和云端并发。可运行 `tests/check_service.py` 检查已启动的本地媒体服务。

转写 API 参考：[Faster Whisper 官方文档](https://github.com/SYSTRAN/faster-whisper)、[阿里云百炼 Paraformer REST API](https://help.aliyun.com/zh/model-studio/paraformer-recorded-speech-recognition-restful-api)。


## 文字导出与并行处理更新

顶部“导出…”支持完整逐字稿 SRT（原片时间、包含未保留内容）、成片字幕 SRT（保留内容、成片时间）和纯文字稿 TXT，无需视频编码。批量多选下载 ZIP；未分析或正在重分析的素材会明确报错，不会静默漏导。文字采用已保存的工程快照，无对白段不生成空字幕。

分析设置支持同时分析 1–3 个视频，默认 2 个；设置保存在本机 data/settings.json。视频编码单独执行一个任务。调低并发不取消在途任务；大模型并发可能增加内存压力，速度需以本机实际测试为准。每个转写进程采用最多 CPU 核心数的四分之一（1–8 线程），为其他任务留出资源。
