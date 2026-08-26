# ai-debugger — AI-Native Windows Debugging Runtime

让 Claude Code / Codex / DSH 等 Coding Agent 能直接进入并操作 Windows 用户态调试环境。
**不重新实现 debugger，而是重新设计 Agent 与 debugger 之间的接口**（对应 PRD v0.1）。

一句话：`Agent → MCP/CLI → Core 抽象 → Backend（DbgEng / GDB）→ 调试器`。

## 核心价值（已验证）

1. **五原语抽象**（State / Observation / Event / Action / Experiment），完成度 **4/5**：
   结构化停因/异常（非 raw 数据）、事件队列（中间事件不丢）、snapshot/restore 实验原语、
   上下文预算意识（lean after-state + 按需 observe）。
2. **跨后端成立**：同一套 pytest **双后端全绿**——DbgEng **42/42**、GDB **38 通过/4 skip**。
   核心契约 + 崩溃定位 + 真实 exploit（ret2win/fnptr×2/uaf_write）在两个完全不同的
   调试器上零修改通过 → 「debugger-agnostic 接口」有测试背书。
3. **Exploit→RCE benchmark（7 样本）**：ret2win / fnptr_stack / fnptr_heap /
   shellcode（弹计算器）/ uaf_write / **rop_target（真 DEP 绕过：ROP→VirtualProtect→
   栈 shellcode）** / fmtstr_target（%hhn 覆写函数指针）。安全知识沉淀 14 条坑
   （`benchmarks/exploit_manifest.md`）。
4. **A/B 对照**：高层抽象 vs 裸后端 **Tool Calls -70%**（n=9，同后端同任务无混淆）；
   真实 token 计量工具（解析 DSH session 日志的 provider usage）。

## 状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | backend spike（选型 + 无头闭环验证） | ✅ DbgEng |
| M2 | core/ 抽象 + DbgEng adapter | ✅ |
| M3 | `dbg` CLI（daemon + 客户端，JSON 输出） | ✅ |
| M4 | MCP server（29 工具） | ✅ |
| P0 | exploit 调试刚需（search_memory/find_gadget/stdin/线程） | ✅ |
| P1 | benchmark 补全（7 个 RCE 样本 + 崩溃定位 11 样例） | ✅ |
| P2 | A/B 验证 + token 计量（DSH 内可做部分） | ✅ |
| P3 | Event 队列 / Experiment / pytest 套件 / **第二后端 GDB** | ✅ |
| 后续 | x64dbg adapter（反反调试专项）、跨模型 A/B（外部 Claude Code） | 待做 |

## 目录结构

```
ai-debugger/
├── core/                 # 调试器无关抽象（PRD 原语）
│   ├── types.py          #   StateSnapshot/StopReason/ExceptionInfo/...
│   └── session.py        #   DebugSession 接口（~30 方法，含 snapshot/restore/resolve_symbol）
├── backends/
│   ├── dbgeng/adapter.py #   DbgEngAdapter（DbgEng 10.0.29617.1000）
│   └── gdb/adapter.py    #   GdbAdapter（gdb 17.1 MI2 协议，mingw-w64 自带）
├── cli/                  #   daemon.py（HTTP 持久 session）+ dbg.py（JSON CLI）
├── mcp/server.py         #   MCP server（stdio），29 个 tool
├── benchmarks/
│   ├── targets/          #   11 个崩溃定位样例（crash/uaf/threads/condbp/...）
│   ├── exploit_targets/  #   7 个 RCE 样本（ret2win/rop/fmtstr/...）
│   ├── manifest.md       #   崩溃 ground truth
│   ├── exploit_manifest.md  # RCE ground truth + 14 条安全坑
│   ├── ab_results.md     #   A/B 对照结果（三阶段，含诚实边界）
│   └── ab_tasks/         #   A/B 任务集
├── scripts/
│   ├── session_tokens.py #   解析 DSH session 日志的 provider token usage
│   ├── ab_measure.py     #   A/B 会话计量（工具调用 + token）
│   └── ab_split.py       #   按事件 seq 切分 A/B 阶段
├── tests/                #   pytest 套件（双后端）
├── vendor/dbgeng/        #   解包自 WinDbg MSIX（gitignore，跑 setup_dbgeng.ps1 重新生成）
└── docs/
```

## 测试（pytest，双后端）

