# 变更日志

记录版本/功能/修复的变化。格式：`日期 — 摘要`。

---

## 2026-08-21 — 五原语补齐：Event 队列 + Experiment snapshot/restore（2.5/5 → 4/5）

### Event ⚠️ → ✅（事件队列）
- 所有调试器事件入 FIFO 队列（带递增 seq）：breakpoint / exception / **thread_create /
  thread_exit / module_load / module_unload / process_exit**（新增 6 类事件订阅，
  信息事件返回 `DEBUG_STATUS_NO_CHANGE` 不打断执行，process_exit 停住供观察）。
- `wait_event` 新契约：按序返回队列事件（不再只回「最后一个回调 dict」）；
  空队列时真阻塞等待。`run()` 后中间事件全部保留——threads_target 单次 run 队列
  **12 个事件**（7×thread_create + 3×thread_exit + exception），之前只剩最后一个。
- `_snapshot` 健壮化：进程已退出时 pc/sp/pid/status 取不到不再抛异常（记录 errors[]）。

### Experiment ❌ → ✅ 基本（snapshot/restore）
- `snapshot(regions=[(addr,size),...])`：捕获当前线程寄存器 + 指定内存区间 + 断点集，
  返回 JSON-safe dict。
- `restore(snapshot)`：best-effort 写回（寄存器先非 rip 后 rip、内存区间、补缺失断点）。
  实测：改 rax → restore 还原 ✓；写内存 → restore 还原 ✓。Windows 无进程级 checkpoint API，
  恢复的是 agent 可见状态（寄存器/内存/断点）。
- MCP 工具：`snapshot`（带 regions 参数）+ 新增 `restore`；CLI daemon/dbg 同步支持。

### 回归
- ret2win / uaf_write / delayedcrash / threads_target 在新事件处理器下全部通过
  （新增 module_load 等订阅不改执行语义）。

---

## 2026-08-21 — P1 完成：exploit benchmark 从「简化」推到「真绕过」

### 新 exploit 样本（全部端到端验证 + manifest 记录）
- **uaf_write（UAF→任意写）**：free 后 memcpy 改悬垂堆对象的函数指针 cb（偏移 32），
  `o->cb()` 跳 win()。关键教训：**exploit 地址必须同会话解析**（进程级 ASLR，跨会话
  拿到过期基址 → 表现为 `unknown` 极难排查；在初始断点改写 payload 文件绕开）。
- **rop_target（真 DEP 绕过）**：无 RWX 缓冲、无 win()，唯一出路是 ROP 链 →
  VirtualProtect(栈页, RWX) → 栈上 shellcode 弹计算器。**DEP 对照实验**：直接 ret2stack
  是 AV（栈 NX），ROP 后同一地址可执行。踩平：Windows 11 系统 DLL 无 `pop r9;ret`
  （用 ntdll `pop r9;pop r10;pop r11;ret` 3-pop gadget）；gadget 字节搜索可能落在不可执行页
  （PE 头区的第一个 `c3`）；链尾插裸 ret 对齐 `rsp%16`；VirtualProtect 序言覆写区
  （shellcode 放 +0x100）。
- **fmtstr_target（格式化字符串→函数指针）**：`%hhn` 覆写全局 `g_cb` 低字节
  （normal_cb/win 同模块只差低字节 → 计数仅 53）。三个真实发现：
  1. **UCRT 默认禁用 %n 族**（安全缓解），需 `_set_printf_count_output(1)`；
  2. x64 printf 变参位置（va_list 基址=[调用点 rsp+8]，第 N 个 specifier 读第 N 个变参，
     地址槽由帧几何决定，断点 dump 实证定位）；
  3. ucrt `%<w>c` 宽度计数 w≳4000 后异常（w=5000 贡献 0）→ 选小计数目标最稳。

### 其他
- `threads_target` 端到端验证（AV 写 0x0；入口断点逐线程记录 arg 反查 faulting=worker 3；
  **engine index 每运行会变，TID 才是稳定标识**）。
- exploit benchmark 达 **7 个 RCE 样本**（ret2win/fnptr_stack/fnptr_heap/shellcode/uaf_write/
  rop/fmtstr），manifest 坑清单 1–14。
- P1 全部完成（ROADMAP 同步）。

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
