# OSS CAD MCP — End-to-End Test Report

**Date:** 2026-11-11
**Repository:** `/home/chalk/oss-cad-mcp`
**Package:** `oss-cad-mcp` 0.1.0, API version 1
**OSS CAD Suite:** 20261105 at `/home/chalk/Kits/oss-cad-suite`
**Platform:** Ubuntu 26.04.1 LTS on WSL2, x86_64
**Test type:** Black-box MCP testing with real HDL projects, plus the repository's existing test suite
**Source changes:** None during the baseline test. Repair changes made afterward are documented in Section 10.

## 1. Executive summary

All **15 exposed MCP tools** were invoked successfully at least once. Testing used three real HDL projects — Verilog, SystemVerilog, and VHDL — plus a formal wrapper, a negative-compile fixture, project-init fixtures, and a long-running simulation for job cancellation.

The basic Verilog/SystemVerilog flows are strong:

- Lint/elaborate, simulation, iCE40/ECP5 synthesis, formal prove/BMC/cover, FPGA bitstream generation, timing analysis, waveform handling, and netlist inspection all produced correct results in their supported paths.
- The repository's existing suite passes: **40/40 tests**.
- `cad_status` reports **155/155** suite binaries present and all advertised capabilities enabled.

However, the server is **not release-ready** in its current state. Six high-impact issues were reproduced:

1. Cancelling a running job crashes the MCP server.
2. VHDL simulation always fails because GHDL elaborates into one directory and runs from another.
3. The default `cad_synthesize` target (`generic`) always fails because `-json` is passed to Yosys's `synth` command, which does not accept it.
4. A `source.filelist` causes the server to silently add unrelated auto-discovered workspace HDL files.
5. VHDL synthesis always fails because the generated Yosys GHDL command omits the required `-e` elaboration flag.
6. `cad_artifact(action="read_text", path=...)` can read a completely different artifact when any job artifact exists.

A running-job cancellation also caused a real loss of the integrated MCP connection (`MCPError: Connection closed`). A fresh stdio MCP server reproduced the same crash deterministically.

**Overall evaluation:** good architecture and a useful tool surface, but at the baseline VHDL support and job cancellation needed fixes before this should be described as a working v0.1 workflow server. Section 10 records the post-fix validation.

## 2. Method and test environment

### 2.1 How the tools were called

Testing was performed through the actual MCP interface, not by importing workflow functions:

- Initial interactive calls used the integrated OpenCode MCP connection.
- After the integrated server crashed during the cancellation test, a fresh server was started over stdio with the project's own `oss-cad-mcp serve` command and driven with the official Python MCP client (`mcp.client.stdio`).
- `session.list_tools()` returned exactly the expected 15 tools.
- The repository's own suite was run with `.venv/bin/pytest -q`: **40 passed**.

### 2.2 Environment checks

`cad_status(deep=true)` reported:

| Item | Result |
|---|---|
| Package version | 0.1.0 |
| Suite version | 20261105 |
| Platform | Ubuntu 26.04.1 LTS (WSL2), verified |
| Workspace | `/home/chalk/oss-cad-mcp` |
| Suite binaries present | 155 / 155 |
| Missing suite tools | none |
| Synthesis / simulation / formal | true / true / true |
| iCE40 / ECP5 / Gowin | true / true / true |
| VHDL / Verilator / Icarus | true / true / true |
| Waveform / FPGA build | true / true |
| Sandbox | unavailable (`bwrap`, warning reported) |
| Hardware programming | disabled by design |

### 2.3 Test projects

Fixtures were created outside the source tree, under `/home/chalk/.cache/oss-cad-mcp/e2e`, so no source files were touched. Generated job data remains under `.oss-cad-mcp/runs/`; build products remain under `build/` (both gitignored).

| Fixture | Language | Purpose |
|---|---|---|
| `verilog_counter` | Verilog-2005 | 4-bit synchronous counter with enable and reset; self-checking testbench; iCE40 PCF; filelist fixture |
| `sv_fifo` | SystemVerilog | 8×8 synchronous FIFO; self-checking testbench; formal wrapper with assertion and cover points |
| `vhdl_pwm` | VHDL-2008 | 8-bit PWM; self-checking GHDL testbench; VHDL synthesis and project-discovery fixture |
| `negative` | Verilog | Undeclared-signal design to exercise diagnostic parsing and failure paths |
| `hang` | Verilog | Long-running testbench used to create a cancellable simulation job |
| `project_init_v` | Verilog | Project inspect → init → validate lifecycle |

