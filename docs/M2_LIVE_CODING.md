# Live local coding evidence — 2026-10-13

**PASS for one arithmetic fixture. Full M2/Release A remains under final project-context verification.**

The actual supervised Ollama CPU server produced a minimal addition correction in **23.508 seconds**. The original fixture's actual approved command failed with exit 1. The separately approved model patch changed subtraction to addition; the same test passed with exit 0. Selective rollback restored original bytes and failing exit 1 while preserving an unrelated user edit. No mock or echo replaced model execution.

Evidence: `evidence/m2-live-local-coding.json`. The runtime archive was SHA-256 verified; qwen2.5-coder:1.5b used a CPU alias with context 2048, four threads and one inference. Ollama reported model allocation 1,109,162,720 bytes and zero VRAM. This allocation is not peak application/system RAM. Application persistence/projects were temporary and removed, and owned model processes stopped. No user database or model configuration was changed.

Earlier setup attempts did not pass: a pull stream timed out after its last reported 973,198,375 bytes, and a later server failed its readiness deadline with about 650 MB available RAM. The exact stall cause was not established. Setup stream/readiness allowances are now separate from the unchanged 60-second inference deadline; CPU setup disables Vulkan/GPU discovery and preserves filtered failure diagnostics. The user freed unused applications before the successful live run. Seven fixture approval-safety regressions still passed.

Independent assessment accepted this narrow live evidence but found two Medium M2 workflow gaps: selected-file proposals omitted inherited project instructions/Git/broader context, and chat was unscoped. Those paths have now been implemented and are undergoing final regression/UI verification. READ approvals use bounded explicit canonical destination sets; ordinary single-file grants remain exact. A subsequent review found Windows-backslash inheritance and a misleading Git timestamp label; fixes and targeted regressions are in progress. Historical Git output is labeled as an earlier approved observation and cannot prove an unchanged current worktree.

The latest verified backend count before the context increment is 397. A parallel context-increment run had one startup-timeout failure with 406 passes; frontend workers for two files failed startup while five files passed. These are not passing completion gates. Checks are now serialized on this device, with no assertion or product deadline removed.

Rust 1.99.0 was installed using the verified official bootstrap into managed build-tool directories with `--no-modify-path`. MSVC prerequisites are still missing. No Windows installer or portable package is claimed yet; M3–M9 remain pending.
