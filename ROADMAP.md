# AI-Native Windows Debugging Runtime — 项目全貌与计划

> 本文档是项目的**一站式入口**：没接触过的人从头读到尾，能看懂「这是什么、为什么做、
> 怎么设计、怎么用、做到哪了、下一步做什么」。续作时先读 §六（关键坑）和 §十（明天从哪开始）。

---

## 一、项目定位与背景

### 1. 一句话

一个让 **Claude Code / Codex / Cursor / DSH 等 AI Agent 能直接操作 Windows 调试器**的轻量
运行时：Agent 可以下断点、单步、读内存/寄存器、捕获崩溃、定位根因，甚至做**漏洞利用到 RCE**。
**不重新实现 debugger，而是重新设计 Agent 与 debugger 之间的接口。**

### 2. 为什么要做（背景痛点）

- 传统 GUI 调试器（x64dbg、WinDbg 图形界面）对人类友好，但 **AI 难以稳定地获取、理解、组织
  调试上下文**——AI 只能看截图、读人贴的代码，不能自己动手操作调试器拿到结果。
- 已有的 x64dbg MCP 项目把「几十上百个底层 API」直接暴露成工具，导致 AI 面临**工具选择难、
  上下文膨胀、冗余调用**。
- 目标：让 AI 用**少量、语义明确**的接口完成真实调试，并让「调试状态」成为一等对象。

### 3. 是什么 / 不是什么

| 是 | 不是 |
|----|------|
| 一套「AI-native 调试接口」抽象 + 一个 DbgEng 后端 | 重新实现一个 debugger 引擎 |
| MCP server + CLI 双出口，共享同一 Core | 又一个「工具更多」的 x64dbg MCP |
| 复用微软 DbgEng（WinDbg/cdb 的底层引擎） | 静态分析平台（那是 IDA/Ghidra 的事） |

### 4. 核心设计思想

把传统 debugger 的低层操作，抽象成适合 AI 推理的四类原语：

- **State**：一次 `observe` 返回完整快照（寄存器/模块/栈/反汇编/停因/异常），而不是几十次零散调用
- **Observation**：面向当前停点的高价值上下文（如 Access Violation 含 fault address/指令/寄存器/栈）
- **Event**：breakpoint / exception / module load 等作为一等对象；`wait_event` **真阻塞**而非轮询
- **Action**：run/step/breakpoint/memory 读写等确定性动作，返回 **after-state** 而非 success

**核心价值主张**：高层抽象让 AI「调用更少、状态更完整、更少拼错」。这个主张已被 A/B 验证（§五）。

### 5. 架构

```
AI Agent（Claude Code / Codex / Cursor / DSH）
        │  MCP（stdio）或 CLI（HTTP daemon）
        ▼
   Core 抽象（core/）          ← 后端无关的契约（Session/State/Observation/Action）
        │
   DbgEng Adapter（backends/dbgeng/adapter.py）  ← 把 DbgEng 翻译成 Core 契约
        │
   dbgeng.dll（vendor/dbgeng/） ← 微软调试引擎（WinDbg/cdb 的底层，per-user 解包，无需管理员）
```

多后端设计：Core 契约定型后，加 x64dbg/GDB/LLDB 只需写对应 Adapter，Core 不动。

### 6. 和现有方案的区别

| | x64dbg MCP（现有开源） | 本项目 |
|---|---|---|
| 后端 | x64dbg（GUI 优先，插件桥接） | DbgEng（进程内、天生无头） |
| 工具形态 | 几十上百个底层端点 | 15→21 个高层工具 |
| 一次调用 | 读寄存器/读内存/反汇编各一次 | `observe` 一次返回完整快照 |
| 停因 | AI 自己拼 | `stop_reason` + 结构化 `exception` 一等字段 |
| 事件等待 | 多数靠轮询 | `wait_event` 真阻塞 |
| 定位 | 薄封装底层命令 | 重新设计 Agent↔debugger 接口 |

### 7. 能做什么（能力）