The main Verilog testbench prints `TEST PASSED` only after checking reset, hold, count, wrap, and disable behavior. The SystemVerilog FIFO testbench checks full/empty flags, write-while-full rejection, FIFO ordering, and drain-to-empty. The VHDL PWM testbench checks the high-time count over 256 clock samples.

## 3. Tool coverage and results

Legend: **PASS** = primary actions worked; **PARTIAL** = primary actions worked but a material defect was found; **FAIL** = advertised path did not work.

| Tool | Actions exercised | Result | Key evidence |
|---|---|---|---|
| `cad_status` | normal and `deep=true` | PASS | 155 tools present; capabilities all true; sandbox warning correct |
| `cad_project` | inspect, init, validate | PARTIAL | Verilog init/validate passed; VHDL top/testbench detection is incomplete |
| `cad_check` | parse/lint/elaborate; slang, Verilator, Icarus, GHDL; negative | PARTIAL | Normal checks passed; filelist check added unrelated files and failed |
| `cad_simulate` | Icarus, Verilator, GHDL; VCD/FST; negative | PARTIAL | Verilog/SV passed; GHDL simulation always fails |
| `cad_synthesize` | generic, iCE40, ECP5; Verilog/SV/VHDL; filelist | PARTIAL | iCE40/ECP5 pass; generic, VHDL, and filelist paths fail |
| `cad_verify` | prove, BMC, cover; equiv without config; failing and passing properties | PASS | Prove/BMC/cover pass; missing-config error is correct |
| `cad_fpga_build` | iCE40 with PCF, ECP5 unconstrained, missing constraints, unknown board, timing-failure attempt | PASS | iCE40 and ECP5 bitstreams produced; error paths returned proper errors |
| `cad_waveform` | info, signals, sample, summarize, export, VCD→FST, FST→VCD, missing file | PASS | All actions worked; minor summary semantics noted |
| `cad_netlist` | stats, modules, cells, nets, fanout, diff, missing arguments | PARTIAL | Main actions work; `fanout` returns false matches; internal ABC9 modules clutter stats |
| `cad_timing` | parse nextpnr iCE40/ECP5, parse icetime JSON, run icetime, missing device, `.asc` misuse | PASS | Timing values and errors correct; one hint references a nonexistent parameter |
| `cad_job` | list, status, wait, stdout/events logs, rerun, not-found, **cancel running** | FAIL | Cancel returns success but crashes the MCP server |
| `cad_artifact` | list, stat, hash, read_text by artifact, read_text by path, default list | PARTIAL | Direct artifact operations work; path-based read can read the wrong file |
| `cad_tool_search` | query, family filter, risk filter, include-hardware, include-internal, no match | PARTIAL | Query/family work; empty-query include filters do not surface excluded tools |
| `cad_tool_help` | known tools, hardware tool, unknown tool | PARTIAL | Known tools work; unknown tool raises an uncaught exception |
| `cad_tool_run` | Yosys, Icarus, hardware/GUI/internal/unsafe blocks, outside cwd, nonzero exit | PASS | Execution and safety blocks work; blocked calls leak pending jobs |

## 4. Detailed results

### 4.1 `cad_status` — PASS

- `deep=true` returned all capabilities true and correctly reported that `bwrap` is unavailable in this WSL2 environment.
- It reported `tools_present = 155` and `tools_missing = []`.
- Hardware programming warning is accurate; no USB hardware was visible.

### 4.2 `cad_project` — PARTIAL

Verilog lifecycle works:

- `cad_project(action="inspect")` found `counter` as top, `tb_counter` as testbench, `clk`, and `pins.pcf`.
- `cad_project(action="init", write=true)` wrote `oss-cad-project.toml`.
- `cad_project(action="validate")` then passed with no warnings.
- A second init without `write=true` correctly returned `config_exists`.

VHDL discovery is incomplete:

- On `vhdl_pwm`, profile returned:
  - `entities`: `["pwm", "tb_pwm"]`
  - `top_candidates`: `[]`
  - `testbenches`: `[]`
  - `clocks`: `[]`
- `init` then wrote a config with `top = null`, `testbenches = []`, and **both** `rtl/pwm.vhd` and `tb/tb_pwm.vhd` in `sources`.
- `validate` still reported `Project validation passed`, so the missing top/testbench was not caught.

