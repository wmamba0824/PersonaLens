# PersonaLens 使用指南

## 人物创建

输入B站数字UID或规范主页链接，点击打开后才显示采集选项。仅打开不联网。
不支持姓名搜索、任意网址、短链接或全历史采集。

文字默认最多30条/3页，上限100条/5页；跳过转发、非本人及可识别的付费内容。
视频默认最多3条/1页，上限5条/5页，仅处理第1P。作者ID校验通过后优先读取平台字幕。
标题不能代替口述；歌曲、嘉宾或引用也不能自动视为作者本人发言。

账号登录态可按需从`.env.tools.example`复制空配置行到本地`.env`，填写自己的值；不要覆盖Key。
聊天Key不能替代B站Cookie。遇到限制、验证或登录失败就停止或导入已有资料，不绕过。

## 画像与问答

至少3条资料、600字有效文字才允许提炼画像。每条视频只计一条，不用时间码或已识别歌词凑数量。
生成前需确认最多12,000字样本发送；一次请求失败不自动重试。草稿需对照样本编辑、确认。
画像只用于风格和分析习惯参考，不是事实证据，也不证明作者当前情况。

确认后准备本地FastEmbed/BGE索引。第一次需要下载公开模型，之后缓存复用，不需要向量Key。
不能加载时明确提示关键词降级；可在`.env`设置`RETRIEVAL_BACKEND=lexical`，跳过模型下载。
聊天前另需同意外发：问题、画像、必要历史和最多6,000字检索上下文块。
引用保留原文、日期和URL，仍需人工核对其主体、含义及推演边界。

每个人物状态位于`.runtime/creators/bilibili_UID/`。切换人物、修改资料/画像会清空本页旧对话。
资料与画像可持久复用；聊天仅当前浏览器会话，不提供跨设备同步。

## 本地 TXT / Markdown 资料

将自己的`.txt`、`.md`或`.markdown`文件放到`knowledge/`，支持子目录、大小写后缀和混用。
网页切换“本地资料问答”，点击“重新加载资料”后直接提问，不需要B站UID或先生成画像。
现有的可选风格画像同样可以用Markdown样本提炼，仍需外发同意及人工确认。

使用UTF-8（含BOM）或GB18030编码。Markdown按纯文本切分并参与同一套本地向量/关键词检索，
无需额外依赖，不执行代码、不跟随链接或读取图片附件；来源显示格式及相对文件名。
不会完整还原表格、Front Matter语义或渲染排版。

自动读取跳过隐藏路径、符号链接、目录外目标，以及README/README.*、AGENTS、SKILL命名的Markdown说明文件。
具体规则见[资料目录说明](../knowledge/README.md)。默认不会读取整个代码仓库或自动绑定到某个人物。
增删改文件后，下次网页交互/刷新或手动重新加载会更新索引、清空旧对话；内容变化后旧风格画像停止使用，需重新生成并确认。

`knowledge/`中的TXT/Markdown默认被Git忽略（仓库自带`README.md`除外），不要强制提交个人资料。
本地索引不消耗聊天API额度；提问和主动提炼画像仍会向配置的API外发必要内容并消耗额度。

## 视频本地转录

```powershell
.\setup_video.ps1 -DownloadModel
```

额外安装faster-whisper及CPU依赖、从官方模型源下载small模型，约486MB。网页不会自动安装或下载。
默认识别每条开头180秒，可选30–600秒；独立音轨上限30分钟/40MB，用完删除临时音频。
不绕过付费、试看或访问限制，不使用云端语音API。纯歌词不会被用作画像样本，未识别出来的污染仍需人工检查。

本地自检默认不识别、不下载：

```powershell
.\.venv\Scripts\python.exe scripts/check_local_transcription.py
# 使用你有权处理的音频，显式测试前60秒
.\.venv\Scripts\python.exe scripts/check_local_transcription.py --execute --audio .\your_audio.wav --seconds 60
```

## 公众号原文 JSON

只读导入已有导出文件，先预览：

```powershell
.\.venv\Scripts\python.exe scripts/import_wechat_articles.py --source .\your_article_exports --uid 你的B站数字UID --account-name 你的公众号名称 --confirm-same-author
```

同一人物关系需由你确认，不是官方认证或版权授权。检查统计后加`--execute`才写本地人物副本，导入本身不调用API、不修改源文件。

JSON逐篇保存，字段示意（占位值必须替换，不是可直接导入的真实文章）：

```json
{
  "catalog": {"appmsgid": "数字文章ID", "title": "原始目录标题", "send_time": "Unix时间戳", "deleted": "0"},
  "fetch_status": "ok",
  "http_status": 200,
  "body_source": "js_content",
  "account_name": "你的公众号名称",
  "title": "原始文章标题",
  "title_matches_catalog": true,
  "body_text": "完整原文正文，而非生成摘要",
  "final_url": "https://mp.weixin.qq.com/s/真实公开原文标识"
}
```

只处理选定目录直接包含的JSON，不递归导入分析报告。过滤缺失/摘要/删除/作者不符/未核验标题等记录。
保留稳定公开原文链接，移除临时阅读凭据；不导入生成画像、时间线或周报来循环当原文。
同文章ID或正文哈希去重；改动后的资料会使画像失效。

## 运行排查

- Key只填本地`.env`；进程环境变量优先。替换Key后停止并重启网页。
- API鉴权、余额或模型配置报错：检查服务商配置；不要把原始Key、Cookie或完整运行快照贴到Issues。
- 资料不足：补充有本人表达的合法资料，而非降低门槛强行生成。
- 模型下载失败：检查网络，或显式选择关键词检索；不要关闭TLS校验。
- 端口占用：不要停止不确定的进程。使用另一个端口，或在原项目运行其自己的停止脚本。
- 任务异常中断可能遗留锁；先确认对应任务进程已停止，再处理那个具体人物的锁文件，不删除整个资料目录。