- **崩溃定位**：`launch → breakpoint → run → observe` 拿到带证据的根因（已跑通 11 类崩溃样本）
- **漏洞利用**：cyclic 算溢出偏移 → `asm` 写 shellcode → `write_memory`/`set_register` 验证 →
  劫持控制流，已做到**弹计算器**（ret2win / fnptr / shellcode 三类）
- **已接入 DSH**：本机 `mcp__dbg__*` 工具是原生工具，AI 直接调

### 8. 关键术语表

| 术语 | 含义 |
|------|------|
| DbgEng | 微软 Windows 调试引擎（WinDbg/cdb 的底层 DLL） |
| pybag | DbgEng 的 Python 绑定（ctypes 封装 COM） |
| MCP | Model Context Protocol，AI 工具接入的通用协议 |
| Core / Adapter | Core=后端无关契约；Adapter=把具体后端翻译成契约 |
| stop_reason | 为什么停（initial_break/breakpoint/exception/step） |
| cyclic | De Bruijn 模式串，定位溢出偏移用 |
| DEP/NX | 数据执行保护，x64 栈不可执行 |
| ASLR | 地址随机化 |
| ROP / gadget | 高级利用：拼接「pop xxx; ret」小片段绕过 DEP |

### 9. 形态决策与架构演进（MCP 优先 + 模块化）

**① MCP 优先（适配各种 agent）**

对外形态固定为 **MCP server**，不绑定任何单一 agent 的原生插件：

- MCP 是行业通用协议，同一套工具能接 Claude Code / Codex / Cursor / DSH / 任意 MCP client
- 已实证：Python MCP server（`mcp` SDK 2.0）与 DSH 的 Node `dsh-mcp-client`（SDK 1.30）协议互通
- CLI 作为第二出口（与 MCP 共享同一 Core），供脚本/人工用

**② 模块化拆分（避免臃肿）**

工具目录每次请求都进模型上下文 → 工具越多、schema 越大，token 越贵、模型选错工具几率越高。
原则：

- **现在 21 个工具，单 server 很清爽，不拆**
- 臃肿临界点约 **40–50 个工具**；过线后按「关注点 × 使用频率」拆：

| Server | 关注点 | 何时挂载 |
|--------|--------|---------|
| `dbg-core` | 会话 + 执行 + 基本观察 | 默认 |
| `dbg-x` | exploit（write_memory/set_register/asm/gadget/search） | 做 exploit 才挂 |
| `dbg-mem` | 结构化解析（PE/TEB/异常链/堆） | 深入分析才挂 |
| `dbg-evt` | 事件订阅/过滤/队列 | 长任务才挂 |
| `dbg-ttd` | 时间旅行调试 | 复现/回放才挂 |

- 中间态：MCP `tools/list_changed` 懒加载（如 `launch` 之后才暴露 exploit 工具）
- 核心原则（呼应 PRD）：**不以 Tool 数量为指标**。成熟形态 = 「薄核心 + 按需模块」，
  不是「塞满 50 个工具的超级插件」

---

## 二、进度总览

| 里程碑 | 状态 |
|--------|------|
| M1 backend spike（选型 → DbgEng） | ✅ |
| M2 Core 抽象 + DbgEng Adapter | ✅ |
| M3 CLI（daemon + 客户端，JSON 输出） | ✅ |
| M4 MCP server | ✅ |
| MCP 接入 DSH（`dsh-mcp-client`） | ✅ |
| 崩溃 benchmark（11 样例 + ground truth） | ✅ |
| A/B 对照（高层抽象 vs 低层 raw，两轮） | ✅ |
| exploit 六件套 + exploit→RCE benchmark（4 样例，含弹计算器） | ✅ |
| **剩余计划（§七）** | ⏳ |

## 三、工具清单（21 个 MCP 工具，`serverName: dbg`）

- 基础 16：`launch/attach/run/step/pause/observe/snapshot/wait_event/read_memory/
  disassemble/breakpoint_add(含 condition)/breakpoint_remove/breakpoint_list/
  restart/terminate/detach`
