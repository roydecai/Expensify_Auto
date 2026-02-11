---
name: "jira-communicator"
description: "Handles Jira Cloud REST API v3 communication (fetch issues/attachments/comments and post comments). Invoke when any Jira data read/write is needed."
---

# JIRA 通讯 Skill

## 目标
以 Jira Cloud REST API v3 为唯一通道，完成 Jira 信息读取与写入（评论、附件下载），不做业务判断与内容处理。

## 职责边界
- 只负责 Jira 通讯与数据编解码（HTTP、鉴权、ADF 文本体）
- 不做业务筛选逻辑之外的判断、不调用外部 AI、不处理附件内容
- 不负责持久化与调度

## 触发条件
- 需要校验 Jira 连接或权限
- 需要根据 JQL 搜索工单
- 需要读取评论或下载附件
- 需要向工单回写评论或上传附件

## 输入（结构化）
```json
{
  "server": "https://your-domain.atlassian.net",
  "auth": {
    "type": "basic",
    "username": "user@example.com",
    "api_token": "xxxxx"
  },
  "operation": "get_current_user | search_issues | get_comments | download_attachment | add_comment",
  "params": {
    "issue_key": "DEMO-1",
    "jql": "project = DEMO AND status = \"Pre Authorize\"",
    "fields": ["id", "attachment"],
    "max_results": 200,
    "attachment_id": "10001",
    "comment_text": "plain text"
  }
}
```

## 输出（结构化）
```json
{
  "ok": true,
  "data": {},
  "error": {
    "type": "auth | permission | network | rate_limit | validation | unknown",
    "message": "string",
    "retryable": true,
    "http_status": 401
  }
}
```

## 核心步骤
1. 鉴权与基础头  
   - 使用 Basic Auth（username + API token）  
   - 头部：Accept=application/json，写请求需 Content-Type=application/json
2. 验证连接  
   - GET /rest/api/3/myself
3. 搜索工单  
   - POST /rest/api/3/search/jql  
   - body: jql、fields、maxResults
4. 读取评论  
   - GET /rest/api/3/issue/{issueKey}/comment
5. 下载附件  
   - GET /rest/api/3/attachment/content/{attachmentId}  
   - 允许重定向
6. 回写评论  
   - POST /rest/api/3/issue/{issueKey}/comment  
   - body 使用 ADF 文本结构：
     - type=doc, version=1, content=[paragraph->[text]]

## 失败策略
- 401/403：鉴权或权限不足，标记不可重试，返回明确错误
- 404：资源不存在或无权限，标记不可重试
- 429/5xx/网络超时：可重试，指数退避，最多 3 次
- JSON 解析失败：返回 validation 错误并保留原始响应片段
- 任何异常：返回 unknown，附带 http_status 与错误信息

## 约束
- 仅支持 Jira Cloud REST API v3
- 评论与多行文本字段使用 ADF 结构
- 单次搜索最大结果数受 Jira 限制，必要时使用分页