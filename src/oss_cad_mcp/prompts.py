"""MCP prompts that encode common HDL debugging and build playbooks."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register_prompts(mcp: MCPServer) -> None:
    @mcp.prompt(description="Debug a failing simulation using diagnostics and waveform evidence.")
    def debug_simulation(testbench: str, symptom: str = "testbench failed") -> str:
        return f"""\
Debug the simulation of `{testbench}`.

Observed symptom: {symptom}

1. Call `cad_status` if the suite or workspace is not already known.
2. Run `cad_simulate` with `top='{testbench}'`, an explicit `SourceSet`, and `trace='fst'`.
3. Read the compiler diagnostics first; do not change RTL until parse errors are gone.
4. If compilation succeeds but simulation fails, call `cad_waveform` with
   `action='summarize'` and then `action='sample'` for the clock, reset, and the
   signals named in the symptom.
5. Explain the first divergence in time order, then propose the smallest RTL or
   testbench fix.
"""

    @mcp.prompt(description="Fix lint and elaboration diagnostics before synthesis.")
    def fix_lint(sources: str = "", top: str = "") -> str:
        return f"""\
Lint and elaborate the current HDL project.

Sources hint: {sources or "use oss-cad-project.toml or auto-detection"}
Top hint: {top or "detect from the project"}

1. Run `cad_project(action='inspect')` to identify files and top candidates.
2. Run `cad_check(level='elaborate')` with the relevant engine.
3. Group diagnostics by file and severity, fix errors first, then warnings that
   indicate real intent mismatches.
4. Re-run `cad_check` until it is clean, then move on to simulation or synthesis.
"""

    @mcp.prompt(description="Prove or bound-check assertions with SBY.")
    def formal_verify(top: str, clock: str = "clk", reset: str = "rst", depth: int = 20) -> str:
        return f"""\
Set up formal verification for `{top}` with clock `{clock}` and reset `{reset}`.

1. Ensure the formal testbench contains `assert`, `assume`, and optional `cover`
   properties, and uses `read -formal` compatible Verilog/SystemVerilog.
2. Run `cad_verify(mode='prove', top='{top}', depth={depth},
   source=SourceSet(files=[...]))`.
3. If it fails, inspect the reported property and counterexample trace; use
   `cad_waveform` to find the first failing cycle.
4. Distinguish an RTL bug from an over-strong assumption or a missing reset
   constraint before editing the design.
"""

    @mcp.prompt(description="Build a software-only iCE40/ECP5 bitstream and inspect timing.")
    def build_fpga(board: str = "icebreaker", constraints: str = "pins.pcf") -> str:
        return f"""\
Build a software-only FPGA image for the `{board}` board.

1. Run `cad_project(action='inspect')` and confirm the top module, sources, and
   constraint file (`{constraints}`).
2. Run `cad_fpga_build(board='{board}', constraints=['{constraints}'],
   source=SourceSet(files=[...]), top='...')`.
3. If place-and-route fails, read the nextpnr log and check device/package/pin
   constraints first.
4. If timing fails, inspect `timing.critical_paths` and reduce the target
   frequency or pipeline the critical path.
5. Hardware programming is disabled in v0.1; stop after the bitstream artifact.
"""
