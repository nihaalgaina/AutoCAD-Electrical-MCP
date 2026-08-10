# AutoCAD Electrical MCP (ECM)

> MCP server + local AI web dashboard for **AutoCAD Electrical 2027** - control AutoCAD with plain language via Claude, Ollama, or any OpenAI compatible models.

> This fork was designed for **Electrical Controls Manufacturing** (ECM), aimed at adding support for their Motor Control Center (MCC) products, as well as planned support for
variable frequency drives (VFDs), soft starters, breaker panels, and other products. The MCC configurator will not work unless MCC_LAYOUT, MCC_UNITDATA, and MCC_NAMEPLATE.dwg are open in AutoCAD, and the required CAD blocks are located in the filepath written in .env. The original repo and its README.md are available at https://github.com/Igualguana/AUTOCAD-ELECTRICAL-MCP.

---

## MCC Configurator Setup

The MCC Builder requires three template drawings to be open in AutoCAD before you start a project. These are the files the tool writes into — they must come from your company's block library.

| File | Purpose |
|------|---------|
| `MCC_LAYOUT.dwg` | Receives section frames and unit blocks |
| `MCC_UNITDATA.dwg` | Receives `UDATALIN` data rows |
| `MCC_NAMEPLATE.dwg` | Receives `LAMACOID` nameplate rows |

**Steps:**

1. Set `MCC_BLOCK_LIBRARY` in your `.env` to the folder containing these files (and all unit block DWGs).
2. Open `MCC_LAYOUT.dwg`, `MCC_UNITDATA.dwg`, and `MCC_NAMEPLATE.dwg` in AutoCAD Electrical.
3. Start the web server: `python start_web.py`
4. Open `http://127.0.0.1:8080` and click **MCC** in the sidebar.
5. Click **New Project** — the tool will connect to the three open drawings automatically.

> If you see *"MCC_LAYOUT.dwg is not open"* when creating a project, go back to step 2 and make sure all three files are open in AutoCAD before clicking New Project.

---

## Web Interface

The web dashboard is a FastAPI + vanilla JS single-page app served locally at `http://127.0.0.1:8080`.

### Running locally

```bash
# Start the web server (opens browser automatically)
python start_web.py

# Or specify host/port explicitly
python start_web.py --host 127.0.0.1 --port 8080 --no-browser
```

---

## Requirements

- **Windows 10/11 (64-bit)** — COM automation is Windows-only
- **Python 3.11+**
- **AutoCAD Electrical 2027** running with the required .dwg files open
- Mode A only: Anthropic API key
- Mode B only: [Ollama](https://ollama.com) installed and running

---

## Installation

```bash
# 1. Clone
git clone https://github.com/nihaalgaina/AutoCAD-Electrical-MCP
cd AutoCAD-Electrical-MCP

# 2. Install dependencies
pip install -e .

# 3. Configure environment
cp .env.example .env
```

Open `.env` in any text editor and fill in the values for your setup:

```env
# Path to the folder containing your MCC block .DWG files
# (MCC_400.DWG, UNIT4040.DWG, UDATALIN.DWG, LAMACOID.DWG, etc.)
MCC_BLOCK_LIBRARY=C:\Path\To\Your\MCC Blocks

# Only needed if you want to use Claude as the AI provider
ANTHROPIC_API_KEY=sk-ant-...

# Only needed for OpenAI / Groq
OPENAI_API_KEY=...
GROQ_API_KEY=...
```

> **`MCC_BLOCK_LIBRARY`** is the only setting most users need to change.
> Point it at whichever folder holds your company's `.DWG` block library.
> Multiple folders are supported — separate them with a semicolon:
> `MCC_BLOCK_LIBRARY=C:\Blocks\400mm;C:\Blocks\500mm`

### Ollama setup (Mode B — local AI, no API key required)

```bash
# Install from https://ollama.com then:
ollama serve                   # start the server
ollama pull qwen2.5:0.5b       # fast, runs on 4 GB RAM
# or
ollama pull qwen2.5:7b         # better quality, needs ~6 GB RAM
```

> **Recommended**: `qwen2.5:0.5b` (397 MB) works well for the keyword pre-router and is reliable on machines with limited RAM. For the full LLM path use `qwen2.5:7b` or larger.

---

## Author (Original Repository)

**Randy Igualguana**  
Copyright © 2026 Randy Igualguana

---

*Built with [Model Context Protocol](https://modelcontextprotocol.io) · [FastMCP](https://github.com/jlowin/fastmcp) · [FastAPI](https://fastapi.tiangolo.com) · [pywin32](https://github.com/mhammond/pywin32) · [Ollama](https://ollama.com)*
