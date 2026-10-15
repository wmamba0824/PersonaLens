# PersonaLens｜人物画像提取与风格问答

从创作者的历史内容提炼有依据的 AI 人物画像，再结合本地知识库进行有出处的风格问答。

这是我的第一版 Vibe Coding 作品，也是可独立运行的模板。**AI 模拟，不是本人；公开内容不等于获得使用或传播授权。**

## 当前能做什么

- 输入 B站主页或数字 UID，有限采集本人公开文字和可用视频字幕。
- 无字幕时，可显式选择下载有限音轨并用本地 Whisper 转录；不会自动安装或下载模型。
- 从有限样本提炼可编辑画像，查看依据、人工确认后启用。
- 本地生成中文向量并缓存，无需云端向量 API Key；失败时明确提示关键词兜底。
- 结合画像与检索资料进行问答，展示原文、日期和来源；不同人物资料、画像和索引隔离。
- 可直接用自己的 TXT、Markdown（`.md` / `.markdown`）问答，也可导入已导出的同人物公众号原文 JSON。
- Windows 自动安装项目依赖、后台启动网页，支持可选可信局域网访问。

```text
主页 / UID → 公开文字或视频字幕 ─┐
                               ├→ 提炼画像 → 查看样本、审核 → 本地知识库 → 风格问答
已有同人物公众号原文 JSON ──────┘
```

不保证任意博主一键成功：资料需有足够本人表达，背景歌、标题、转载不能当本人观点；至少3条内容、600字有效文字才开放人物画像。

## 快速开始（Windows）

先安装 Python（新环境推荐3.11/3.12，勾选 Add Python to PATH），将项目放在短路径，例如 `D:\AI\PersonaLens`。

```powershell
git clone https://github.com/wmamba0824/PersonaLens.git
cd PersonaLens
.\setup_local.bat
```

安装器创建独立`.venv`，安装最小依赖；仅在`.env`不存在时创建，不覆盖已有Key或修改系统Python。
编辑本地`.env`，填写你自己的、账户有可用API额度的Key：

```dotenv
DEEPSEEK_API_KEY=你的_API_Key
```

然后双击`start_local.bat`，打开`http://127.0.0.1:8501/`。停止使用`stop_local.bat`。

网页默认进入“B站人物助手”：

1. 输入数字UID或`https://space.bilibili.com/数字UID`，打开人物。仅此一步不采集、不扣API额度。
2. 确认内容使用权限，采集文字或视频资料；查看实际原文和跳过原因。
3. 同意有限样本外发，生成人物画像草稿。
4. 对照样本编辑和审核，确认画像后准备本地索引。
5. 同意聊天外发后提问，展开“本次实际参考资料”核对来源。

刷新不会重新采集或提炼。资料变化后旧画像停止使用，需重新生成并审核。部分采集需要本地平台登录态，见[使用指南](docs/USAGE.md)。

### 已有 TXT、Markdown、公众号或视频

- **TXT / Markdown**：将`.txt`、`.md`或`.markdown`放入`knowledge/`（支持子目录和混用），网页切换“本地资料问答”；不需要先生成画像。Markdown按文本读取，不执行代码或抓取链接；README等说明文件不参与检索。可选虚构示例见[examples](examples/README.md)，详细规则见[资料目录说明](knowledge/README.md)。
- **公众号JSON**：离线导入已有原文导出，不是任意链接爬虫；见[导入格式](docs/USAGE.md#公众号原文-json)。
- **无字幕视频**：先显式运行下面命令准备可选本地转录，再在网页勾选本机转录。

```powershell
.\setup_video.ps1 -DownloadModel
```

语音模型约486MB，不随仓库提供。默认只识别每条开头180秒，不是全量直播转录。

## API、隐私和费用

默认聊天接DeepSeek。其他兼容接口可配置`DEEPSEEK_API_URL`与`DEEPSEEK_CHAT_MODEL`，但不承诺任意厂商只换Key即可使用。

向量和可选语音识别在本机处理；画像提炼、聊天使用你配置的云端API并消耗额度。
画像最多发送12,000字样本；人物聊天发送问题、画像、必要历史及最多6,000字检索上下文块，不是整库上传，也不是完全离线AI。

`.env`、用户TXT/Markdown、`.runtime`、模型、索引和日志均不应提交；只能导入你有权使用、且允许发送给所配置API的资料。
详细边界见[隐私与使用范围](docs/PRIVACY.md)。

## 本机与手机

- 本机：`start_local.bat`，仅监听`127.0.0.1`。
- 手机同一可信Wi-Fi：停止后运行`start_lan.bat`，打开脚本输出地址并输入访问码。访客会使用这台电脑的资料和API额度。
- 端口被占用：先检查对应服务，或运行`powershell -ExecutionPolicy Bypass -File .\start_local.ps1 -Port 8502`。

不要公网端口转发。本模板不包含在线托管、公共账号/支付、多租户或已部署站点。

## 目录与验证

独立模板最初在全新环境通过119项离线测试；加入Markdown后，完整回归为**132项通过、2项权限相关跳过**。
正式脚本启动、HTTP健康检查及安全停止也已验证；测试未新增真实API调用。

```text
chat_app.py        独立网页入口
app/ingest/       采集、画像、审核、人物存储与问答
app/rag/          本地向量与关键词兜底
app/llm/          API客户端
instances/        通用配置，不附带真实人物画像
scripts/          自检、公众号导入与本地转录准备
tests/            使用合成资料和模拟API的离线测试
docs/             使用、隐私与验收说明
examples/         明确标注的虚构示例，不是人物原文
```

本模板从维护项目的白名单源码整理，初始化全新Git仓库；不附带旧人物原文、画像、向量、Key或维护者账号状态。
历史功能验证与独立模板测试分开记录，见[验证说明](docs/VALIDATION.md)。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/doctor.py
.\.venv\Scripts\python.exe scripts/check_release.py
```

常规测试禁止未模拟的HTTP请求，不自动消耗聊天额度或下载模型。真实回答可能有误，引用编号合法也不等于所有解释获得原文支持。

欢迎提交脱敏问题与试用反馈，见[贡献说明](CONTRIBUTING.md)。不要宣传为真人复制、当前观点保证或全平台一键爬取。

## 许可证

代码采用[MIT许可证](LICENSE)。代码许可不包含任何第三方内容、人物身份、头像或声音的使用权；
依赖软件和模型遵循各自的许可证。本仓库不附带任何真实人物原文、画像或账号凭据。
