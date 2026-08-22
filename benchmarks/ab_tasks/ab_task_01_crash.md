# A/B 任务 01 — 崩溃根因定位（crash_target）

你是一个 Windows 调试分析师。用 `mcp__dbg__*` 工具（DSH 里显示为
`mcp__dbg__launch` / `mcp__dbg__run` / `mcp__dbg__observe` 等）分析一个
原生 x64 Windows 程序并回答它的崩溃根因。

## 目标

程序：`E:\startup\ai-debugging-rt\ai-debugger\benchmarks\targets\crash_target.exe`

1. 启动它（不需要参数），让它跑完（它会自己崩溃）。
2. 观察崩溃状态：异常类型、崩溃指令所在函数（符号）、关键寄存器/内存证据。
3. 得出根因结论：**哪个函数、什么操作导致崩溃**。

## 判定标准（你的报告必须包含这些证据）

- 异常代码（如 0xC0000005）
- 崩溃时 PC 对应的函数符号（如 `crash_target!crash_here+0x...`）
- 崩溃指令的汇编（mov/读写什么）
- 涉及的内存地址（写/读的目标地址，如 0x0）

## 工作方式约束（严格遵守）

- 所有调试操作必须通过 `mcp__dbg__*` 工具，不要用 pwsh 直接调 python 后端或写自定义脚本。
- 完成后把结论写入文件 `E:\startup\dsh_work\ab_report_<你的组名>.md`（markdown，
  含上面判定标准要求的全部证据 + 一句话根因）。
- 报告写完后简短总结一下你用了哪些工具、大概多少步。

## 注意

- MCP 调试服务器是共享的：如果 launch/run 报"target not accessible"或类似错误，
  说明调试对象已退出或状态异常——launch 一个全新进程即可（terminate 后再 launch）。
- 程序崩溃后调试器会停在异常处（stop_reason=exception），这是预期的，不是故障。
- 完成即可，不需要做任何利用/修复。
