# 多模型 AI 答题接口文档

StarCourse 的 AI 答题模块自本版起采用**多提供商统一架构**，突破了仅支持
DeepSeek 的限制：DeepSeek、OpenAI GPT、Anthropic Claude、百度文心一言、
阶跃星辰等模型均可独立配置、按优先级容灾。

- 包路径：`engine/ai_providers/`
- 零新增依赖（仅使用已有的 `requests` 与 `PyYAML`）

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────┐
│                    调用方（Work / Quiz）                   │
│         answer_question() / answer_question_content()     │
└───────────────────────────┬──────────────────────────────┘
                            │
                  ┌─────────▼─────────┐
                  │   MultiAIClient   │  按 priority 顺序容灾
                  └─────────┬─────────┘
            ┌───────────┬───┴────┬────────────┐
            ▼           ▼        ▼            ▼
     OpenAICompatible  Claude  Wenxin   （未来新增提供商）
      （GPT/DeepSeek                          │
        /StepFun/网关）                        │
            └─────────────────────────────────┘
                          │
                 ┌────────▼────────┐
                 │  AIProviderBase │  统一 prompt / 解析 /
                 │                 │  缓存 / 超时 / 重试
                 └─────────────────┘
```

**核心设计**：新增一个厂商适配器只需继承 `AIProviderBase` 并实现两个方法：

| 方法 | 职责 |
|------|------|
| `is_configured()` | 该提供商是否完成必要配置（API Key 等） |
| `_do_chat(system, user, max_tokens, temperature)` | 发送一次对话补全，返回模型文本 |

答题分派、Prompt 构造、答案清洗、JSON 解析、缓存、请求间隔等通用能力
全部由基类提供，保证各厂商答题行为一致。

---

## 2. 支持的提供商

| 内部名称 | 提供商 | 默认模型 | 申请地址 |
|----------|--------|----------|----------|
| `deepseek` | DeepSeek | `deepseek-chat` | https://platform.deepseek.com |
| `openai`（别名 `gpt`） | OpenAI GPT | `gpt-4o-mini` | https://platform.openai.com |
| `claude` | Anthropic Claude | `claude-3-5-haiku-20241022` | https://console.anthropic.com |
| `wenxin`（别名 `ernie`） | 百度文心一言 | `ernie-4.0-turbo-8k` | https://console.bce.baidu.com |
| `stepfun` | 阶跃星辰 | `step-3.5-flash` | https://platform.stepfun.com |

> 任何兼容 OpenAI Chat Completions 协议的服务（自建网关、Moonshot、
> SiliconFlow 等）都可以直接使用：在配置中自定义名称并填写 `base_url`
> 即可，工厂会自动按 OpenAI 兼容协议处理。

**各厂商协议要点**

- **OpenAI 兼容**：`POST {base_url}/v1/chat/completions`（自动兼容
  base_url 是否已含 `/v1`），`Authorization: Bearer <key>`，文本位于
  `choices[0].message.content`。
- **Claude**：`POST /v1/messages`，请求头 `x-api-key` +
  `anthropic-version: 2023-06-01`，`system` 为顶层字段，文本位于
  `content[*].text`。
- **文心一言**：先用 `api_key`(Client ID) + `secret_key`(Client Secret)
  通过 OAuth 2.0 换取 `access_token`（本地缓存至过期前 60 秒），再调用
  `/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{model}`，文本位于 `result`。
- **阶跃星辰**：OpenAI 兼容协议，端点路径前缀为 `/step_plan/v1`。

---

## 3. 配置方式

配置有两个来源，**`config.json` 优先**：

| 文件 | 用途 | 是否入库 |
|------|------|----------|
| `engine/config.json` | GUI「AI 模型设置」写入，也可手动编辑；热更新 | 否（.gitignore） |
| `engine/config.yml` → `AIConfig` | 随仓库分发的无密钥模板 | 是 |

### 3.1 `config.json` 字段说明

模板见仓库根目录 [config.example.json](../config.example.json)。

| 字段 | 类型 | 说明 | 默认 |
|------|------|------|------|
| `default_provider` | string | 默认提供商名称 | `deepseek` |
| `priority` | string[] | 调用优先级链，自上而下依次尝试 | `["deepseek"]` |
| `timeout` | int | 统一请求超时（秒） | `30` |
| `max_retries` | int | 可重试错误（超时/限流/5xx）的重试次数 | `0` |
| `<provider>` | object | 各提供商参数块，键名为内部名称 | — |

提供商参数块通用字段：

| 字段 | 说明 |
|------|------|
| `api_key` | API 密钥（文心还需 `secret_key`） |
| `base_url` | 服务端点根地址 |
| `model` | 模型名称 |
| `path_prefix` | 非标准路径前缀（如阶跃 `/step_plan/v1`），可选 |

最小示例（仅用 DeepSeek）：

```json
{
    "default_provider": "deepseek",
    "priority": ["deepseek"],
    "timeout": 30,
    "max_retries": 1,
    "deepseek": {
        "api_key": "sk-xxxxxxxxxxxxxxxx",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat"
    }
}
```

多模型容灾示例（DeepSeek 失败自动切 GPT，再切 Claude）：

```json
{
    "default_provider": "deepseek",
    "priority": ["deepseek", "openai", "claude"],
    "timeout": 30,
    "max_retries": 1,
    "deepseek": {
        "api_key": "sk-xxxx",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat"
    },
    "openai": {
        "api_key": "sk-xxxx",
        "base_url": "https://api.openai.com",
        "model": "gpt-4o-mini"
    },
    "claude": {
        "api_key": "sk-ant-xxxx",
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-haiku-20241022"
    }
}
```

文心一言配置（注意双凭证）：

```json
"wenxin": {
    "api_key": "百度云 Client ID",
    "secret_key": "百度云 Client Secret",
    "base_url": "https://aip.baidubce.com",
    "model": "ernie-4.0-turbo-8k"
}
```

### 3.2 `config.yml` 的 `AIConfig` 段

结构与 `config.json` 归一化后一致，区别在于各提供商参数收纳在
`providers:` 映射下：

```yaml
AIConfig:
  active: deepseek
  priority: [deepseek, openai, claude, wenxin]
  timeout: 30
  max_retries: 1
  providers:
    deepseek:
      api_key: ''
      base_url: 'https://api.deepseek.com'
      model: 'deepseek-chat'
    # openai / claude / wenxin ...
