# oss-cad-mcp

An intent-level [Model Context Protocol](https://modelcontextprotocol.io/) server for
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build). It exposes HDL workflows
— lint, simulate, synthesize, formally verify, build iCE40/ECP5 bitstreams, inspect
waveforms and netlists — instead of 155 raw CLI binaries.

## Status: v0.1, software-only

- Verilog / SystemVerilog / VHDL lint, elaboration, simulation, and synthesis
  (VHDL synthesis uses the Yosys GHDL plugin).
- SBY formal proof, BMC, and cover for Verilog/SystemVerilog; EQY with an existing config.
- iCE40 and ECP5 builds: synth → place-and-route → pack → timing.
- VCD/FST waveform inspection and Yosys JSON netlist inspection.
- A catalog, curated help, and an opt-in raw dispatcher for the rest of the suite.

**Hardware programming is disabled.** Programmers such as `openFPGALoader`, `openocd`,
`iceprog`, and `fujprog` are cataloged but not exposed as MCP actions.

## Security model

- Workflow paths are confined to the workspace (plus suite/cache read roots). This covers
  source globs, filelist entries, nested `-f` filelists, `+incdir+`, and include dirs.
- `cad_tool_run` is off by default (`allow_raw = false`). When enabled, path-like argv
  entries are checked against writable roots; absolute paths, `..` escapes, and embedded
  `-p "read_verilog /tmp/..."` paths are rejected. This is an argv check, not a sandbox.
- `bwrap` is unavailable in the verified WSL2 environment, so raw tools run unsandboxed.
  Treat RTL, testbenches, filelists, and raw argv as trusted input.

## Platform support

| Platform | Status |
|---|---|
| Ubuntu 26.04 on WSL2 (Windows 11), x86_64 | **Verified** |
| Native Linux, x86_64 | Expected to work; **not verified** |
| macOS (Intel or Apple Silicon) | **Experimental; not verified** |
| Native Windows | **Not supported; use WSL2** |

Verified configuration: Ubuntu 26.04.1 LTS on WSL2 with OSS CAD Suite 2026-11-05.

WSL2 notes:

- Keep projects on the Linux filesystem (`~/...`), not `/mnt/c`.
- MCP tools never block on a GUI; GUI viewers are catalog-only.
- USB passthrough and hardware programming are out of scope.
- `doctor --deep` reports that `bwrap` sandboxing is unavailable.

## Install

Requirements: Python 3.14, [`uv`](https://docs.astral.sh/uv/), an extracted OSS CAD Suite,
a host C++ compiler and `make` for Verilator, and `perl-doc` for Verilator's help output.

```bash
git clone <repository> oss-cad-mcp
cd oss-cad-mcp
uv sync
export OSS_CAD_SUITE_ROOT="$HOME/Kits/oss-cad-suite"
uv run oss-cad-mcp doctor
```

`doctor` reports the suite version, workspace, capabilities, host toolchain, and platform
verification status.

## MCP client configuration

The server speaks stdio by default:

```json
{
  "mcpServers": {
    "oss-cad": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/oss-cad-mcp", "oss-cad-mcp", "serve"],
      "env": {
        "OSS_CAD_SUITE_ROOT": "/home/you/Kits/oss-cad-suite",
        "OSS_CAD_MCP_WORKSPACE": "/home/you/projects/my-fpga-project"
      }
    }
  }
}
```

The workspace defaults to the server's current directory. Use the equivalent local-MCP
entry for your client.

### OpenCode

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "oss-cad": {
        "type": "local",
        "command": ["/path/to/oss-cad-mcp/.venv/bin/oss-cad-mcp", "serve"],
        "environment": {"OSS_CAD_SUITE_ROOT": "/path/to/oss-cad-suite"}
      }
    }
  }
}
```

Or register globally:

```sh
opencode mcp add oss-cad --global \
  --env OSS_CAD_SUITE_ROOT="$HOME/Kits/oss-cad-suite" \
  -- /path/to/oss-cad-mcp/.venv/bin/oss-cad-mcp serve