- exploit 4：`write_memory` / `get_register` / `set_register` / `breakpoint_add_hw`(watchpoint)
- 汇编 1：`asm`（Keystone，x64 Intel 语法，支持 label + `.string`/`.byte`）

## 四、benchmark 资产

- 崩溃（11 样本）：`benchmarks/targets/` + `benchmarks/manifest.md`
- exploit（4 样本）：`benchmarks/exploit_targets/` + `benchmarks/exploit_manifest.md`
  - ret2win(offset72) / fnptr_stack(40) / fnptr_heap(32) / shellcode_target(弹计算器)
- 工具函数：`benchmarks/exploit_util.py`（cyclic/cyclic_find/p64/u64/asm_x64，自包含无 pwntools）

## 五、A/B 验证结论（`benchmarks/ab_results.md`）

- 对照组：同 DbgEng 后端的「低层 raw tools」（`backends/dbgeng/raw.py`，模拟 x64dbg MCP 风格）
- 结果（9 崩溃样本）：**Tool Calls 27 vs 90 = -70%**（远超 PRD -30% 门槛）；bytes +23%（≈持平，
  未计工具 schema；真实 token 需外部补测）
- 关键：第一轮 observe 全量返回 bytes +120%，瘦身后（launch 极简 + run 轻量 after-state +
  observe 按需全量）压到 +23%

## 六、关键坑（都已在代码/文档里修掉，勿重踩）

1. 事件回调不能抛异常（被吞成 E_FAIL）；`GetBreakpointById` 已返回 DebugBreakpoint，勿二次包装。
2. 条件断点 `SetCommand` 要传 bytes。
3. timeout 必须 int（float 会让 WaitForEvent 静默失败）。
4. debuggee stdout 用 `DETACHED_PROCESS(0x8)` 隔离。
5. terminate 不要调 `Release()`（会杀 MCP 单例的 worker 线程，后续 launch 全废）；硬杀 PID 即可。
6. x64 非规范地址 + Win11 CET：`ret`/`call` 劫持崩成 #GP，faulting address 报 `0xFFFFFFFFFFFFFFFF`；
   偏移要从**栈/寄存器读被覆盖的原始值**，别用 exception.params。
7. shellcode：x64 栈强制 DEP（DllCharacteristics=0x0160），不能直接跳栈；用 VirtualProtect 的 RWX 缓冲。
8. shellcode 栈对齐：`and rsp,-0x10` + `sub rsp,0x20`（不是 0x28）；字节用 `asm` 别手写。

## 七、成熟度目标与剩余计划

成熟形态 = 「**稳定、可观察、可恢复、可组合**」+ 五原语（State/Observation/Event/Action/Experiment）。
功能全景（north star），✅已有 / 🔲缺失：

| 能力 | 状态 |
|------|------|
| 会话控制（launch/attach/terminate/detach） | ✅ |
| 执行控制（断点/watchpoint/条件断点/单步） | ✅ |
| 结构化观察（快照/寄存器/栈/反汇编/停因/异常） | ✅ |
| 修改注入（write_memory/set_register/asm） | ✅ |
| **snapshot/restore + 实验原语**（可恢复，试错基石） | 🔲 |
| **函数调用注入**（远程调 VirtualProtect/VirtualAlloc） | 🔲 |
| **内存搜索 + gadget 查找** | ✅ |
| **完整事件集 + 事件队列/订阅** | 🔲 |
| **结构化解析**（PE/TEB/异常链/堆） | 🔲 |
| **TTD 时间旅行**（DbgEng 自带，未暴露） | 🔲 |
| stdin 输入 / 线程枚举 / 源码级调试 | 🔲 |
| 反反调试（x64dbg 生态才有） | 🔲 |

**成熟度度量：五原语完成度**（比功能清单更本质，回答「离 AI-native runtime 还有多远」）：