```powershell
python -m pytest                 # DbgEng 后端：42 passed（~75s）
DSH_TEST_BACKEND=gdb python -m pytest   # GDB 后端：38 passed, 4 skipped（~45s）
```

覆盖：session 生命周期/五原语、Event 队列、snapshot-restore、7 个 RCE exploit、
11 个崩溃 ground truth、threads 线程识别、条件断点、栈溢出。
`tests/conftest.py` 说明：DbgEng 每进程仅一个可用 adapter（pybag 限制 → session 单例）；
GDB 每测试全新进程（无此限制）。

## 环境

- Python 3.14（本机 `C:\Python314\python.exe`）
- `vendor/dbgeng/`：DbgEng 10.0.29617.1000（WinDbg MSIX 解包，无需管理员）。
  **clone 后先跑 `scripts/setup_dbgeng.ps1` 重新生成**（vendor 已 gitignore）
- 依赖：`pybag capstone win32more comtypes mcp keystone-engine pytest`。
  安装要点：`pip install pybag --no-deps` + `pip install capstone win32more`
  （comtypes/pywin32 已存在）。MCP SDK 2.0.0，用 `from mcp.server import MCPServer`。
- 目标编译：mingw-w64（`gcc -O0 -g`）。GDB 后端直接用 mingw 自带的 gdb 17.1。

## 使用

### 编译测试目标

```powershell
cd benchmarks/targets; gcc -O0 -g -o crash_target.exe crash_target.c   # 依此类推
```

### CLI（人类 / 脚本）

```powershell
python cli/daemon.py --port 9777                       # 终端 A：守护进程
python cli/dbg.py launch C:\path\app.exe               # 终端 B：驱动调试
python cli/dbg.py breakpoint add mod!func
python cli/dbg.py run --timeout 10
python cli/dbg.py observe
python cli/dbg.py snapshot                              # Experiment 捕获
python cli/dbg.py restore '{"registers": {...}}'
```

### MCP（AI Agent）

在 Claude Desktop / Claude Code / DSH / Cline 注册：

```json
{ "mcpServers": { "ai-debugger": {
  "command": "python",
  "args": ["E:\\startup\\ai-debugging-rt\\ai-debugger\\mcp\\server.py"]
}}}
```

Agent 工具（29）：`launch / attach / restart / terminate / detach / run / pause / step /
thread_list / set_thread / get_thread / wait_event / observe / snapshot / restore /
read_memory / write_memory / get_register / set_register / disassemble / asm /
search_memory / find_gadget / breakpoint_add / breakpoint_add_hw / breakpoint_remove /
breakpoint_list`。全部返回结构化 JSON。

### 切换后端（库/测试）

```python
from backends.dbgeng.adapter import DbgEngAdapter   # 或
from backends.gdb.adapter import GdbAdapter
```

## 关键设计决策（详见 docs/ 与 ROADMAP）

1. **backend 抽象层是核心**（不是任何单个调试器）：DebugSession 接口 + 双后端证明。
   DbgEng 天生无头；GDB 17 自带 MI2/DAP 且 DWARF 符号更全（能看到 main 等非导出函数）。
   x64dbg 降级为反反调试场景扩展（无头化成本高）。
2. **事件回调必须异常安全**（comtypes 吞异常成 E_FAIL）。
3. **timeout 必须 int**（DbgEng WaitForEvent 收 ULONG，float 静默失败）。
4. **debuggee stdout 隔离**（DETACHED_PROCESS / 句柄重定向）。
5. **exploit 地址必须同会话解析**（进程级 ASLR；跨会话必失败，表现为 `unknown`）。
6. **Windows 11 无 `pop r9;ret`**（用 ntdll 3-pop gadget）；**UCRT 默认禁用 %n 族**
   （需 `_set_printf_count_output(1)`）；**gdb MI exec 异步**（`*stopped` 在 prompt 后）。

## 已知限制 / 下一步

- x64dbg adapter（反反调试专项；跨后端抽象已由 GDB 证明，非核心路径）。
- 跨模型 A/B（真 Claude Code 对照）需外部环境；DSH 的 subagent 被路由到不可用端点。
- attach 到受保护进程需提升权限（本环境非 admin）。
- 系统 DLL 符号化需 `_NT_SYMBOL_PATH=srv*C:\symcache*https://msdl.microsoft.com/download/symbols`。