```

OpenCode starts the server in each workspace, so `cad_status.workspace` follows the
project being edited.

## Tool surface

### Workflows

| Tool | Purpose |
|---|---|
| `cad_status` | Suite discovery, versions, capabilities, host toolchain |
| `cad_project` | Inspect, initialize, or validate a project |
| `cad_check` | Parse, lint, or elaborate with slang / Verilator / Icarus / GHDL |
| `cad_simulate` | Compile and run a testbench, returning diagnostics and waveform |
| `cad_synthesize` | Yosys synthesis with structured cell statistics |
| `cad_verify` | SBY proof/BMC/cover; EQY with an existing config |
| `cad_fpga_build` | iCE40/ECP5 synth → PnR → pack → timing |
| `cad_waveform` | VCD/FST info, signals, sample, summarize, export, convert |
| `cad_netlist` | Yosys JSON stats, modules, cells, nets, fanout, diff |
| `cad_timing` | Parse nextpnr/icetime reports or run icetime on `.asc` |

### Jobs, artifacts, discovery

| Tool | Purpose |
|---|---|
| `cad_job` | List, status, wait, log, cancel jobs |
| `cad_artifact` | List, stat, read text, hash artifacts |
| `cad_tool_search` | Search the full 155-binary catalog |
| `cad_tool_help` | Curated help, version, examples, and risk notes |
| `cad_tool_run` | Raw suite binary escape hatch (disabled by default) |

Tools take a single nested `request` object matching their published schema. Example:

```json
{
  "request": {
    "source": {"files": ["rtl/top.sv"], "language": "systemverilog"},
    "top": "top",
    "engines": ["slang", "verilator"]
  }
}
```

`cad_check(level="parse")` uses `slang --parse-only` or GHDL `-s` where supported;
Verilator and Icarus still compile. `cad_netlist` hides cell-library/blackbox modules by
default. Timed-out simulations are reported as `timeout`, and oversized waveforms are not
registered as artifacts.

### Catalog coverage

All `<suite>/bin` binaries are cataloged. Internal helpers, GUI tools, and hardware
programmers are not agent-facing by default; `cad_tool_search` can include them for
inspection. `cad_tool_run` blocks hardware, GUI, and internal tools in v0.1.

## Common workflows

```jsonc
// Lint / elaborate
{"request": {"source": {"files": ["rtl/top.sv"], "language": "systemverilog"},
             "top": "top", "level": "lint", "engines": ["slang", "verilator"]}}

// Simulate
{"request": {"source": {"files": ["rtl/top.sv", "tb/tb_top.sv"]},
             "top": "tb_top", "engine": "verilator", "trace": "fst"}}

// Synthesize
{"request": {"source": {"files": ["rtl/top.sv"]}, "top": "top", "target": "ice40"}}

// Build a software-only bitstream
{"request": {"source": {"files": ["rtl/top.sv"]}, "top": "top",
             "board": "icebreaker", "constraints": ["pins.pcf"], "frequency_mhz": 48}}

// Prove an assertion testbench
{"request": {"mode": "prove", "source": {"files": ["formal/prove.sv"]},
             "top": "testbench", "depth": 20}}
```

## Project configuration

`cad_project(action="inspect")` discovers files, top candidates, testbenches, clocks, and
constraint files. For deterministic workflows, add `oss-cad-project.toml`:

```toml
[project]
name = "blinky"
top = "top"
sources = ["rtl/**/*.sv"]
testbenches = ["tb/**/*.sv"]
include_dirs = ["rtl/include"]
defines = { SYNTHESIS = "1" }
language = "systemverilog"
std = "1800-2017"

[target]
board = "icebreaker"
family = "ice40"
device = "up5k"
package = "sg48"
speed = "6"
frequency_mhz = 48
constraints = ["pins.pcf"]

[sim]
engine = "auto"
testbench = "tb_top"
trace = "auto"