Edge case found:

- A project rooted under `build/e2e/...` was inspected as having zero HDL files. The project scanner excludes any path component named `build`, so exclusion is applied to absolute components rather than path components relative to the project root.

### 4.3 `cad_check` — PARTIAL

Passing cases:

- Verilog counter, engines `slang`, `verilator`, `iverilog`, level `lint`: `ok=true`, 0 errors.
- Verilog counter, `slang`, level `elaborate`: 0 errors, hierarchy root count 1.
- SystemVerilog FIFO, `slang` + `verilator`: 0 errors; only expected style warnings.
- VHDL PWM, `ghdl`: 0 errors.
- Negative design with `iverilog` + `slang`: `ok=false`, 3 normalized errors, including undeclared identifier and elaboration failures.

Failing case caused by a filelist bug:

A `cad_check` call using `source.filelist = build.f`, where `build.f` contained only `rtl/counter.v`, unexpectedly compiled `examples/blinky-ice40/rtl/blinky.sv` and `examples/blinky-ice40/tb/tb_blinky.sv` from the server workspace. It returned 12 errors that had nothing to do with the requested files.

Representative job: `01a1ca54-7a13-711e-be77-c2430f68ee6c`.

### 4.4 `cad_simulate` — PARTIAL

Passing cases:

| Case | Engine | Trace | Result |
|---|---|---|---|
| Verilog counter | Icarus | VCD | `TEST PASSED`, waveform 1,355 bytes |
| Verilog counter | Verilator | FST | `TEST PASSED`, FST 712 bytes |
| SystemVerilog FIFO | Icarus | VCD | `TEST PASSED`, waveform 2,859 bytes |
| SystemVerilog FIFO | Verilator | FST | `TEST PASSED`, FST 996 bytes |
| Negative compile | Icarus | none | Correctly failed with normalized diagnostics |

The Icarus counter run used plusargs and a seed successfully. Verilator runs correctly converted the VCD to FST.

VHDL simulation fails:

Both the first attempt and a clean reproduction failed with:

```
/home/chalk/Kits/oss-cad-suite/libexec/ghdl:error: file 'tb_pwm' does not exist
/home/chalk/Kits/oss-cad-suite/libexec/ghdl:error: Please elaborate your design.
```

The workflow runs `ghdl -e ... tb_pwm` with the server workspace as the working directory, so GHDL writes the executable `tb_pwm` into the workspace. It then runs `ghdl -r ... tb_pwm` from the job work directory, where that executable is not present. The failed run also left an executable named `tb_pwm` in the workspace root; this was cleaned up after testing.

Jobs: `01a1ca4a-5e79-72bd-b78e-8f4433879448`, `01a1ca4b-eb1c-7149-adf7-266dd149dd3a`.

Non-MCP toolchain note: Verilator 5.055 rejected `$fatal` in a `--binary` testbench (`Unsupported or unknown PLI call: '$fatal'`). Rewriting the failure branch to `$display("SIMULATION FAILED")` allowed the Verilator path to pass. This is toolchain behavior rather than an MCP bug, but it is worth documenting for testbench authors.

### 4.5 `cad_synthesize` — PARTIAL

Passing cases:

| Design | Target | Result |
|---|---|---|
| Verilog counter | iCE40, `flatten=true`, `abc9=true` | 11 cells; `SB_CARRY=2`, `SB_DFFESR=4`, `SB_LUT4=5` |
| SystemVerilog FIFO | iCE40 | 168 cells; netlist 537,476 bytes |
| SystemVerilog FIFO | ECP5 | 79 cells; netlist 1,014,942 bytes |
| Negative Verilog | generic | Correctly failed |

Failing cases:

**Generic target — always fails.**
`cad_synthesize` defaults to `target="generic"`. The workflow generates:

```
read_slang ...
synth -top counter -json /path/counter.json
```

Yosys's `synth` command does not accept `-json`; the run ended with:

```
Syntax error in command `synth -top counter -json ...`
```

Job: `01a1ca4c-2702-7444-a626-45679e6e59fd`.

**Filelist — unrelated files added.**
Using the filelist that contained only `rtl/counter.v`, the generated Yosys script contained:

```
read_slang ... counter.v examples/blinky-ice40/rtl/blinky.sv examples/blinky-ice40/tb/tb_blinky.sv
```

