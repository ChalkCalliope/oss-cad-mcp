# Repair Plan — OSS CAD MCP E2E Defects

**Baseline tag:** `pre-fix-e2e-baseline-20261111`
**Report:** `internalDocs/oss-cad-mcp-e2e-test-report.md`
**Scope:** Fix the reproducible defects D1–D15 from the report, add regression tests, and re-run the full suite.

## Guiding constraints

- Preserve the existing public tool names and request shapes where possible.
- Prefer workflow-level fixes over raw-command workarounds.
- Do not weaken path/safety checks.
- Add a regression test for every fixed defect.
- Re-run `ruff`, `pyright`, unit tests, and integration tests after changes.
- Verify the key real-project flows again with Verilog, SystemVerilog, and VHDL.

## Step 1 — Job cancellation lifecycle (D1)

- Add an explicit per-job cancellation flag.
- Make `JobManager.execute` return the cancelled job instead of re-raising `CancelledError` when the cancellation was requested through `cad_job`.
- Keep shutdown/client cancellation propagating.
- Return a structured `job_cancelled` tool error from workflow wrappers.
- Regression test: start a sleeping job, cancel it, await the original execute task, then call another tool on the same app.

## Step 2 — GHDL simulation (D2)

- Elaborate GHDL into the job work directory with `-o`.
- Run the produced executable directly from that directory with `--vcd`.
- Avoid leaving `tb_*` binaries in the workspace root.
- Suppress the Verilog-only `$dumpfile` warning for GHDL.
- Regression test: VHDL entity + testbench, `cad_simulate(engine="ghdl")`, assert pass and VCD artifact.

## Step 3 — Synthesis correctness (D3, D4, D5)

- Generic target: run `synth` without `-json`, then `write_json`.
- Filelist: treat a filelist as an explicit source set; do not auto-discover project/workspace HDL after a successful filelist parse.
- VHDL: append `-e <top>` to the Yosys GHDL command.
- Regression tests: default/generic synthesis, filelist-only resolution, VHDL synthesis.

## Step 4 — Artifact resolution (D6, D13)

- Prefer an explicit `path` over implicit artifact lookup.
- Keep artifact-ID lookup unchanged.
- Make `list` without a job choose the most recent job that actually has artifacts.
- Regression test: create two artifacts, request one by path, assert the returned text belongs to that path.

## Step 5 — Netlist inspection (D7, D15)

- Skip modules/fanout groups that contain no matching nets.
- Add `include_internal` (default `false`) and filter Yosys internal `$...` modules from stats and searches.
- Regression tests: missing fanout net returns zero matches; internal helper modules are hidden by default and visible when requested.

## Step 6 — Discovery/help/raw jobs (D8, D9, D11)

- Allow `cad_tool_search` to surface hardware/internal entries when their include flags are set and the query is empty.
- Return a structured error from `cad_tool_help` for unknown tools.
- Create terminal failed/blocked jobs for rejected `cad_tool_run` calls instead of leaving `pending` records.
- Regression tests for all three.

## Step 7 — Project discovery (D10, D12)

- Parse VHDL entities for top candidates and testbenches.
- Detect VHDL `rising_edge`/`falling_edge` clocks.
- Generate config with RTL in `sources`, testbench paths in `testbenches`, and `sim.testbench` set.
- Apply directory exclusions relative to the project root, not absolute path components.
- Make `validate` check configured top against modules and entities, and warn when no top is detected.
- Regression tests: VHDL project profile/init, project rooted under `build/`.

## Step 8 — Timing hint (D14)

- Change the `.asc` misuse hint from `driver="icetime"` to `action="icetime"`.
- Add a focused assertion in the timing workflow test.

## Step 9 — Validation

- Run `ruff check`, `ruff format --check`, `pyright`, and `pytest`.
- Rebuild the Verilog iCE40 and ECP5 projects.
- Re-run VHDL simulation, VHDL synthesis, generic synthesis, filelist check, formal prove/BMC/cover, artifact path reads, cancellation, and search/help/raw-tool checks.
- Update this plan with completion status and append a short fix-validation note to the report.

## Acceptance criteria

- No `pending` jobs created by rejected raw-tool calls.
- Cancelling a running job returns a normal `job_cancelled` result and the server remains usable.
- VHDL simulation, VHDL synthesis, generic synthesis, and filelist-only builds pass.
- `cad_artifact(path=...)` reads exactly that path.
- `fanout` returns zero matches for an absent net.
- Search include filters work with empty queries.
- Unknown tool help returns a structured error.
- VHDL project init produces a usable config and validation passes.
- Existing test suite plus new regression tests pass.
- `ruff` and `pyright` pass.

## Completion status

All planned repair steps were completed on 2026-11-11.

| Step | Status | Validation |
|---|---|---|
| 1. Cancellation lifecycle | Done | Running job cancels to `job_cancelled`; server stays alive |
| 2. GHDL simulation | Done | VHDL testbench passes and produces a VCD |
| 3. Synthesis correctness | Done | Generic, filelist, and VHDL synthesis verified |
| 4. Artifact resolution | Done | Path-based reads return the requested file |
| 5. Netlist inspection | Done | Missing fanout returns zero matches; internal modules hidden by default |
| 6. Discovery/help/raw jobs | Done | Search filters, structured help errors, terminal blocked jobs |
| 7. Project discovery | Done | VHDL top/testbench/clock detection and `build/` root handling |
| 8. Timing hint | Done | Hint now names `action="icetime"` |
| 9. Validation | Done | `ruff`, `pyright`, and 53 tests pass; real-project smoke checks pass |

### Validation commands

```text
ruff format --check src tests   # 63 files already formatted
ruff check src tests           # All checks passed
pyright                        # 0 errors, 0 warnings
pytest -q                      # 53 passed
```

### Regression coverage added

- Job cancellation returns normally and the app remains usable.
- GHDL simulation produces a VCD.
- VHDL synthesis produces an iCE40 netlist.
- Generic synthesis produces a netlist.
- Filelists remain explicit source sets.
- Artifact path reads take precedence over implicit artifacts.
- Default artifact listing selects a job with artifacts.
- Fanout returns no matches for an absent net.
- Internal netlist modules are hidden by default.
- Empty-query search includes requested hardware/internal entries.
- Unknown tool help returns a structured `tool_not_found`.
- Blocked raw-tool calls create terminal failed jobs.
- VHDL project discovery/init/validate works.
- Projects rooted under `build/` are inspected correctly.