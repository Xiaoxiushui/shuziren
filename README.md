# 数字纪念形象

代码仓库：[Xiaoxiushui/shuziren](https://github.com/Xiaoxiushui/shuziren)，主分支为 `main`。换设备开发见 [跨设备开发](docs/跨设备开发.md)。

一个可在手机浏览器打开的数字纪念 H5：扫码进入人物页面，查看简介，和基于已审核资料生成的纪念形象进行文字问答，并可以把回答读出来。

2026-11-08 语音修复：定位到临时 Token 无效，负责人更新本机配置后，真实 ASR/TTS 及新 HTTPS 公网入口联调通过。已补录音可见提示、播放被拦截后的点击重试、扫码口令传递和配置自动重读。Python 37 项、Node 35 项及两份人物配置检查通过；负责人已确认微信真机录音和朗读均正常，本次故障关闭。当前恢复入口的二维码在 `out/voice-repair/`，详见 [语音故障修复](docs/语音故障修复.md)。已授权真实人物试点、声音复刻、口型驱动和固定域名部署仍未完成。**完整目标见 [接手梳理](docs/接手梳理.md)**；运行说明见 [交接说明](docs/交接说明.md)，长期蓝图见 [量产架构](docs/量产架构.md)，历史证据见 [验收记录](docs/验收记录.md)。

## 这一版能做什么

- 手机 H5 页面，扫码或输入链接即可打开
- 人物列表，可切换林叔、周姨两位演示人物
- 建议问题、自由提问、重新开始
- 支持多轮对话，前端保留最近 8 条上下文
- 浏览器语音朗读回答，可点击停止
- 服务异常时自动回退到审核过的演示回答
- 请求超时会结束等待；人物列表或资料加载失败可直接重试
- 人物资料加载完成前禁止提问，指定人物服务故障时不会改用其他人物
- 重新开始会清空消息、草稿和模型上下文，并取消仍在等待的请求
- 每个人物一个独立 JSON 素材包，改资料不用改代码
- 新人物支持草稿、逐条审核、版本校验、发布归档和单客户打包，见 [人物配置流程](docs/人物配置流程.md)
- 电脑和手机在同一 Wi-Fi 下即可访问，不需要公网服务器
- 对外只开放 `web/` 页面，人物资料、`.env` 密钥和文档不会被直接下载

## 环境要求

- Windows 电脑，已安装 Python 3
- 手机和电脑连接同一个 Wi-Fi
- 可选：`py -3 -m pip install "qrcode[pil]"`（用于生成二维码）

## 启动

双击根目录的 `一键启动.cmd`，会自动打开人物列表，并生成当前网络的手机扫码图片。交付 ZIP 中也包含此文件。

在 PowerShell 中执行：

```powershell
Set-Location "C:\Users\27106\Documents\ChatGPT\虚拟数字人"
.\scripts\serve.ps1
```

启动后窗口会显示两个地址：

- 电脑访问：`http://localhost:4174/`（会自动进入人物列表）
- 手机访问：`http://电脑的局域网IP:4174/web/people.html`

手机直接打开第二个地址即可。如果打不开，依次检查：手机是否连着同一个 Wi-Fi、路由器是否开启了“无线设备隔离 / AP 隔离”、Windows 防火墙弹窗是否选择了“允许访问”。

## 页面

- `/`：自动跳转到人物列表
- `/web/people.html`：人物列表
- `/web/?profile=another-demo`：周姨
- `/web/?profile=lin-shu-demo`：林叔

## 生成专属链接和二维码

```powershell
py -3 .\scripts\generate_links.py --profile another-demo --base-url http://192.168.71.38:4174/web/ --out out
```

把 `--base-url` 换成实际的局域网地址或部署后的公网地址。二维码会输出到 `out\` 目录。

## 接入真实模型（可选）

默认是演示模式，回答来自 `profiles/` 里人工审核过的内容。要接入兼容 OpenAI 接口的模型：

1. 复制 `server/.env.example` 为 `server/.env`
2. 填写 `MODEL_API_BASE_URL`、`MODEL_API_KEY`、`MODEL_API_NAME`
3. 重启服务，`/api/health` 返回 `"mode": "live"` 表示配置齐全；再实际提问，`/api/chat` 返回 `"mode": "live"`、`"fallback": false` 才表示本次真实模型调用成功

密钥只在服务端读取，不会发送到浏览器。模型调用失败时会自动回退到审核过的回答。

## 打包交付

```powershell
py -3 .\scripts\package_demo.py --out out\digital-memorial-demo.zip
```

交付包包含一键启动文件、环境变量空白示例和回归检查；排除 `.env`、`.env.local`、`.env.production` 等本地环境配置及历史输出。

默认仅打包两位虚构演示人物。单客户交付使用 `--profile 人物ID`，只包含该人物及运行所需私有审核侧文件。新增人物必须先通过 `scripts/manage_profile.py` 的草稿审核与发布流程，不能只复制 JSON 后直接交付。

## 开发自检

跨设备开发、GitHub 拉取/提交和下载演示包的方法见 [跨设备开发](docs/跨设备开发.md)。代码仓库不保存本机 `server/.env`、人物草稿和私有审核记录；换设备需单独迁移这些配置。

Python 交付检查会实际打包、解压并启动独立服务，验证页面、问答和文件访问边界：

```powershell
py -3 -m unittest discover -s tests -p "test_*.py" -v
```

交互回归需要本机 Node.js，无需安装 npm 依赖：

```powershell
node --experimental-vm-modules --test tests/*.test.mjs
```

此命令的 VM Modules 提示来自测试运行方式，演示服务本身不需要 Node.js。

## 目录

- `web/`：手机 H5 页面
- `server/`：本地服务和问答接口
- `profiles/`：人物素材包
- `scripts/`：启动、链接、打包、试点记录脚本
- `docs/`：产品说明、接口契约、手机访问、打包说明等文档
- `tests/`：对话重置、请求超时、资料重试与交付包回归检查
- `out/`：生成的链接、二维码和交付压缩包（不提交到 git）

## 边界

这是基于资料生成的 AI 纪念形象，不代表本人真实意识或意志。第一版不做实时数字人脸部驱动，回答以审核资料为准，未知内容不编造。