The filelist path itself was correct, but `resolve_sources` then fell back to workspace auto-discovery because `source.files` was empty. The job failed with 14 errors from the unrelated example files.

Job: `01a1ca4c-25c1-74e4-afba-5b50693c8268`.

**VHDL — always fails.**
The generated Yosys script used:

```
ghdl --std=08 /path/pwm.vhd
```

Yosys's GHDL plugin help says the file-import form is:

```
ghdl [options] files... -e [unit]
```

The missing `-e` produced:

```
error: bad unit name '/home/chalk/'
ERROR: vhdl import failed.
```

Job: `01a1ca4c-b05c-769b-9221-3adf63947dc7`.

A raw Yosys workaround with `-e pwm` succeeded and produced a 437,909-byte iCE40 netlist, confirming that the GHDL plugin itself is functional.

### 4.6 `cad_verify` — PASS

The SystemVerilog FIFO formal wrapper was used with `smtbmc`.

| Mode | Result | Properties |
|---|---|---|
| `prove` | PASS | 1 assertion PASS at depth 15; proof by k-induction |
| `bmc` | PASS | 1 assertion PASS at depth 15 |
| `cover` | PASS | 2 cover points reached at depth 1 |

An initial formal wrapper with an incorrect reset-state assertion correctly failed, showing that property failures and traces are reported rather than hidden. `equiv` without a configuration correctly returned `invalid_input`, matching the documented v0.1 limitation.

Passing jobs: `01a1ca4d-d8ef-716e-8e63-40ea17972d4e` (prove), `01a1ca4d-d8f1-70f6-8f04-b4e2aa375beb` (BMC), `01a1ca4d-d8f5-717a-ab80-2db2f6da9df0` (cover).

### 4.7 `cad_fpga_build` — PASS

| Build | Result |
|---|---|
| iCEBreaker / iCE40 UP5K, 48 MHz, PCF | PASS; 104,090-byte `design.bin`; timing met; 73.7 MHz; 8 logic cells used |
| ECP5 85K, CABGA381, speed 6, unconstrained, 25 MHz | PASS; 280,156-byte `design.bit`; timing met; 561.17 MHz reported |
| iCE40 without constraints | Correctly failed with `A physical constraint file is required for PnR.` |
| Unknown board preset | Correctly failed with `Unknown board preset` |
| iCE40 at 500 MHz | Failed at place-and-route with `pnr_failed`; useful failure rather than incorrect success |

Jobs: `01a1ca4d-236c-779d-b59f-3c9fbe287bc1` (iCE40), `01a1ca4d-2371-722f-96a0-2b84f3e684a4` (ECP5).

### 4.8 `cad_waveform` — PASS

All actions worked:

- `info` on VCD: 10 signals, 1 ps timescale, max time 216,000.
- `info` on FST: 9 signals, 1 ps timescale, max time 216,000.
- `signals` with an exact name returned the requested signal.
- `summarize` returned per-signal statistics.
- `sample` honored a 10 ns–50 ns window and returned 10 samples.
- `export` wrote a CSV artifact (`samples.csv`).
- `convert` VCD→FST and FST→VCD both produced registered artifacts.

Minor UX observation: the `signals` action's summary reports the total header signal count, not the number of selected signals, even though the returned `info.signals` list is filtered.

### 4.9 `cad_netlist` — PARTIAL

Working actions:

- `stats`, `modules`, `cells` with module/type filters, `nets` with name filter, and `diff` all returned structured data.
- `diff` correctly reported removed `SB_DFFE`/`SB_DFFSR` types and changed LUT/DFF/CARRY counts.
- Missing `name` for `fanout` and missing `other` for `diff` produced clean `invalid_input` errors.

Two issues:

**Fanout fabricates matches for nonexistent nets.**
`fanout` with `name="definitely_missing_net"` returned 10 matches, all with empty `bits` and `loads`, one per scanned module. It should return zero matches or an error. `fanout` with `name="full"` also returned mostly empty helper-module matches before useful data.

Jobs: `01a1ca4e-bb70-7481-909a-579dba0370c3` (missing net), `01a1ca4e-bb6d-7339-a2fd-e65bc594ddc9` (`full`).

**Internal modules inflate stats.**
The iCE40 counter netlist reported 61 modules and 229 cells, while synthesis reported 11 design cells. The extra modules are ABC9 helper modules (`$__ABC9_*`, `$__DFF_*`, `$paramod*`). This is technically accurate for the JSON file but not useful for design-level inspection.

