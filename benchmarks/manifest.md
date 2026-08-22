# Benchmark 任务集（ground truth）

每个样例对应 PRD 验证方法论 11.3 里的一类任务。ground truth 是「标准答案 + 证据」，
用于 A/B 对照里判定 agent 是否定位到正确 root cause。

> 编译：`targets/` 下 `gcc -O0 -o <name>.exe <name>.c`（mingw-w64）。
> 关键函数都 `__declspec(dllexport)` 导出，DbgEng 可按符号名解析（无需 PDB）。

| # | 样例 | PRD 类别 | 崩溃类型 | ground truth（标准答案） |
|---|------|---------|---------|------------------------|
| 1 | crash_target.exe | NULL deref / AV | 0xC0000005 | `crash_here` 解引用 NULL 写 |
| 2 | uaf_target.exe | UAF | 0xC0000005 | 对象 free 后槽位被 `memset('A')` 复用，悬垂调用函数指针；崩溃指令 `call rdx`，`rdx=0x4141414141414141` |
| 3 | branch_target.exe | 错误分支定位 | 0xC0000005 | `classify` 分支条件 `n>10` 应为 `n>=10`（off-by-one），n==10 走了 error_path |
| 4 | dllload_target.exe | DLL load failure | 0xC0000005 | `LoadLibrary` 失败返回 NULL，未检查就 `GetProcAddress(NULL)` 再 call |
| 5 | badparam_target.exe | 错误参数追踪 | 0xC0000005 | `use_buffer` 的 length 参数未校验，8 字节缓冲区传入 0x40000 长度 → 越界读 |
| 6 | stack_target.exe | 异常调用栈定位 | 0xC00000FD | 无终止条件的递归 + 大栈帧 → 栈溢出 |
| 7 | heapcorrupt_target.exe | heap corruption | 堆校验断点（调试堆下）| `memset` 溢出 16 字节堆缓冲，损坏相邻块 header；`free` 时 `RtlpCheckBusyBlockTail` 检出。**注意：无调试器时可能不崩（无调试堆），benchmark 均在 DbgEng 下跑** |
| 8 | condbp_target.exe | 条件断点验证 | 0xC0000005 | 10000 次循环中 i==500 时 `process_item` 解引用 NULL；高效做法是条件断点 |
| 9 | delayedcrash_target.exe | 启动后延迟崩溃 | 0xC0000005 | Sleep(4s) 后解引用 NULL；崩溃不在启动点 |
| 10 | threads_target.exe | 多线程状态观察 | 0xC0000005 | 8 个线程中仅 id==3 的线程解引用 NULL，需按线程区分。**已验证**：崩溃 AV 写 0x0，faulting 线程 pc=`worker+0x36`（`mov [rax],1`），其余线程在 ntdll；worker 入口断点逐线程记录 `(tid, rcx=arg)`，崩溃时反查 faulting tid → arg=3 ✓（注意：engine 线程 index 每次运行会变，**TID 才是稳定标识**） |
| 11 | unknown_target.exe | 未知 crash 根因 | 0xC0000005 | `table_lookup` 未校验 index，`index*2` 越界读（非明显的 NULL 解引用） |

## 判定标准（每样例）

- **成功**：agent 给出的 root cause 与 ground truth 一致，且引用了可验证证据
  （寄存器值 / 崩溃指令 / 栈回溯 / 异常码，至少其一）。
- **失败**：root cause 错误、空泛（如只说"崩溃了"没说为什么）、或无证据支撑。

## 记录指标（对应 PRD 11.4）

Task Success Rate / Time to Resolution / Tool Calls / Tokens / Invalid Calls /
Human Interventions / Debugger Operations / Evidence Quality。