[formal]
engine = "smtbmc"
depth = 20
mode = "prove"
top = "top_formal"
sources = ["formal/top_formal.sv", "rtl/top.sv"]
```

Board presets in v0.1: iCEBreaker, TinyFPGA BX, iCEstick, ULX3S 45F/85F, and the ECP5
evaluation board. Target precedence is request > project `[target]` > board preset >
built-in default. An explicit request `board` is authoritative. `allow_unconstrained=true`
always passes the nextpnr allow-unconstrained option, even with constraints present.

`constraints` accepts one PCF/LPF per build; passing multiple files is rejected. Project
`[formal]` settings supply defaults for `cad_verify`, and `formal.top`/`formal.sources`
make a formal harness config-driven. `cad_synthesize` uses `[target].family` when the
request omits `target`.

VHDL files are compiled in lexicographic order; name packages so they sort before their
entities (`01_pkg.vhd`, `02_entity.vhd`). Header files (`.vh`, `.svh`) are listed as HDL
files but are not compilation units or top candidates.

## Server configuration

Settings come from `~/.config/oss-cad-mcp/config.toml`, `OSS_CAD_MCP_*` environment
variables, and CLI options (CLI > env > TOML > defaults):

```toml
suite_root = "/home/you/Kits/oss-cad-suite"
workspace = "/home/you/projects/blinky"
profile = "core"
allow_raw = false       # raw execution is opt-in
allow_unsafe = false
max_jobs = 4
default_timeout_s = 300
max_output_bytes = 65536
max_waveform_bytes = 268435456
```

`max_waveform_bytes` caps registered waveform artifacts (default 256 MiB; `0` disables).
Oversized waveforms remain in the job work directory.

Useful commands:

```bash
uv run oss-cad-mcp doctor
uv run oss-cad-mcp doctor --deep --json
uv run oss-cad-mcp catalog --family formal
uv run oss-cad-mcp tool-help verilator
uv run oss-cad-mcp serve --transport stdio
```

## Updating OSS CAD Suite

The updater verifies the release SHA-256, extracts to staging, runs `yosys -V`, and swaps
atomically. The previous installation is kept as a timestamped backup by default.

```bash
./scripts/update-oss-cad-suite.sh ~/Kits/oss-cad-suite
./scripts/update-oss-cad-suite.sh --check ~/Kits/oss-cad-suite
./scripts/update-oss-cad-suite.sh --no-backup ~/Kits/oss-cad-suite
uv run oss-cad-mcp update-suite ~/Kits/oss-cad-suite
```

Restart the MCP client/server after an update. Do not run the updater while a build is
active. Native Windows is rejected; WSL2 uses the `linux-x64` asset.

## Architecture

- **Workflows** compose suite binaries into validated steps.
- **Parsers** normalize diagnostics, Yosys stats, SBY statuses, nextpnr/icetime, and VCD/FST.
- **Job manager** records argv, environment, timings, logs, manifests, and artifacts.
- **Catalog** covers every suite binary; high-level tools cover common goals.
- **Process boundary** runs suite binaries with the suite environment and parses outputs.

Generated files go to `build/`; job state and logs go to `.oss-cad-mcp/runs/`.

## Limitations in v0.1

- No FPGA programming or hardware debug; no cocotb, UVM, or mixed-language simulation.
- Gowin/Nexus/GateMate/MachXO2 build adapters are unverified; unsupported Yosys targets
  return `unsupported_target`. Use `cad_synthesize` plus `cad_tool_run` for those targets.
- EQY requires an existing `.eqy` config. `cad_verify(mode="mutation")` returns
  `not_supported`; drive `mcy init`/`run` through `cad_tool_run` if needed.
- GHW waveform analysis is unsupported; ask GHDL for VCD. `cad_artifact(read_text)` rejects
  binary artifacts; use `stat`/`hash` for bitstreams.
- GHDL `-s` still performs some semantic checks; it is not purely syntactic.
- `cad_verify(mode="cover")` fails when any cover is unreachable. Read cover statuses
  separately from assertion failures.
- Timing-failed builds are reported as `timing_failed`, not `pnr_failed`. A bitstream from
  `allow_timing_fail=true` is not trustworthy for hardware.
- `cad_job(action="rerun")` is not implemented; re-submit the original workflow.
- macOS and native Linux are not verified for full support.
- Simulation executes project code; treat RTL/testbenches as trusted input.

## Seed contract

`cad_simulate(seed=N)` passes `+seed=N` to Icarus and Verilator (matching
`$value$plusargs("seed=%d", ...)`) and additionally passes `+verilator+seed+N` for
Verilator's runtime RNG. If `plusargs` already has `+seed=`, the explicit plusarg wins and
`seed` is ignored with a warning. GHDL has no standard plusarg equivalent; `seed` is
ignored with a warning. Repeated runs with the same engine and seed are deterministic.

## Development

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run pytest
```

Conventional Commits are used. The suite includes parser, command-builder, path/security,
and integration tests; integration tests run against an installed suite when
`OSS_CAD_SUITE_ROOT` is available.

## License

MIT. OSS CAD Suite is not bundled or redistributed; each component retains its own license.