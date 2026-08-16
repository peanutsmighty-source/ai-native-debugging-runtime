# M1 Backend Spike 报告

日期：自动生成（agent 运行）
结论：**M1 通过。首个 backend 选定 DbgEng（非 x64dbg）。**

## 1. 结论一句话

DbgEng（通过 pybag 在进程内托管 `dbgeng.dll`）可以在**完全无头、无 GUI、无需管理员**的前提下，稳定完成
`launch/attach → 阻塞等待断点/异常事件 → 读寄存器/内存/反汇编 → 结构化 JSON → terminate` 的完整闭环。
因此 MVP 的 backend 从 PRD 原定的 x64dbg 调整为 **DbgEng**，x64dbg 降级为后续可选 backend。

## 2. 验证结果（对照 M1 的 5 个验证点）

| # | 验证点 | 结果 | 证据 |
|---|--------|------|------|
| 1 | launch（无头） | ✅ | 初始断点停在 `ntdll!LdrpDoDebuggerBreak+0x35`，无 GUI |
| 1 | attach（无头） | ✅ | attach 到运行中进程，停在 `ntdll!DbgBreakPoint+0x0`，模块列表正确，detach 正常 |
| 2 | 断点事件阻塞等待 | ✅ | `dbg.go(10)` 阻塞返回，停在 `crash_target!crash_here+0x0` |
| 2 | 异常事件阻塞等待 | ✅ | 捕获 Access Violation，`code=0xc0000005`、`address=0x...`、`param0=1`(写)、`param1=0`(NULL) |
| 3 | 读寄存器/内存/反汇编 | ✅ | 全部结构化返回（寄存器 hex、内存 bytes、capstone 反汇编） |
| 4 | 结构化输出 | ✅ | 纯 JSON 输出到 stdout（debugger 输出已抑制） |
| 5 | 版本稳定性 | ✅ | vendor 锁定 cdb/dbgeng **10.0.29617.1000**，per-user 解包，无系统依赖 |

额外验证：**事件回调的返回值会被正确执行**（返回 `GO` 会越过断点继续，返回 `BREAK` 会停下）。
这决定了 M2 的 `go_handled`（异常继续）语义是可行的。

## 3. 技术选型

| 组件 | 选择 | 说明 |
|------|------|------|
| 调试引擎 | DbgEng（`dbgeng.dll`） | 微软官方、天生无头、符号/事件/阻塞等待齐全 |
| 进程内绑定 | pybag 2.2.16（ctypes 封装 DbgEng COM） | 无需额外 C 扩展；直接调 `IDebugClient/IDebugControl/...` |
| 反汇编 | capstone（pybag 依赖） | 结构化反汇编 |
| 获取 DbgEng 的方式 | 从 WinDbg MSIX 解包 `amd64/` 目录，vendor 进项目 | 见下；**无需管理员、无需安装 SDK** |
| 测试目标 | mingw-w64 gcc 编译的 `crash_target.exe`（导出 `crash_here`） | 无 MSVC/PDB 也能解析导出符号 |

## 4. 环境依赖与获取方式（已在本次跑通）

- Python 3.14.4（系统已装）
- 依赖：`pybag`（`--no-deps` 装本体）+ `capstone`、`win32more`；`comtypes`、`pywin32` 已存在。
  - 注意：`pip install pybag`（带依赖）会卡住，需分开装。
- DbgEng：`winget download Microsoft.WinDbg` → 解包 msixbundle → 内层 `windbg_win-x64.msix` → `amd64/` 目录
  复制到 `vendor/dbgeng/`。该目录包含 `cdb.exe`、`kd.exe`、`dbgeng.dll`、`dbghelp.dll`、`symsrv.dll` 等
  （完整的 Debugging Tools for Windows）。
- mingw：`winget install BrechtSanders.WinLibs.POSIX.UCRT`。

## 5. 关键技术发现（M2 必须注意）

1. **事件回调绝不能抛异常**：回调内任何异常都会被 comtypes 吞成 `E_FAIL`，引擎打印
   `Callback failed with 80004005`。必须 try/except 包裹，且用 `getattr(record, "contents", record)`
   解包 comtypes `POINTER`。
2. **debuggee 的 stdout 会污染 CLI 输出**：目标进程的 `printf` 走继承句柄直接打到控制台。
   M2/M3 必须用 `CreateProcess2` 重定向 debuggee 输出，或单独捕获。
3. **mingw 无 PDB，符号仅限导出函数**：`get_name_by_offset` 对非导出函数只返回
   `crash_target+0xoffset`。要完整符号化，基准目标要么全部导出关键函数，要么改用 MSVC `/Zi` 生成 PDB。
4. **系统 DLL 符号需配置符号服务器**：默认符号路径 `srv*`（无本地缓存），ntdll/kernel32 只显示
   `module+offset`。建议设 `_NT_SYMBOL_PATH=srv*C:\symcache*https://msdl.microsoft.com/download/symbols`。
5. **pybag 的 `GetLastEventInformation` 是 E_NOTIMPL**：停因（stop reason）改用自定义事件回调记录，效果一致。
6. **comtypes 首次 import 会在用户目录生成 TLB 模块**（`comtypes.gen.DbgEng`），无需管理员，已验证。

## 6. 复现命令

```powershell
# 编译目标
#   gcc -O0 -o crash_target.exe crash_target.c   (targets/ 下)

# 主 spike（完整闭环）
python backends/dbgeng/spike_demo.py

# 附加 + 回调返回值验证
python backends/dbgeng/m1_extra_checks.py attach
python backends/dbgeng/m1_extra_checks.py go
```

## 7. 对后续里程碑的影响

- **M2（Core+Adapter）**：把 spike 里的 `snapshot/disasm/read_regs/事件捕获` 提升为 PRD 的
  `core/` 抽象（Session/State/Event/Observation/Action），`backends/dbgeng/adapter.py` 实现该接口。
- **M3（CLI）**：`dbg` 命令输出纯 JSON；debuggee stdout 必须重定向（见发现 2）。
- 基准目标集（benchmarks）用 mingw 编译、关键函数导出；复杂样本（UAF/heap corruption）后续补。
