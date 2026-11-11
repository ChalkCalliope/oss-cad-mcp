# oss-cad-mcp

An intent-level [Model Context Protocol](https://modelcontextprotocol.io/) server for
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build). It gives an AI agent
high-level HDL workflows — lint, simulate, synthesize, formally verify, build FPGA
bitstreams, inspect waveforms and netlists — instead of exposing 155 raw CLI binaries.

## Status: v0.1, software-only

This first version deliberately focuses on **software flows**:

- SystemVerilog / Verilog lint, elaboration, simulation, synthesis, formal verification
- iCE40 and ECP5 software builds (synth → place-and-route → pack → timing)
- VCD/FST waveform inspection and Yosys JSON netlist inspection
- A catalog and raw dispatcher that cover the rest of the suite

**Hardware programming is disabled.** Tools such as `openFPGALoader`, `openocd`, `iceprog`,
and `fujprog` are cataloged but not exposed as MCP actions.

## Platform support

| Platform | Status |
|---|---|
| Ubuntu 26.04 on WSL2 (Windows 11), x86_64 | **Verified** |
| Native Linux, x86_64 | Expected to work; **not verified** |
| macOS (Intel or Apple Silicon) | **Not verified for full support; experimental** |
| Native Windows | **Not supported; use WSL2** |

**macOS support has not been verified for full support.** No macOS machine was available
during development; treat macOS as experimental and best-effort. Native Windows is not
supported. Use WSL2 for Windows. The verified configuration is Ubuntu 26.04.1 LTS on
WSL2 with the OSS CAD Suite 2026-11-05 release.

### WSL2 notes

- Keep projects on the Linux filesystem (`~/...`), not `/mnt/c`. The 9p mount is slow and
  has different executable-bit and symlink behavior.
- WSLg is enabled here, so GUI viewers may work, but MCP tools never block on a GUI.
- USB passthrough is not part of v0.1 because hardware programming is out of scope.
- `bwrap` sandboxing is unavailable in this WSL2 environment because user namespaces
  cannot be created. The server reports this via `doctor --deep` and runs unsandboxed.

## Install

Requirements:

