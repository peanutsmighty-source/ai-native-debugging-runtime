# ai-debugger — AI-Native Windows Debugging Runtime

让 Claude Code / Codex / Cursor 等 Coding Agent 能直接进入并操作 Windows 用户态调试环境。
**不重新实现 debugger，而是重新设计 Agent 与 debugger 之间的接口**（对应 PRD v0.1）。

一句话：`Agent → MCP/CLI → Core → DbgEng Adapter → dbgeng.dll`。

## 状态

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | backend spike（选型 + 无头闭环验证） | ✅ 通过，选定 **DbgEng** |
| M2 | core/ 抽象 + DbgEng adapter | ✅ 通过 |
| M3 | `dbg` CLI（daemon + 客户端，JSON 输出） | ✅ 通过 |
| M4 | MCP server（16 个 tool） | ✅ 通过 |
| 下一步 | benchmark 任务集 + A/B 验证（PRD 验证方法论） | 待做 |

## 目录结构

```
ai-debugger/
├── core/                 # 调试器无关的抽象（PRD 原语）
│   ├── types.py          #   StateSnapshot/StopReason/ExceptionInfo/...
│   └── session.py        #   DebugSession 接口（15 个方法）
├── backends/
│   └── dbgeng/
│       ├── adapter.py    #   DbgEngAdapter：实现 DebugSession
│       ├── spike_demo.py #   M1 原始 spike（可独立运行）
│       └── m1_extra_checks.py
├── cli/
│   ├── daemon.py         #   持有持久 session 的 HTTP 守护进程
│   └── dbg.py            #   瘦客户端（每命令输出 JSON）
├── mcp/
│   └── server.py         #   MCP server（stdio），16 个 tool
├── benchmarks/targets/
│   ├── crash_target.c    #   NULL deref → AV（launch/attach/断点/异常 测试用）
│   ├── uaf_target.c      #   真实 UAF 样本（释放后调用被 'A' 覆盖的函数指针）
│   └── build.ps1
├── vendor/dbgeng/        #   解包自 WinDbg MSIX 的 Debugging Tools（dbgeng.dll/cdb.exe/...）
├── tests/
│   ├── test_core_closure.py   # M2 端到端
│   ├── test_mcp_smoke.py      # M4 冒烟（tools/list）
│   ├── test_mcp_flow.py       # M4 全流程（launch→bp→run→AV→terminate）
│   └── agent_debug_uaf.py     # Agent-in-the-loop 真实 UAF 根因定位
└── docs/M1_backend_spike_report.md
```

## 环境

- Python 3.14（本机 `C:\Python314\python.exe`）
- `vendor/dbgeng/`：DbgEng 10.0.29617.1000（从 WinDbg MSIX 解包，无需管理员）。
  **clone 后先跑 `scripts/setup_dbgeng.ps1` 重新生成**（vendor 已 gitignore）
- Python 依赖：`pybag`、`capstone`、`win32more`、`comtypes`、`pywin32`、`mcp`、`keystone-engine`
- 目标编译：mingw-w64（`winget install BrechtSanders.WinLibs.POSIX.UCRT`）

> 依赖安装要点（踩坑记录）：`pip install pybag`（带依赖）会卡住；应
> `pip install pybag --no-deps` + `pip install capstone win32more`（comtypes/pywin32 已存在）。
> MCP SDK 是 2.0.0，用的是 `from mcp.server import MCPServer`（不是旧的 `FastMCP`）。

## 使用

### 编译测试目标

```powershell
# targets/ 下：
gcc -O0 -o crash_target.exe crash_target.c
```

### CLI（人类 / 脚本）

```powershell
# 终端 A：起守护进程
python cli/daemon.py --port 9777

# 终端 B：驱动调试器（每个命令输出 JSON）
python cli/dbg.py launch C:\path\app.exe
python cli/dbg.py breakpoint add mod!func
python cli/dbg.py run --timeout 10
python cli/dbg.py observe
python cli/dbg.py memory read 0x7ff600001000 16
python cli/dbg.py disassemble 0x7ff600001000 8
python cli/dbg.py run --timeout 10          # 停到 AV
python cli/dbg.py terminate
```

### MCP（AI Agent）

在 Claude Desktop / Claude Code / Cursor / Cline 注册：

```json
{
  "mcpServers": {
    "ai-debugger": {
      "command": "python",
      "args": ["E:\\startup\\ai-debugging-rt\\ai-debugger\\mcp\\server.py"]
    }
  }
}
```

Agent 可调用的 tool：`launch / attach / restart / terminate / detach / run / pause / step /
wait_event / observe / snapshot / read_memory / disassemble / breakpoint_add /
breakpoint_remove / breakpoint_list`。所有 tool 返回结构化 JSON（StateSnapshot 带
stop_reason / registers / modules / backtrace / disassembly / exception）。

## 测试

```powershell
python tests/test_core_closure.py   # M2：launch→bp→run→内存/反汇编→AV→terminate
python tests/test_mcp_smoke.py      # M4：tools/list + 一次调用
python tests/test_mcp_flow.py       # M4：全流程走 MCP
```

## 关键设计决策（详见 docs/M1_backend_spike_report.md）

1. **backend 选 DbgEng 而非 x64dbg**：DbgEng 天生无头、阻塞事件等待、符号齐全，且能
   per-user 解包（无需管理员、无需装 SDK）。x64dbg 降级为可选 backend。
2. **事件回调必须异常安全**：回调内抛异常会被 comtypes 吞成 E_FAIL（`Callback failed
   with 80004005`），且返回值会被忽略。所有 handler 都 try/except + 解包 comtypes POINTER。
3. **timeout 必须传 int**：`WaitForEvent` 收 ULONG，传 float 会让 pybag 静默吞掉 TypeError，
   导致 `run()` 不等事件立即返回（M3 已踩并修复）。
4. **debuggee stdout 必须隔离**：launch 用 `DETACHED_PROCESS` 阻止目标进程继承 std 句柄，
   否则目标 printf 会污染 CLI/MCP 的 stdio 流。
5. **符号**：mingw 无 PDB，仅导出函数有符号名；要完整符号化需 MSVC `/Zi` 或导出关键函数。
   系统 DLL 符号需设 `_NT_SYMBOL_PATH=srv*C:\symcache*https://msdl.microsoft.com/download/symbols`。

## 已知限制 / 下一步

- benchmark 任务集（PRD 的 10 个样例：UAF、heap corruption、DLL load failure 等）尚未建齐（现有 crash_target + uaf_target 两个）。
- A/B 对照验证（vs 直接 x64dbg MCP，测 Tool Calls/Token/成功率）未做。
- snapshot/restore、Experiment 原语、条件断点（PRD P1）未做。
- pause 语义是 best-effort（`SetInterrupt` + wait）；复杂场景需再验证。
- attach 到受保护/高完整性进程需提升权限（本环境非 admin）。

两个踩坑记录：
- **MCP tool 不要返回裸 list**：mcp 2.0.0 的 structured_content 对 list 返回值会丢元素（disassemble
  只回 1 条）。已改为返回 dict（`{"instructions": [...]}`、`{"breakpoints": [...]}`）。
- **Windows 11 CET**：UAF 样本里 `call rdx` 跳到 0x4141... 时，异常记录的 faulting address 报
  `0xFFFFFFFFFFFFFFFF`（而非 0x4141...）。判 UAF 要看寄存器（`rdx=0x4141414141414141`），不要只看
  exception 的 address 字段。