```

---

## 4. GUI 使用

1. 打开账号的课程工作台；
2. 在左侧「高级设置」中点击 **「AI 模型设置…」**；
3. 选择默认提供商；
4. 在「调用优先级」中勾选启用的厂商，用「上移/下移」调整容灾顺序；
5. 设置全局超时与重试次数；
6. 在「提供商参数」中逐家填入 API Key（文心需同时填 Secret Key）、
   Base URL 与模型名；
7. 点击「保存」。配置写入 `engine/config.json`，**下次答题自动生效，
   无需重启程序**。

密钥输入框为密码模式（显示掩码），且 `config.json` 不会进入版本库。

---

## 5. 代码调用示例

### 5.1 构建客户端

```python
from ai_providers import build_multi_client, build_provider

# 方式一：按配置（config.json 优先，回退 config.yml）构建容灾客户端
client = build_multi_client()

# 方式二：显式传入配置
client = build_multi_client({
    "priority": ["deepseek", "openai"],
    "timeout": 30,
    "max_retries": 1,
    "providers": {
        "deepseek": {"api_key": "sk-xxxx"},
        "openai": {"api_key": "sk-xxxx"},
    },
})

# 方式三：只构建单个提供商
deepseek = build_provider("deepseek", {"api_key": "sk-xxxx"})
```

### 5.2 答题接口

| 方法 | 返回 | 适用 |
|------|------|------|
| `answer_question(question)` | `str`：选择题为字母（如 `"A"`/`"AB"`），判断题为 `"true"`/`"false"`，主观题为文本 | 选择/判断 + 轻量文本 |
| `answer_question_content(question)` | `list[str]`：答案内容列表 | 非选择题、相似度匹配 |

`question` 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `question_type` | string | 题型：`single`/`multiple`/`judgement`/`completion`/`shortanswer`/`term_explanation`/`essay`/`calculation`/`case_analysis`（也接受中文名） |
| `title` | string | 题干 |
| `options` | string[] | 选项列表（选择题） |
| `blank_count` | int | 填空题空数，可选 |
| `system_prompt` | string | 自定义系统提示词（覆盖默认），可选 |
| `user_content` | string | 自定义用户内容（覆盖默认），可选 |

示例：

```python
# 选择题（字母接口）
letter = client.answer_question({
    "question_type": "single",
    "title": "以下哪位是唐代诗人？",
    "options": ["李白", "苏轼", "陆游", "曹操"],
})
# -> "A"