### 4.10 `cad_timing` — PASS

- Parsed iCE40 nextpnr report: timing met, max 170.42 MHz, critical path details and source locations.
- Parsed ECP5 nextpnr report: timing met, max 561.17 MHz.
- Parsed icetime JSON: 13.57 ns / 73.70 MHz.
- Ran `icetime` directly on `design.asc`: 73.70 MHz, timing met.
- Missing device correctly returned `missing_device`.

Minor defect: passing an `.asc` file to `action="parse"` returned a hint telling the user to pass `driver='icetime'`. The schema has no `driver` field; it should say `action="icetime"`.

### 4.11 `cad_job` — FAIL (critical)

Working actions:

- `list` returned recent jobs.
- `status` and `wait` returned correct terminal state.
- `log` returned stdout and `events` streams.
- `rerun` returned the documented `not_supported` error.
- Unknown job returned `job_not_found`.

**Cancelling a running job crashes the server.**
A long-running Icarus simulation was started, then cancelled:

- `cad_job(action="cancel")` returned `ok=true` and job status `cancelled`.
- The original `cad_simulate` call then failed with `MCPError: Connection closed`.
- The integrated OpenCode MCP connection was lost with `MCP server process exited with code 1`.
- A fresh standalone stdio server reproduced the crash with job `01a1ca53-33c9-7259-9952-ce7902569878`.
- The server stderr ended in `ExceptionGroup: unhandled errors in a TaskGroup` / `BrokenResourceError`.

The likely cause is that `JobManager.execute` re-raises `asyncio.CancelledError` when `JobManager.cancel` cancels `job.task`; the MCP request handler is then cancelled and the stdio session closes.

### 4.12 `cad_artifact` — PARTIAL

Working actions:

- `list` for the iCE40 build job returned 4 artifacts: netlist, synthesis stats, bitstream, and nextpnr report.
- `stat` returned correct metadata.
- `hash` matched the artifact SHA-256.
- `read_text` by artifact ID read the requested byte range and reported truncation correctly.
- Artifacts were restored correctly after server restart from manifests.

Two issues:

**Path is ignored when artifacts exist.**
A call with `action="read_text"` and:

```json
{"path": "build/synth/.../counter.stat.json", "max_bytes": 120}
```

returned the beginning of a converted VCD file from a waveform job, not the requested synthesis JSON. The job field also pointed at the waveform job. `_find_artifact` selects the most recent artifact before the code reaches the explicit `request.path` fallback, so the user-supplied path is never used.

**Default list can show an empty job.**
`cad_artifact(action="list")` without `job_id` picked the most recent job, which was a cancelled simulation with zero artifacts, even though earlier jobs had artifacts. A user would have to know which job ID to pass.

### 4.13 `cad_tool_search` — PARTIAL

Working cases:

- `query="formal"` found `sby`, `yosys`, AIGER tools, `eqy`, `mcy`, and `pono`.
- `family="formal"` returned formal-family entries.
- `query="openFPGA"` with `include_hardware=true` found `openFPGALoader`.
- `query="verilator_bin"` with `include_internal=true` found the internal helper.
- A nonexistent query returned an empty list.

Failing case:

- `risk="hardware", include_hardware=true` returned **0 tools**.
- `include_hardware=true` with an empty query returned only agent-facing tools, not hardware tools.
- `include_internal=true` with an empty query did the same for internal tools.

The search scoring gives non-agent-facing entries score 0 when the query is empty, so they can never pass the `if score:` filter even when their inclusion flag is set.

### 4.14 `cad_tool_help` — PARTIAL

Working cases:

- `verilator` returned version, usage, examples, risk, and help text.
- `openFPGALoader` returned curated summary, version, help, hardware risk, and the hardware-disabled note.
- `yosys` returned version and recommended high-level tool.

Failing case:

- `tool="does-not-exist"` raised an uncaught `ToolNotFound`, logged a traceback, and returned a generic MCP `is_error=true` result (`Error executing tool cad_tool_help`) instead of a structured error.

### 4.15 `cad_tool_run` — PASS (with job-state leak)

Working cases:

- `yosys -V` returned version and created a passed job.
- `iverilog -V` returned version output.
- `openFPGALoader --detect` blocked with `hardware_disabled`.
- `verilator_bin --version` blocked with `internal_tool`.
- `gtkwave` blocked with `gui_not_supported`.
- `yosys -p shell` blocked with `unsafe_command`.
- `cwd="/tmp"` rejected with `path_not_allowed`.
- `iverilog` with no source returned `exit_code=1` and `tool_failed`.

