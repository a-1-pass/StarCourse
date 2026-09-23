# Changelog

本项目所有重要变更记录。日期格式为 YYYY-MM-DD，遵循 [Keep a Changelog](https://keepachangelog.com/) 简化版。

---

## [2.1.0] - 2026-09-23 — 全面评估与质量修复

### 评估摘要

对项目全部功能模块、代码质量、性能指标与安全漏洞进行了四维评估：
- **安全扫描**：SQL 注入 / 命令注入 / YAML 反序列化 / 硬编码密钥 / SSRF 全部通过，无可利用风险
- **代码质量**：发现并修复 2 个真实缺陷（见下）
- **功能完整性**：多模型 AI 容灾 / 题库答题 / 非选择题 / GUI 双路径均符合设计要求
- **性能指标**：缓存命中、请求间隔控制、线程锁保护均已到位

### 关键变更

- **修复** `src/ai_assistant.py`：`DeepSeekAI` 类缺少 `SYSTEM_PROMPT_SHORTANSWER` 类属性，
  导致 `answer_question_content` 在 system_prompt 为空时触发 `AttributeError`。
  补充与 `engine/deepseek_ai_enhanced.py` 一致的常量定义。
- **修复** `engine/tiku.py`：`Tiku.get_tiku_from_config` 中 `float(cover_rate)` 使用裸
  `except:` 捕获所有异常（含 `SystemExit`/`KeyboardInterrupt`），收窄为
  `except (TypeError, ValueError):`。

### 影响范围

- 回退 AI 客户端 (`src/ai_assistant.py`) 的主观题答题路径不再崩溃
- 题库配置解析的异常处理更安全，不会意外拦截解释器信号
- 全部 106 个单元测试通过，无回归

---

## [2.0.0] - 2026-09-23 — 多模型 AI 架构与项目重构

### 新增

- **多模型 AI 统一架构**：`engine/ai_providers/` 包，支持 DeepSeek、OpenAI GPT、
  Anthropic Claude、百度文心一言，按优先级链自动容灾切换
  - 统一基类 `AIProviderBase`，各厂商适配器只需实现 `is_configured` + `_do_chat`
  - 统一异常体系 `AIProviderError`（auth/rate-limit/request/response），支持 retryable 标记
  - 统一 HTTP 层 `_post_json`：自动错误分类、指数退避重试、超时控制
  - `MultiAIClient` 聚合器保持与历史单客户端完全一致的接口契约
  - 配置双通道：`config.json`（GUI 写，gitignore）优先于 `config.yml` 模板
- **非选择题全题型支持**：`engine/non_choice.py`，覆盖填空/简答/名词解释/论述/计算/案例分析
  - 题型码 5-8 的主观题模板化作答（答题结构模板 + 评分规则 + 学科差异适配）
  - 三级知识关联：题库答案≥30字直接用 / 短答案AI扩充 / 未命中AI生成
- **答题记录持久化**：`engine/answer_record.py`（SQLite），支持进度统计、错题集、JSON 导出
- **GUI 双路径区分**：题库答题与 AI 答题独立路径，`AISettingsDialog` 多模型配置对话框
- **接口文档**：`docs/ai_providers.md`，含字段参考、代码示例、提供商扩展指南

### 变更

- `engine/config.yml`：新增 `AIConfig` 段（多模型配置模板，无密钥）
- `config.example.json`：多模型配置示例
- `README.md`：更新为多模型架构描述

### 移除

- 清理 4 个零外部引用的冗余模块：`src/crypto.py`、`engine/cookies_manager.py`、
  `engine/retry_manager.py`、`engine/notification.py`

### 测试

- 新增 `tests/test_ai_providers.py`（48 个测试）+ `tests/test_non_choice.py`
- 全量 106 个测试通过