- Python 3.14
- [`uv`](https://docs.astral.sh/uv/)
- OSS CAD Suite extracted somewhere, for example `~/Kits/oss-cad-suite`
- A host C++ compiler and `make` for Verilator simulation
- `perl-doc`, required by Verilator's POD-based `--help` output and the catalog
  help command

```bash
git clone <repository> oss-cad-mcp
cd oss-cad-mcp
uv sync
export OSS_CAD_SUITE_ROOT="$HOME/Kits/oss-cad-suite"
uv run oss-cad-mcp doctor
```

`doctor` reports the suite version, workspace, capabilities, host compiler, and platform
verification status.

## MCP client configuration

The server speaks stdio by default. A generic client entry looks like:

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

Use the equivalent local-MCP entry for your client. The workspace defaults to the
server's current directory when `OSS_CAD_MCP_WORKSPACE` is not set.

## Tool surface

### Workflows

| Tool | Purpose |
|---|---|
| `cad_status` | Suite discovery, versions, capabilities, host toolchain |
| `cad_project` | Inspect, initialize, or validate a project |
| `cad_check` | Parse, lint, or elaborate with slang / Verilator / Icarus / GHDL |
| `cad_simulate` | Compile and run a testbench, returning diagnostics and waveform |
| `cad_synthesize` | Yosys synthesis with structured cell statistics |
| `cad_verify` | SBY formal proof/BMC/cover; EQY/MCY config fallback |
| `cad_fpga_build` | iCE40/ECP5 synth → PnR → pack → timing, software only |
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
| `cad_tool_run` | Allowlisted raw suite binary escape hatch |

Tools take a single nested `request` object, matching their published JSON schema. For
example:

```json
{
  "request": {
    "source": {"files": ["rtl/top.sv"], "language": "systemverilog"},
    "top": "top",
    "engines": ["slang", "verilator"]
  }
}
```

### Catalog coverage

All binaries in `<suite>/bin` are cataloged. Internal helpers (`tabbypy3`,
`verilator_bin`, `yosys-abc`), GUI tools, and hardware programmers are cataloged but not
agent-facing by default. `cad_tool_search` can include them for inspection; `cad_tool_run`
blocks hardware, GUI, and internal tools in v0.1.

## Example workflows

Lint a SystemVerilog file:

```python
# tool: cad_check
{"request": {"source": {"files": ["rtl/top.sv"], "language": "systemverilog"},
             "top": "top", "level": "lint", "engines": ["slang", "verilator"]}}
```

Simulate a testbench:

```python
# tool: cad_simulate
{"request": {"source": {"files": ["rtl/top.sv", "tb/tb_top.sv"]},
             "top": "tb_top", "engine": "verilator", "trace": "fst"}}
```

Synthesize for iCE40:

```python
# tool: cad_synthesize
{"request": {"source": {"files": ["rtl/top.sv"]}, "top": "top", "target": "ice40"}}
```

Build an iCEBreaker bitstream (software only):

```python
# tool: cad_fpga_build
{"request": {"source": {"files": ["rtl/top.sv"]}, "top": "top",
             "board": "icebreaker", "constraints": ["pins.pcf"],
             "frequency_mhz": 48}}
```

Prove an assertion testbench:

```python
# tool: cad_verify
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
```

Supported board presets in v0.1 include iCEBreaker, TinyFPGA BX, iCEstick, ULX3S 45F/85F,
and the ECP5 evaluation board. Presets only supply device/package/speed and frequency;
users provide pin constraints. For early bring-up, `allow_unconstrained=true` passes the
architecture-specific “allow unconstrained I/O” option to nextpnr.

## Configuration

Server settings are read from `~/.config/oss-cad-mcp/config.toml`, `OSS_CAD_MCP_*`
environment variables, and CLI options (CLI > env > TOML > defaults).

```toml
suite_root = "/home/you/Kits/oss-cad-suite"
workspace = "/home/you/projects/blinky"
profile = "core"
allow_raw = true
allow_unsafe = false
max_jobs = 4
default_timeout_s = 300
max_output_bytes = 65536
include_hardware = false
```

Useful commands:

```bash
uv run oss-cad-mcp doctor
uv run oss-cad-mcp doctor --deep --json
uv run oss-cad-mcp catalog --family formal
uv run oss-cad-mcp tool-help verilator
uv run oss-cad-mcp serve --transport stdio
```

## Architecture

- **Intent workflows** compose suite binaries into validated steps.
- **Family parsers** normalize diagnostics, Yosys stats, SBY statuses, nextpnr reports,
  icetime data, and VCD/FST waveforms.
- **Job manager** records argv, environment, timings, logs, manifests, and artifacts.
- **Catalog** covers every suite binary; high-level tools cover common goals.
- **Raw dispatcher** handles advanced flags without turning 155 binaries into 155 MCP
  schemas.
- **Process boundary**: the server does not import the suite's bundled Python 3.11
  packages. It runs suite binaries with the suite environment and parses their outputs.

Generated files go to `build/`; job state and logs go to `.oss-cad-mcp/runs/`.

## Limitations in v0.1

- No FPGA programming or hardware debug.
- No cocotb, UVM, or mixed-language simulation workflows.
- Gowin/Nexus/GateMate/MachXO2 build adapters are not yet verified; use
  `cad_synthesize` plus `cad_tool_run` for those targets.
- EQY/MCY require an existing configuration file.
- GHW waveform analysis is not supported; ask GHDL for VCD.
- macOS and native Linux are not verified for full support.
- Simulation executes project code. Treat RTL/testbenches as trusted input.

## Development

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
uv run pytest
```

Conventional Commits are used. The test suite includes parser fixtures, command-builder
tests, path/security tests, and integration tests against the installed suite when
`OSS_CAD_SUITE_ROOT` is available.

## License

MIT. OSS CAD Suite itself is not bundled or redistributed; each component retains its own
license. See the OSS CAD Suite project and component repositories for details.