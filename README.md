# AutoCAD Electrical Interface (ECM)

> Local web dashboard for **AutoCAD Electrical 2027** - control AutoCAD with plain language using Claude, Ollama or any OpenAI compatible models. Comes with a motor control center (MCC) configurator panel to edit or create complete MCC drawing packages.

> This app is designed for **Electrical Controls Manufacturing** (ECM), aimed at adding support for their Motor Control Center (MCC) products, as well as planned future support for
variable frequency drives (VFDs), soft starters, breaker panels, and other products. The MCC configurator requires a layout sheet, unit data sheet, and nameplate sheet to be open in AutoCAD — the drawings can have any filename. Additionally, the general data configurator panel requires a drawing containing the `GENRA001B` block to be open. The original repo used for this project is available at https://github.com/Igualguana/AUTOCAD-ELECTRICAL-MCP.

---

## MCC Configurator Setup

The MCC Builder requires four template drawings to be open in AutoCAD before you start a project. These are the files the tool writes into — they must come from your company's block library.

| Role | Default name | Purpose |
|------|-------------|---------|
| Layout sheet | e.g. `MCC_LAYOUT.dwg` | Receives section frames and unit blocks |
| Unit data sheet | e.g. `MCC_UNITDATA.dwg` | Receives `UDATALIN` data rows |
| Nameplate sheet | e.g. `MCC_NAMEPLATE.dwg` | Receives `LAMACOID` nameplate rows |
| General data sheet | e.g. `General_Data Sheet.dwg` | (Optional) Receives `GENRA001B` data |

> **Drawings can have any filename.** When you click **New Project**, the configurator
> fetches all drawings currently open in AutoCAD and presents four dropdowns so you can
> assign each role to the correct file — even if it is named something like
> `8PX3-A9390-S001.dwg`. The assignment is saved with the project and can be updated
> at any time via the **Reassign DWGs** toolbar button.

**Steps:**

1. Set `MCC_BLOCK_LIBRARY` in your `.env` to the folder containing your unit block DWGs.
2. Open your layout, unit data, and nameplate drawings in AutoCAD Electrical.
3. (Optional) Open your general data drawing in AutoCAD Electrical.
4. Start the web server: `python start_web.py`
5. Open `http://127.0.0.1:8080` and click **MCC** in the sidebar.
6. Click **New Project**, assign each drawing role in the dropdowns, and click **Create Project**.

> If a drawing is missing from the dropdowns, switch back to AutoCAD and open it, then
> click ↻ **Refresh** in the New Project form without closing it.

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

### Option A — One-click installer (recommended)

1. [Download or clone the repo](#) — if you don't have Git yet, download the ZIP from GitHub and unzip it.
2. Open the folder, right-click **`install.bat`** → **Run as administrator**.

The script will:
- Install **Python 3.11** via `winget` if it isn't already on your machine
- Install **Git** via `winget` if it isn't already on your machine
- Install all required Python libraries automatically
- Copy `.env.example` → `.env` so you can fill in your settings

> `winget` is built into Windows 10 (1709+) and Windows 11. If your machine doesn't have it,
> install [Python 3.11+](https://www.python.org/downloads/) and [Git](https://git-scm.com/download/win)
> manually, then run **Option B** below.

### Option B — Manual install

```bash
# 1. Clone
git clone https://github.com/nihaalgaina/AutoCAD-Electrical-Interface-ECM
cd AutoCAD-Electrical-Interface-ECM

# 2. Install dependencies
python scripts/install.py
```

### Configure your environment

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

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

You are free to use, modify, and distribute this software. Any modified version run as a network service must make its full source code available to users of that service.

See the [LICENSE](LICENSE) file for the complete license text.

---

## Author (Original Repository)

**Randy Igualguana**  
Copyright © 2026 Randy Igualguana
Original Repo: https://github.com/Igualguana/AUTOCAD-ELECTRICAL-MCP

---

*Built with [Model Context Protocol](https://modelcontextprotocol.io) · [FastMCP](https://github.com/jlowin/fastmcp) · [FastAPI](https://fastapi.tiangolo.com) · [pywin32](https://github.com/mhammond/pywin32) · [Ollama](https://ollama.com)*