| 原语 | 完成度 | 现状 |
|------|--------|------|
| State | ✅ | StateSnapshot 结构化快照 |
| Observation | ✅ | 组合观察（停因/异常/寄存器/栈/反汇编） |
| Action | ✅ | run/step/breakpoint/write_memory/set_register/asm |
| **Event** | ✅ | 事件队列（FIFO + seq）：breakpoint/exception/thread_create/thread_exit/module_load/unload/process_exit 全部入队；`wait_event` 按序返回，中间事件不再被覆盖（threads_target 单次 run 队列 12 事件） |
| **Experiment** | ✅ 基本 | `snapshot(regions)` 捕获寄存器+内存区间+断点 → `restore()` 写回（best-effort；Windows 无进程级 checkpoint API，恢复的是 agent 可见状态：寄存器/内存/断点） |

**4 / 5（+Event 补齐、Experiment 基本可用）**。缺口收窄到 Experiment 的「完整进程状态恢复」
（需快照 DLL 内存布局/句柄，Windows 无原生 API，用 snapshot/restore + 重启近似）与订阅/过滤。

**AI-native 独有层**（区别于普通 debugger）：上下文预算意识（观察瘦身+按需）、结构化语义
（stop_reason/异常分类而非 raw 数据）、可解释错误、确定性（snapshot/restore）、工具粒度策略。

### P0 — exploit 调试刚需 ✅ 全部完成
- [x] **ROP gadget 查找器**：`find_gadget`
- [x] **内存搜索**：`search_memory`
- [x] **stdin 输入**：`launch(stdin_data)`（headless 句柄重定向，实测 scanf 注入）
- [x] **线程枚举/切换**：`thread_list`/`set_thread`/`get_thread`（已解锁 `threads_target`）

### P1 — benchmark 补全 ✅ 全部完成
- [x] 崩溃：`threads_target` 端到端验证（AV 写 0x0，faulting 线程 = worker id 3，入口断点逐线程
  记录 `(tid, rcx=arg)` 反查；engine index 每次运行会变，**TID 才是稳定标识**）
- [x] exploit：**UAF→任意写**（`uaf_write`，cb 偏移 32，`'A'*32+p64(win)`，同会话解析地址绕 ASLR）
- [x] exploit：**ROP 链 → VirtualProtect → 栈上 shellcode**（`rop_target`，真 DEP 绕过：
  直接 ret2stack 是 AV，ROP 后同一栈地址可执行 → CalculatorApp 弹出）
- [x] exploit：**格式化字符串 → 函数指针覆写**（`fmtstr_target`，`%hhn` 写 g_cb 低字节；
  关键发现：UCRT 默认禁用 %n 族需 `_set_printf_count_output(1)`、va_list 位置、宽度计数阈值）
- [x] 全部 exploit 样本的地址都在**同一会话内解析**（进程级 ASLR，跨会话拿过期基址必失败）

### P2 — PRD 验证方法论收尾
- [x] ~~真实 token 计量（DSH 内做不到）~~ **纠正**（2026-08-22）：DSH 的 UI 底部 token 来自
  `@deepseek-ai/dsh-token-meter`（Cordis 服务 `ctx.tokenMeter`），数据源是持久化的
  session 事件流 `~/.dsh/sessions/<workspace>/session-<uuid>/session.jsonl.zstd` 里
  `assistant/message` 的 provider `usage`（input/output/reasoning/cacheRead）。
  agent 工具面虽不暴露（tool_stats 无 token；cordis_inspect_query 不能调业务方法），
  但日志可解析 → `scripts/session_tokens.py` 已落地，token 计量在 DSH 内可行。
  剩余：跨模型对照（Claude Code）仍需外部环境；同模型（deepseek-v4-flash）下
  「高层 vs 低层接口」对照可在 DSH 内用两个 subagent + 各自 session 计量完成。
- [ ] 真 x64dbg MCP 对照组（现在用「同后端低层 raw」代理；需先无头化 x64dbg）

