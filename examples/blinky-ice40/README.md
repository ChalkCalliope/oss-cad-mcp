# Blinky for iCEBreaker

A minimal software-only build example. It produces a bitstream but does not program
hardware.

From an MCP client, use this directory as the workspace:

```json
{"request": {"source": {"files": ["rtl/blinky.sv"]}, "top": "blinky",
             "board": "icebreaker", "constraints": ["pins.pcf"],
             "frequency_mhz": 48}}
```

Useful local checks:

```bash
cd examples/blinky-ice40
uv run --directory ../.. oss-cad-mcp doctor --workspace .
uv run --directory ../.. oss-cad-mcp catalog --family formal
```

Simulation and bitstream outputs are written under `build/` and
`.oss-cad-mcp/runs/`.