Issue: every blocked call created a job through `app.jobs.create("tool")` and returned without running or finalizing it. Five such jobs remained permanently `pending`, with no steps and no terminal status:

`01a1ca51-ed37-7327-8540-e1f20904bddf`, `01a1ca51-ed3c-72a7-b9ee-1dda8e73b76f`, `01a1ca51-edfd-71fa-93e9-23a350495eb1`, `01a1ca51-ee36-7151-a23b-7d0af768e01a`, `01a1ca51-ef3d-711f-afa4-5baa9ee4bf3c`.

## 5. Defect register

| ID | Severity | Tool | Defect | Evidence |
|---|---|---|---|---|
| D1 | Critical | `cad_job` | Cancelling a running job crashes the server | `01a1ca53-33c9-7259-9952-ce7902569878`; integrated connection `BrokenResourceError` |
| D2 | Critical | `cad_simulate` | VHDL simulation always fails | `01a1ca4a-5e79...`, `01a1ca4b-eb1c...`; executable in wrong cwd |
| D3 | High | `cad_synthesize` | Default `generic` target always fails | `01a1ca4c-2702...`; `synth ... -json` syntax error |
| D4 | High | `cad_synthesize`, `cad_check` | Filelist silently adds unrelated workspace HDL | `01a1ca4c-25c1...`, `01a1ca54-7a13...`; example blinky files appeared |
| D5 | High | `cad_synthesize` | VHDL synthesis always fails | `01a1ca4c-b05c...`; Yosys GHDL `bad unit name` |
| D6 | High | `cad_artifact` | Path-based read ignores `path` and reads another artifact | `read_text` path returned converted VCD from `01a1ca4e-93f8...` |
| D7 | Medium | `cad_netlist` | `fanout` fabricates empty matches for missing nets | `01a1ca4e-bb70...` |
| D8 | Medium | `cad_tool_search` | Empty-query include filters cannot surface hardware/internal tools | `risk="hardware"` returned 0 |
| D9 | Medium | `cad_tool_run` | Blocked calls leave permanently pending jobs | five `01a1ca51-*` jobs |
| D10 | Medium | `cad_project` | VHDL top/testbench/clock discovery incomplete; init emits unusable config | `vhdl_pwm` profile and generated config |
| D11 | Medium | `cad_tool_help` | Unknown tool raises uncaught exception | `does-not-exist` |
| D12 | Low | `cad_project` | Project under `build/...` reports zero HDL files | initial `build/e2e` inspect |
| D13 | Low | `cad_artifact` | Default `list` picks latest job, not latest artifact-bearing job | cancelled simulation with 0 artifacts |
| D14 | Low | `cad_timing` | `.asc` misuse hint references nonexistent `driver` parameter | error message |
| D15 | Low | `cad_netlist` | Internal ABC9 modules inflate module/cell stats | 61 modules / 229 cells for an 11-cell design |

## 6. Evaluation

### Strengths

- The intent-level tool surface is coherent. The separation between workflows, jobs, artifacts, catalog search/help, and raw execution is a good design.
- Normal Verilog and SystemVerilog flows work end to end and produce genuinely useful structured data.
- FPGA builds are real: the iCE40 and ECP5 paths produced valid bitstreams, timing reports, and utilization data.
- Formal verification is well integrated; prove/BMC/cover and counterexample traces work.
- Waveform support is practical: inspect, sample, summarize, CSV export, and VCD/FST conversion all work.
- Error handling is generally good in the normal preflight paths: path policy, missing constraints, unknown board, disabled hardware, GUI blocking, and unsafe Yosys commands all returned useful codes and hints.
- Job manifests and artifacts survive a server restart.
- The existing 40-test suite passes.

### Weaknesses

- The advertised VHDL support is broken at both simulation and synthesis levels, despite `cad_status` reporting `vhdl=true`.
- Cancellation is unsafe and can take down the entire MCP server, which is a serious robustness problem for an agent-facing tool.
- Filelists are not treated as authoritative source sets, which can silently mix unrelated modules into a build.
- Several "read/inspect" tools return misleading results rather than errors (`cad_artifact` path, `cad_netlist fanout`).
- Discovery and error handling have several polish gaps that will confuse agents: empty-query search filters, unknown tool help, permanently pending blocked jobs, stale artifact-list selection, and a wrong hint parameter.
- The existing tests do not cover the failing VHDL, generic-target, filelist, cancellation, or artifact-path cases.