# 主观题（内容接口，按题型模板化作答）
parts = client.answer_question_content({
    "question_type": "essay",
    "title": "试论述李白诗歌的艺术特色。",
})
# -> ["一、豪放飘逸的风格……二、……"]
```

其他方法：`is_configured()`、`check_connection()`（发起一次最小请求
探测连通性）、`get_cache_stats()`、`clear_cache()`。

---

## 6. 错误处理与超时

所有适配器失败时统一抛出 `errors.py` 中的异常，上层可按错误性质决定
重试或切换：

| 异常 | 触发场景 | 默认处理 |
|------|----------|----------|
| `AIConfigError` | 配置缺失/不合法 | 不重试 |
| `AIAuthError` | HTTP 401/403、文心凭证错误码 | 不重试，切换下一家 |
| `AIRateLimitError` | HTTP 429 | 可重试 |
| `AIRequestError` | 超时（`timeout=True`）、连接失败、5xx | 可重试 |
| `AIResponseError` | 非 2xx 业务错误、响应结构异常 | 不重试 |

行为说明：

- 超时由配置中的 `timeout`（默认 30 秒）统一控制，覆盖连接与读取；
- `max_retries` 仅针对可重试错误，按约 0.5s × 次数退避；
- `MultiAIClient` 捕获上述错误后自动切换到优先级链中的下一家；
- 单客户端模式下，底层错误在 `answer_question*` 层收敛为 `None`，
  不会把异常抛给答题主流程。

---

## 7. 扩展新提供商

1. 在 `engine/ai_providers/` 下新建模块（如 `myvendor.py`）；
2. 继承 `AIProviderBase`，实现 `is_configured()` 与 `_do_chat()`；
   HTTP 请求建议复用基类的 `_post_json()` 以自动获得错误分类、
   超时与重试；
3. 在 `factory.py` 的 `PROVIDER_REGISTRY` 中注册名称；
4. 如需在 GUI 暴露，在 [gui/course_workshop.py](../gui/course_workshop.py)
   的 `_AI_PROVIDER_META` 中增加元数据；
5. 参照 `tests/test_ai_providers.py` 补充适配器测试。

示例骨架：

```python
from .base import AIProviderBase
from .errors import AIResponseError


class MyVendorProvider(AIProviderBase):
    provider_name = "myvendor"

    def is_configured(self) -> bool:
        return bool(str(self.config.get("api_key", "")).strip())

    def _do_chat(self, system, user, max_tokens, temperature) -> str:
        data = self._post_json(
            "https://api.myvendor.com/chat",
            {"Authorization": f"Key {self.config['api_key']}"},
            {"prompt": f"{system}\n{user}", "max_tokens": max_tokens},
        )
        text = data.get("reply")
        if text is None:
            raise AIResponseError("myvendor 响应缺少 reply")
        return text
```
