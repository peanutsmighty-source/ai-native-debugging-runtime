# 变更日志

记录版本/功能/修复的变化。格式：`日期 — 摘要`。

---

## 2026-08-19 — 完成 P0，落地 code review 修复

### exploit 能力补全（P0 全部完成）
- **stdin 输入**：`launch(path, args, stdin_hex)` — headless 安全句柄重定向
  （可继承句柄 + `DEBUG_ECREATE_PROCESS_INHERIT_HANDLES` + `CreateProcess2`）。
  实测 `scanf_target` 注入 "hello" 成功。
- **线程枚举/切换**：`thread_list` / `set_thread` / `get_thread`
  （解锁 `threads_target` 多线程 benchmark）。
- **内存搜索**：`search_memory(address, size, pattern_hex)`。
- **ROP gadget 查找**：`find_gadget(module, [mnemonic...])`（capstone 线性反汇编扫）。

工具集：21 → **29 个 MCP 工具**。

### code review 修复（外部 review 落地）
- **契约同步**：`DebugSession` 接口补全 10 个方法（write_memory/get_register/
  set_register/search_memory/find_gadget/breakpoint_add_hw/module_base/thread_*）。
- **asm 搬离**：`asm_x64` 从 `benchmarks/` 移到 `core/assembler.py`（runtime 能力不是 benchmark 工具）。
- **静默错误 → 结构化错误**：`disassemble` 抛 `BackendError`；快照带 `errors[]` 字段。
- **cmdline quoting**：`" ".join` → `subprocess.list2cmdline`（路径含空格/元字符）。
- **顺带修出的两个隐藏 bug**（被错误记录机制暴露）：
  - `eflags` → `efl`（寄存器名一直写错，之前被静默吞掉）。
  - `initial_break` 幂等性（observe/launch 对同一事件判停因不一致）。

### 其他
- ROADMAP 补「五原语完成度」（2.5/5，Event/Experiment 是主要缺口）。
- P0 全部完成（内存搜索 / gadget / stdin / 线程）。

---

## 2026-08-19（本工作段早前）— exploit→RCE 链路打通

- **弹计算器**：`shellcode_target`（栈溢出 → RWX 缓冲 → WinExec("calc") shellcode），
  实际弹出 `CalculatorApp.exe`。踩平：DEP（x64 栈不可执行 → RWX 缓冲）、栈对齐
  （`and rsp,-0x10` + `sub rsp,0x20`）、字节 typo。
- **asm 工具**：`mcp__dbg__asm`（Keystone，label + `.string` 支持，规避手写字节 typo）。
- **exploit benchmark 4 样本**：ret2win / fnptr_stack / fnptr_heap / shellcode_target
  （`benchmarks/exploit_manifest.md`）。