## 7. Prioritized suggestions

### P0 — fix before release

1. **Make job cancellation non-fatal.**
   - Catch `asyncio.CancelledError` in `JobManager.execute` when the cancel came from `cad_job`.
   - Return a normal `Job`/tool result with status `cancelled` and a structured `job_cancelled` error.
   - Keep server-shutdown cancellation distinct from user cancellation.
   - Add an integration test that starts a long simulation, cancels it, and then calls `cad_status` on the same session.

2. **Fix GHDL simulation.**
   - Use `ghdl -e ... -o <workdir>/sim <top>` and run `<workdir>/sim` from the work directory, or run `ghdl -r` from the directory where `-e` created the executable.
   - Avoid writing the simulation executable into the workspace root.
   - Add a VHDL simulation integration test.

3. **Fix generic synthesis.**
   - Do not append `-json` to Yosys's `synth` command.
   - Use `synth -top ...` followed by `write_json <path>`, or an equivalent script.
   - Add a default-target synthesis test.

4. **Fix VHDL synthesis.**
   - Append `-e <top>` (or the correct GHDL plugin import form) to the generated `ghdl` command.
   - Keep the raw workaround as a regression test; a `-e pwm` run produced a valid 437,909-byte iCE40 netlist.

5. **Fix filelist resolution.**
   - If a filelist produces files, do not auto-discover workspace sources afterward.
   - Treat `source.files`, `source.filelist`, and project sources as mutually scoped inputs, not cumulative fallbacks.
   - Add a test where the workspace contains an unrelated HDL example.

### P1 — correctness and discoverability

6. **Fix `cad_artifact` path precedence.**
   - If `request.path` is supplied and no explicit `artifact_id` is supplied, read that path directly.
   - Do not attach an unrelated job to a path-based read.

7. **Fix `fanout`.**
   - Skip modules with no matching net names.
   - Return an empty match list or a clear `not_found` result for a missing net.

8. **Fix search include filters.**
   - When `include_hardware` or `include_internal` is true and the query is empty, give those entries a nonzero score.
   - Consider making `total` the pre-limit match count, not the returned page size.

9. **Finalize blocked raw-tool jobs.**
   - Mark blocked `cad_tool_run` attempts as `failed`/`blocked`, or create a lightweight job reference that is not persisted.
   - On startup, do not leave old `pending` jobs pending indefinitely.

10. **Improve VHDL project discovery.**
    - Parse VHDL entities and architectures, not only Verilog `module` declarations.
    - Recognize `rising_edge(clk)` / `falling_edge(clk)` as clock usage.
    - Detect `tb_*` entities as testbenches and exclude them from default synthesis sources.
    - Make `validate` fail when a project has HDL but no discoverable top.

11. **Handle unknown tools in `cad_tool_help`.**
    - Catch `ToolNotFound` and return a structured error, or intentionally raise a typed MCP error with a useful hint.

### P2 — polish

12. Make `cad_artifact(action="list")` find the most recent job with artifacts when no job is specified.
13. Fix the `cad_timing` hint to use `action="icetime"`.
14. Filter internal `$__ABC9_*` / `$paramod*` modules from `cad_netlist` stats and modules by default, or add an `include_internal` option.
15. Make the `signals` action summary report selected signals rather than total header signals.
16. Apply the project-root exclusion relative to the root, not to all absolute path components, so projects under a directory named `build` are not silently ignored.
17. Document the Verilator `$fatal` limitation or provide a testbench guidance note.

## 8. Reproduction commands

The fixtures live under:

```
/home/chalk/.cache/oss-cad-mcp/e2e/
```

The standalone MCP client used after the integrated server crash is:

```
/tmp/opencode/oss-cad-test-client.py
```

Representative reproductions:

