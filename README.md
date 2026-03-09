# cc-sessions — Claude Code Session Manager

[中文版](#中文版) | [English](#english)

---

<a id="english"></a>

## English

A TUI + CLI tool for managing Claude Code conversation history: browse, search, rename, resume, delete sessions.

Pure Python 3 stdlib, no external dependencies.

### Install

```bash
echo 'alias cc-sessions="python3 ~/path/to/cc-sessions.py"' >> ~/.zshrc
source ~/.zshrc
```

### Interactive TUI

Run `cc-sessions` with no arguments:

```
── cc-sessions ──────────────────────── 12/12 sessions ──
 > Add user auth API                        5 min ago    1.2 MB
   Fix login redirect bug                  2 hours ago  340.5 KB
   Refactor database layer                  1 day ago    2.8 MB
   Setup CI/CD pipeline                     2 days ago  156.3 KB
   Debug payment webhook                    3 days ago    4.1 MB
─────────────────────────────────────────────────────────────
 j/k:move  Enter:detail  /:search  +:new  n:rename  d:delete  c:copy  r:refresh  q:quit
```

#### List view

| Key | Action |
|-----|--------|
| `j` / `↓` | Move down |
| `k` / `↑` | Move up |
| `g` / `G` | Jump to top / bottom |
| `Enter` / `→` / `l` | Open detail view |
| `/` | Search (live filter) |
| `+` / `a` | Create new session |
| `n` | Rename session |
| `d` | Delete session (with confirmation) |
| `c` | Copy full session ID to clipboard |
| `r` | Refresh list |
| `q` | Quit |

#### Search mode

Press `/` to start typing. The list filters in real time:

```
── cc-sessions ──────────────────────── 2/12 sessions ──
 / database█
 > Refactor database layer                  1 day ago    2.8 MB
   Debug database connection pool           2 weeks ago  1.3 MB
```

| Key | Action |
|-----|--------|
| Type text | Live filter by title, first message, or session ID |
| `Enter` | Confirm filter, return to list |
| `Esc` | Cancel, restore full list |
| `↑` / `↓` | Move within results |

#### Detail view

Shows metadata and a preview of recent messages:

```
── Add user auth API ────────────────────────────────────
  a3b7c912  ·  my-project  ·  45 msgs  ·  1.2 MB  ·  5 min ago
─────────────────────────────────────────────────────────────
  Recent messages:
  ▶ You
    How long should the JWT expiry be?
  ◀ Claude
    Recommended: access token 15min, refresh token 7 days.
    Updated auth/config.ts accordingly.
  ▶ You
    Add token cleanup on logout
  ◀ Claude
    Added logout endpoint with token blacklist to prevent
    reuse of invalidated tokens.
─────────────────────────────────────────────────────────────
 q:back  Enter:resume  n:rename  d:delete  c:copy ID
```

| Key | Action |
|-----|--------|
| `Enter` | **Resume session in a new terminal tab** (auto `cd` + `claude --resume`) |
| `n` | Rename this session |
| `d` | Delete this session |
| `c` | Copy full session ID to clipboard |
| `q` / `Esc` / `←` / `h` | Back to list |

#### Create a new session

Press `+` or `a` in the list view. Optionally enter a session name (or press `Enter` to skip):

```
 Name: my new feature█  (Enter:create  Esc:cancel)
```

A new terminal tab opens with `claude`. If you provided a name, the tool will automatically detect the new session and rename it.

#### Rename a session

Press `n` in list or detail view. Type the new name and press `Enter`:

```
 Rename: My new session name█  (Enter:confirm  Esc:cancel)
```

The existing title is pre-filled for editing. If the session already has a custom title, it is **overwritten** (not duplicated). The rename is also reflected in Claude Code's built-in `/resume` list.

#### Resume a session

Press `Enter` in the detail view. The tool will:

1. Read the session's original working directory
2. Detect your terminal (iTerm2 / Terminal.app)
3. Open a **new tab** and run `cd <project-dir> && claude --resume <id>`
4. The TUI stays open — you can keep browsing

#### Delete a session

Press `d`, then confirm with `y`:

```
 Delete 'Add user auth API'? (y/N)
```

A backup is saved to `~/.claude/backups/sessions/` before deletion.

### CLI Commands

```bash
cc-sessions list                          # List all sessions
cc-sessions list -n 10                    # Latest 10
cc-sessions list -p my-project            # Filter by project

cc-sessions search "auth"                 # Search session content
cc-sessions info a3b7c912                 # Show details (partial ID ok)

cc-sessions delete --dry-run a3b7c912     # Preview files to delete
cc-sessions delete a3b7c912               # Delete (auto backup + confirm)
cc-sessions delete -f a3b7c912            # Force delete, skip confirm
cc-sessions delete --before 2025-01-01    # Bulk delete old sessions

cc-sessions rename a3b7c912 "New title"   # Rename a session
cc-sessions rename a3b7c912               # Interactive rename (prompts)

cc-sessions clean --dry-run               # Preview orphaned files
cc-sessions clean                         # Clean orphaned files
cc-sessions stats                         # Disk usage statistics
```

### Claude Code Slash Command

Configure `~/.claude/skills/sessions/SKILL.md`:

```yaml
---
name: sessions
description: Manage Claude Code conversation sessions
allowed-tools: Bash
---

Run: python3 ~/path/to/cc-sessions.py $ARGUMENTS
Show the output to the user.
```

Then use `/sessions list -n 5` inside Claude Code.

### Configuration

Create `config.json` next to `cc-sessions.py` (or at `~/.config/cc-sessions/config.json`):

```jsonc
{
    // Working directory for new sessions (default: "~")
    // When you press '+' in the TUI, `claude` opens in this directory.
    "new_session_cwd": "~/Codes"
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `new_session_cwd` | `~` | Working directory when creating new sessions via `+` |

All fields are optional. The file supports `//` comments.

---

<a id="中文版"></a>

## 中文版

管理 Claude Code 对话记录的 TUI + CLI 工具：浏览、搜索、重命名、恢复、删除会话。

纯 Python 3 标准库实现，无外部依赖。

### 安装

```bash
echo 'alias cc-sessions="python3 ~/path/to/cc-sessions.py"' >> ~/.zshrc
source ~/.zshrc
```

### 交互式 TUI

直接运行 `cc-sessions`（不带参数）进入交互界面：

```
── cc-sessions ──────────────────────── 12/12 sessions ──
 > Add user auth API                        5 min ago    1.2 MB
   Fix login redirect bug                  2 hours ago  340.5 KB
   Refactor database layer                  1 day ago    2.8 MB
   Setup CI/CD pipeline                     2 days ago  156.3 KB
   Debug payment webhook                    3 days ago    4.1 MB
─────────────────────────────────────────────────────────────
 j/k:move  Enter:detail  /:search  +:new  n:rename  d:delete  c:copy  r:refresh  q:quit
```

#### 列表页

| 键 | 功能 |
|----|------|
| `j` / `↓` | 向下移动 |
| `k` / `↑` | 向上移动 |
| `g` / `G` | 跳到顶部 / 底部 |
| `Enter` / `→` / `l` | 进入详情页 |
| `/` | 搜索（实时过滤） |
| `+` / `a` | 新建对话 |
| `n` | 重命名对话 |
| `d` | 删除对话（需确认） |
| `c` | 复制完整 session ID 到剪贴板 |
| `r` | 刷新列表 |
| `q` | 退出 |

#### 搜索模式

按 `/` 开始输入，列表实时过滤：

```
── cc-sessions ──────────────────────── 2/12 sessions ──
 / database█
 > Refactor database layer                  1 day ago    2.8 MB
   Debug database connection pool           2 weeks ago  1.3 MB
```

| 键 | 功能 |
|----|------|
| 输入文字 | 实时过滤（匹配标题、首条消息、session ID） |
| `Enter` | 确认过滤结果，回到列表 |
| `Esc` | 取消搜索，恢复完整列表 |
| `↑` / `↓` | 在结果中移动 |

#### 详情页

显示元数据和最近几条消息预览：

```
── Add user auth API ────────────────────────────────────
  a3b7c912  ·  my-project  ·  45 msgs  ·  1.2 MB  ·  5 min ago
─────────────────────────────────────────────────────────────
  Recent messages:
  ▶ You
    JWT token 过期时间设置多少合适?
  ◀ Claude
    建议 access token 15 分钟, refresh token 7 天。
    已在 auth/config.ts 中配置。
  ▶ You
    加一下登出时清除 token 的逻辑
  ◀ Claude
    已添加 logout endpoint 和 token blacklist 机制，
    防止已登出的 token 继续使用。
─────────────────────────────────────────────────────────────
 q:back  Enter:resume  n:rename  d:delete  c:copy ID
```

| 键 | 功能 |
|----|------|
| `Enter` | **在新终端 tab 中恢复对话**（自动 `cd` + `claude --resume`） |
| `n` | 重命名当前对话 |
| `d` | 删除当前对话 |
| `c` | 复制完整 session ID 到剪贴板 |
| `q` / `Esc` / `←` / `h` | 返回列表 |

#### 新建对话

在列表页按 `+` 或 `a`，可选输入对话名称（按 `Enter` 跳过）：

```
 Name: 新功能开发█  (Enter:create  Esc:cancel)
```

会在新终端 tab 中打开 `claude`。如果输入了名称，工具会自动检测新对话并重命名。

#### 重命名对话

在列表页或详情页按 `n`，输入新名称后按 `Enter` 确认：

```
 Rename: 我的新对话名称█  (Enter:confirm  Esc:cancel)
```

如果已有自定义标题，会预填现有名称供编辑。已有的标题会被**覆盖**（不会重复）。修改后在 Claude Code 内置的 `/resume` 列表中也会同步显示。

#### 恢复对话

在详情页按 `Enter`：

1. 读取对话的原始工作目录
2. 自动检测终端（iTerm2 / Terminal.app）
3. 在**新 tab** 中执行 `cd <项目目录> && claude --resume <id>`
4. TUI 不会退出，可以继续浏览

#### 删除对话

按 `d`，然后按 `y` 确认：

```
 Delete 'Add user auth API'? (y/N)
```

删除前自动备份到 `~/.claude/backups/sessions/`。

### CLI 命令

```bash
cc-sessions list                          # 列出所有对话
cc-sessions list -n 10                    # 最近 10 个
cc-sessions list -p my-project            # 按项目过滤

cc-sessions search "auth"                 # 搜索对话内容
cc-sessions info a3b7c912                 # 查看详情（支持部分 ID）

cc-sessions delete --dry-run a3b7c912     # 预览要删除的文件
cc-sessions delete a3b7c912               # 删除（自动备份 + 确认）
cc-sessions delete -f a3b7c912            # 强制删除，跳过确认
cc-sessions delete --before 2025-01-01    # 批量删除旧对话

cc-sessions rename a3b7c912 "新名称"      # 重命名对话
cc-sessions rename a3b7c912               # 交互式重命名（提示输入）

cc-sessions clean --dry-run               # 预览孤立文件
cc-sessions clean                         # 清理孤立文件
cc-sessions stats                         # 磁盘占用统计
```

### Claude Code Slash Command

配置 `~/.claude/skills/sessions/SKILL.md`：

```yaml
---
name: sessions
description: Manage Claude Code conversation sessions
allowed-tools: Bash
---

Run: python3 ~/path/to/cc-sessions.py $ARGUMENTS
Show the output to the user.
```

然后在 Claude Code 中使用 `/sessions list -n 5`。

### 配置文件

在 `cc-sessions.py` 同目录下创建 `config.json`（或放在 `~/.config/cc-sessions/config.json`）：

```jsonc
{
    // 新建对话时的工作目录（默认: "~"）
    // 在 TUI 中按 '+' 时，claude 会在该目录下打开
    "new_session_cwd": "~/Codes"
}
```

| 设置 | 默认值 | 说明 |
|------|--------|------|
| `new_session_cwd` | `~` | 按 `+` 新建对话时的工作目录 |

所有字段可选。配置文件支持 `//` 注释。