### P3 — 架构/长期
- [x] **第二后端：GDB/DAP adapter**（2026-08-22 完成：`backends/gdb/adapter.py`，
  gdb 17.1 MI2 协议。**同一套 pytest 双后端跑通：DbgEng 42/42、gdb 38 过/4 skip**——
  跨后端抽象成立。skip = 3 个 DbgEng 专项 exploit + 条件断点）
- [ ] x64dbg adapter（**降级为反反调试专项**：加壳/反反调试目标才需要，x64dbg 无头化
  成本高、价值窄；跨后端抽象已由 gdb 验证，x64dbg 纯属场景扩展）
- [x] ~~snapshot/restore、Experiment 原语~~（2026-08-21 完成：Event 队列 + Experiment snapshot/restore，
  五原语 2.5/5 → 4/5）
- [x] ~~pytest 测试套件~~（2026-08-22 完成：**42 个测试全绿，72s**——session 生命周期/五原语、
  Event 队列语义、snapshot-restore 往返、5 个简单 RCE + 3 个高级 exploit（shellcode/ROP DEP
  绕过/fmtstr）、10 个崩溃 benchmark ground truth（含 threads 线程识别、条件断点、栈溢出）。
  单例 adapter fixture（pybag 每进程仅一个可用 adapter）；顺带修出 2 个核心 bug：
  `wait_event` 空闲轮询破坏 DbgEng 符号状态、`breakpoint_add` 对 0x 地址的 address 解析）

## 八、文件地图

```
ai-debugger/
├── ROADMAP.md            ← 本文档（先读这个）
├── README.md             ← 安装/使用
├── core/                 ← 抽象契约（types.py 类型 + session.py 接口）
├── backends/dbgeng/
│   ├── adapter.py        ← 高层 DbgEngAdapter（实现 DebugSession）
│   ├── raw.py            ← 低层 RawDbgEng（A/B 对照组）
│   └── spike_demo.py     ← M1 原始 spike
├── cli/                  ← daemon.py + dbg.py（CLI，JSON 输出）
├── mcp/server.py         ← MCP server（21 工具）
├── benchmarks/
│   ├── targets/          ← 崩溃样本（11 个）
│   ├── exploit_targets/  ← exploit 样本（4 个）+ work/（payload）
│   ├── manifest.md       ← 崩溃样本 ground truth
│   ├── exploit_manifest.md ← exploit 样本 ground truth
│   ├── exploit_util.py   ← cyclic/p64/asm 工具
│   ├── ab_runner.py      ← A/B 跑分脚本
│   └── ab_results.md     ← A/B 结论
├── tests/                ← test_core_closure / test_mcp_smoke / test_mcp_flow / agent_debug_uaf
├── vendor/dbgeng/        ← 解包自 WinDbg MSIX 的 DbgEng（dbgeng.dll/cdb.exe，无需管理员）
└── docs/M1_backend_spike_report.md
```

## 九、环境依赖（已就位）

- Python 3.14：`C:\Python314\python.exe`
- pip 包：`pybag`(2.2.16) `capstone` `comtypes` `win32more` `mcp`(2.0.0) `keystone-engine`(0.9.2)
- mingw-w64 gcc：`C:\Users\WHO\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_...\mingw64\bin\gcc.exe`
- DbgEng：`vendor/dbgeng/`（cdb 10.0.29617.1000）
- DSH 接入：`C:\Users\WHO\.dsh\profiles\web\cordis.patch.yml` 里 `insert:` 了 `mcp-dbg`（`dsh-mcp-client`）

## 十、明天从哪开始

1. 先读本文档 §一（定位）、§六（关键坑）、§七（计划）。
2. **从 P0 第一项「内存搜索 + gadget 查找器」动手**：在 adapter 加 `search_memory(addr,size,pattern)`
   和 `find_gadget(insns)`（gadget 用 capstone 反汇编全段扫 `pop xxx; ret` 等模式），再暴露成 MCP 工具。
3. 然后做「ROP 链 → VirtualProtect → shellcode」样本，把 exploit benchmark 从「简化」推向「真 DEP 绕过」。