```bash
# VHDL simulation failure
.venv/bin/python /tmp/opencode/oss-cad-test-client.py cad_simulate \
  '{"request":{"source":{"files":["/home/chalk/.cache/oss-cad-mcp/e2e/vhdl_pwm/rtl/pwm.vhd","/home/chalk/.cache/oss-cad-mcp/e2e/vhdl_pwm/tb/tb_pwm.vhd"],"language":"vhdl"},"top":"tb_pwm","engine":"ghdl","trace":"vcd"}}'

# Generic synthesis failure
.venv/bin/python /tmp/opencode/oss-cad-test-client.py cad_synthesize \
  '{"request":{"source":{"files":["/home/chalk/.cache/oss-cad-mcp/e2e/verilog_counter/rtl/counter.v"],"language":"verilog"},"top":"counter","target":"generic"}}'

# Filelist contamination
.venv/bin/python /tmp/opencode/oss-cad-test-client.py cad_check \
  '{"request":{"source":{"filelist":"/home/chalk/.cache/oss-cad-mcp/e2e/verilog_counter/build.f","language":"verilog"},"top":"counter","level":"lint","engines":["iverilog"]}}'

# Cancellation crash
.venv/bin/python /tmp/opencode/repro_cancel.py
```

## 9. Conclusion

The server has a solid core and genuinely useful HDL workflows for Verilog/SystemVerilog on iCE40 and ECP5. The tool design, structured reports, artifact model, and formal integration are all worth keeping.

At the baseline, the server should have been considered **experimental** because two advertised languages/paths failed outright (VHDL simulation and synthesis), the default synthesis target was broken, filelists could silently corrupt source resolution, and a routine job cancellation could terminate the MCP server. Those issues were repaired in the post-fix pass documented in Section 10, which moves the project from a promising prototype toward a credible v0.1.

## 10. Post-fix validation — 2026-11-11

The defects in this report were repaired after the baseline was tagged
`pre-fix-e2e-baseline-20261111`. The repair plan is in
`internalDocs/oss-cad-mcp-repair-plan.md`.

### 10.1 Repair status

| ID | Status | Fix / verification |
|---|---|---|
| D1 | Fixed | Running-job cancellation returns `job_cancelled`; server remains alive |
| D2 | Fixed | GHDL elaborates to a job-local executable and runs it there; VHDL simulation + VCD pass |
| D3 | Fixed | Generic synthesis uses `synth` + `write_json`; generic netlist produced |
| D4 | Fixed | A filelist now suppresses workspace auto-discovery; filelist-only check passes |
| D5 | Fixed | Generated Yosys GHDL command now includes `-e <top>`; VHDL iCE40 synthesis passes |
| D6 | Fixed | Explicit `path` takes precedence over implicit artifact lookup |
| D7 | Fixed | `fanout` skips modules with no matching nets; missing net returns zero matches |
| D8 | Fixed | Empty-query hardware/internal include flags now surface those entries |
| D9 | Fixed | Rejected `cad_tool_run` calls create terminal `failed` jobs, not `pending` jobs |
| D10 | Fixed | VHDL entities, testbenches, and `rising_edge` clocks are detected; init writes usable config |
| D11 | Fixed | Unknown `cad_tool_help` returns structured `tool_not_found` |
| D12 | Fixed | Project exclusion is relative to the project root; projects under `build/` work |
| D13 | Fixed | Default artifact listing selects the most recent job that has artifacts |
| D14 | Fixed | `.asc` hint now names `action="icetime"` |
| D15 | Fixed | Yosys internal `$...` modules are hidden by default; `include_internal=true` exposes them |

### 10.2 Post-fix verification

- `ruff format --check src tests`: **63 files already formatted**
- `ruff check src tests`: **All checks passed**
- `pyright`: **0 errors, 0 warnings**
- `pytest -q`: **53 passed**
- Real-project smoke checks:
  - GHDL VHDL simulation produced `wave.vcd` and passed.
  - VHDL iCE40 synthesis produced a 437,909-byte netlist with 40 cells.
  - Generic Verilog synthesis produced a 7,506-byte netlist with 10 cells.
  - Filelist-only Verilog lint returned 0 errors.
  - Cancelling a running simulation returned a normal `job_cancelled` result; a subsequent `cad_status` succeeded.
  - Path-based artifact read returned the requested synthesis JSON.
  - Missing-net fanout returned 0 matches.
  - Empty-query hardware search returned hardware tools.
  - Unknown tool help returned a structured `tool_not_found`.
  - Rejected hardware raw-tool call produced a terminal `failed` job.

The server now passes the repaired paths that previously failed. The remaining
limitations listed in the README (hardware programming, EQY/MCY configuration
requirements, mixed-language simulation, GHW analysis) are intentional v0.1
scope limits rather than regressions.