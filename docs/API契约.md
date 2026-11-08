# P1 API 契约

默认服务地址为当前页面同源地址。所有 JSON 请求使用 `Content-Type: application/json`。

## 静态页面

- `GET /` 返回 302，跳转到 `/web/people.html`。
- 只有 `/web/` 下的页面、样式和脚本会被直接提供。
- `/server/`、`/profiles/`、`/docs/` 以及项目根目录下的其他文件一律返回 404，避免密钥和人物资料被直接下载。

## `GET /api/health`

```json
{
  "ok": true,
  "mode": "demo",
  "service": "digital-memorial"
}
```

`mode` 只有在 `MODEL_API_BASE_URL`、`MODEL_API_KEY`、`MODEL_API_NAME` 三个环境变量均非空时才为 `live`，否则为 `demo`。此接口只表示配置状态，不探测供应商是否可达，也不验证密钥；真实调用结果以 `/api/chat` 的 `mode` 和 `fallback` 为准。

## `GET /api/profiles`

返回人物列表，供人物选择页使用：

```json
{
  "ok": true,
  "profiles": [
    { "id": "another-demo", "name": "周姨", "intro": "喜欢种茉莉花……" },
    { "id": "lin-shu-demo", "name": "林叔", "intro": "喜欢修理旧收音机……" }
  ]
}
```

## `GET /api/profile?id=lin-shu-demo`

返回已校验的公开人物素材包，含 `schemaVersion: 1` 和 `kind: "fictional" | "real"`。`id` 为 1–48 位小写字母、数字和短横线。审核人、授权记录和逐条素材引用不在公开返回或模型上下文中。

非法 `id` 返回 `400 invalid_profile_id`，不存在的人物返回 `404 profile_not_found`。

资料结构错误、缺少审核记录、审核未通过或摘要失配，返回 `503 profile_unavailable`。人物列表省略这些不可用项。`/api/chat` 和 `/api/tts` 同样验证当前人物并返回此错误，不向访问者返回私有审核诊断。

页面在非法链接或 `404` 时会明确提示切换至默认演示人物；超时、`5xx` 或资料格式错误时保留失败状态并提供重试，禁止用另一个人物代答。

## `POST /api/chat`

请求：

```json
{
  "profileId": "lin-shu-demo",
  "message": "你最常说的一句话是什么？",
  "history": []
}
```

返回：

```json
{
  "ok": true,
  "reply": "我最常说的是……",
  "mode": "demo",
  "source": "approved_profile",
  "fallback": false
}
```

`mode` 可为 `demo` 或 `live`。`fallback: true` 表示服务端没有得到真实模型结果，使用了审核过的演示回答。

请求体必须是 JSON 对象；空消息返回 `400 empty_message`，非法人物 ID 返回 `400 invalid_profile_id`，不存在的人物返回 `404 profile_not_found`。

`message` 最长 500 个字符，超出返回 `400 message_too_long`。

`history` 可选，用于多轮对话。服务端只接受 `user` 和 `assistant` 两种角色，忽略其他字段，每条内容截断到 500 字符，最多保留最近 8 条。真实模型模式下，历史会随本轮问题一起发送；演示模式下历史不影响审核回答。

页面点击“重新开始”会清空历史与未发送草稿、停止语音并取消当前请求。取消前的迟到回复不会加入新对话；取消浏览器请求不保证供应商已经停止处理。

页面请求超时：健康检查 4 秒，人物资料与列表各 8 秒，对话 25 秒，录音上传与语音合成各 15 秒。超时包括读取 JSON 响应。虚构人物对话网络失败时可以使用已加载的审核回答；真实人物网络失败，以及任意人物收到 404/503 时停止缓存回答，清空人物并禁用问答，显示“重新加载资料”。服务端人物审核有效但模型失败时仍返回审核回答。真实模型每次请求超时 6 秒，最多尝试 2 次。

## `POST /api/tts`

请求头 `Content-Type: application/json`，请求：

```json
{
  "profileId": "lin-shu-demo",
  "text": "回来啦。慢慢说，我在这儿听着。"
}
```

是否走真实合成只取决于服务端是否配置了 `NLS_APPKEY` 与 `NLS_TOKEN`，前端不需要知道开关状态。

- 已配置且合成成功：直接返回 `200`，`Content-Type: audio/wav`，响应体是 16kHz WAV 音频字节。文本超过 300 字（阿里云单次上限）时只合成前 300 字，并在响应头返回 `X-TTS-Truncated: 1`，本轮不做音频拼接。
- 未配置、超时（服务端 12 秒）、供应商报错或返回非音频内容：回退为原来的浏览器朗读模式，状态码仍为 `200`：

```json
{ "ok": true, "mode": "browser", "source": "browser_speech", "fallback": true }
```

前端收到 `audio/wav` 时用 `Audio` 元素播放，播放中按钮显示“停止播放”并可随时停止；收到上面的 JSON 时退回浏览器 `SpeechSynthesis`。两种方式结束后按钮都恢复为“播放这句话”。

空文本返回 `400 empty_text`，文本超过 500 字符返回 `400 text_too_long`，非法人物 ID 返回 `400 invalid_profile_id`，不存在的人物返回 `404 profile_not_found`，审核失效返回 `503 profile_unavailable`。这些错误码与接入语音之前一致。

## `POST /api/asr`

请求头 `Content-Type: audio/wav`，请求体是原始音频字节，不是 JSON：16kHz、单声道、16bit PCM 的标准 WAV。

```
POST /api/asr
Content-Type: audio/wav

<44 字节 WAV 头 + PCM 数据的二进制>
```

成功返回：

```json
{ "ok": true, "text": "北京的天气。" }
```

识别为空（有声音但没听出内容）同样返回 `200` 和 `{"ok": true, "text": ""}`，由前端提示用户重说。

错误：

| 状态码 | `error` | 触发条件 |
| --- | --- | --- |
| 400 | `empty_audio` | 请求体长度为 0 |
| 400 | `audio_too_large` | 请求体超过 5MB |
| 400 | `unsupported_audio_type` | `Content-Type` 不是 `audio/wav`、`audio/x-wav` 或 `audio/wave` |
| 503 | `asr_unavailable` | 未配置 `NLS_APPKEY`/`NLS_TOKEN`，或供应商超时（服务端 8 秒）、报错、返回非成功状态 |

服务端把音频转发到阿里云“一句话识别”：`POST {NLS_ASR_URL}?appkey=...&format=wav&sample_rate=16000`，请求头 `X-NLS-Token`，请求体为原始 WAV 字节；只接受供应商返回 `status: 20000000` 的结果。`NLS_TOKEN` 只出现在转发请求里，不会进入日志、错误响应或页面。

前端行为：麦克风录音最长 60 秒自动结束；权限被拒绝、没有麦克风设备、设备被占用、等不到授权（15 秒）都会在状态栏给出中文提示并恢复按钮；录音中可点“取消录音”直接丢弃。识别结果只填进输入框，不自动发送。

## 语音相关配置

`server/.env` 支持三个可选键，留空即回退，不影响文字对话：

- `NLS_APPKEY`：智能语音交互项目的 Appkey（阿里云控制台创建项目后获得）。
- `NLS_TOKEN`：访问令牌，有效期有限，过期后需要重新获取并更新。
- `NLS_TTS_VOICE`：合成发音人，留空使用 `xiaoyun`。
