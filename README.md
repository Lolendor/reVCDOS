# GTA Vice City — HTML5 Port (DOS Zone)

Web-based port of GTA: Vice City running in browser via WebAssembly.

## Recent Updates (Dec 2025)

*   **New Cheat Command**: Added `pixi run cheat` to automatically launch the game with cheats enabled and open the browser.
*   **Asset Management**: Updated server logic to correctly handle `vcsky/fetched` assets without duplication.
*   **Documentation**: Added comprehensive "Quick Start", "Download Assets" (with links), and "Project Structure" sections.
*   **Fixes**: Solved directory nesting issues and added `vcbr` to `.gitignore` to prevent large file commit errors.
*   **Upstream Sync**: Merged latest changes from `Lolendor/reVCDOS` while preserving custom features (cheats, offline mode).
*   **Smart Caching**: Restored "Smart Cache" behavior (check local, then download) as the default.

You can check which files I made changes in commit section.

## History & Restoration

This project is a community effort to preserve the incredible HTML5 port of GTA: Vice City.

*   **The Ban**: Rockstar Games issued a takedown for the original web port, shutting down the official servers and CDNs (`cdn.dos.zone`).
*   **Deobfuscation**: The source code was deobfuscated to allow for self-hosting and study.
*   **WebAssembly Magic**: Unlike standard PC mods (e.g., *Vice City: Reviced*), this port runs entirely in the browser using WebAssembly. It streams assets on-the-fly, making it playable on almost any device with a browser and keyboard, without installation.
*   **Restoring Functionality**: After the shutdown, the game would hang on a black screen because it couldn't fetch core data files (`.wasm`, `.data`) or assets from the dead CDNs. This repository solves that by:
    1.  Providing a server that serves these critical files locally.
    2.  Implementing a caching system to download and save surviving assets.
    3.  Removing dependencies on the defunct infrastructure.

> [!NOTE]
> We still do not have the source code for the files in `vcbr` (the compiled WebAssembly modules), which contain the core logic of the game. This project wraps and serves those existing binaries.

**Stability Test**: Watch the server in action here: [GTA VC Server Stability Test](https://www.youtube.com/watch?v=C8nK81N4iBs)

## Quick Start

1.  **Clone the repository**:
    ```bash
    git clone --depth 1 https://github.com/Th3w33knd/reVCDOS
    cd reVCDOS
    ```

2.  **Download Assets**:
    > [!WARNING]
    > These files contain copyrighted materials from Rockstar Games. You must own the original game to use them legally.

    *   **vcbr** (Core Data): [Download](https://gofile.io/d/ceuXTa) (or [Older Version](https://gofile.io/d/U63PZO))
    *   **vcsky** (Assets): [Download](https://gofile.io/d/9QsvMn)

    **Important**: Extract them so your folder structure looks exactly like this:
    ```text
    reVCDOS/
    ├── vcbr/
    │   ├── vc-sky-en-v6.data
    │   ├── vc-sky-en-v6.wasm
    │   ├── vc-sky-ru-v6.data
    │   └── vc-sky-ru-v6.wasm
    ├── vcsky/
    │   ├── sha256sums.txt
    │   ├── fetched/
    │   │   ├── audio/      <-- From download
    │   │   ├── data/       <-- From download
    │   │   └── ...
    │   └── ...
    ├── server.py
    └── ...
    ```

3.  **Install Pixi** (if not already installed):
    *   **Windows (PowerShell)**:
        ```powershell
        powershell -ExecutionPolicy Bypass -c "irm -useb https://pixi.sh/install.ps1 | iex"
        ```
    *   **Linux & macOS**:
        ```bash
        curl -fsSL https://pixi.sh/install.sh | bash
        ```

4.  **Start the game**:
    *   **Online Mode** (Recommended):
        ```bash
        pixi run online
        ```
        *Checks local files first. If missing, downloads from CDN and caches them.*

    *   **Offline Mode** (Strict):
        ```bash
        pixi run offline
        ```
        *Uses ONLY local files. No network connection. Useful if you have all assets downloaded.*

    *   **Cheat Mode**:
        ```bash
        pixi run cheat
        ```

5.  **Play**: Open the link shown in the terminal (usually `http://localhost:8000`). If you used `pixi run cheat`, the browser should open automatically.

## Requirements

- Docker or Python 3.8+ or PHP 8.0+
- Dependencies from `requirements.txt`
- **Pixi** (Recommended for package management)

## Setup & Running

### Option 1: Using Pixi (Recommended)

This project uses [Pixi](https://pixi.sh/) for dependency management and task running.

1.  **Install Pixi**: Follow the instructions at [pixi.sh](https://pixi.sh/).
2.  **Run a task**:
    *   `pixi run online` - Smart caching (download if missing).
    *   `pixi run offline` - Strict local files only.
    *   `pixi run cheat` - Online mode + cheats enabled + auto-open browser.

### Option 2: Using Docker
The easiest way to get started is using Docker Compose:

```bash
VCSKY_CACHE=1 VCBR_CACHE=1 docker compose up -d --build
```

To configure server options via environment variables:

```bash
# Set port, enable auth and custom saves
IN_PORT=3000 AUTH_LOGIN=admin AUTH_PASSWORD=secret CUSTOM_SAVES=1 docker compose up -d --build
```

| Environment Variable | Description |
|---------------------|-------------|
| `OUT_HOST` | External host (default: 0.0.0.0) |
| `OUT_PORT` | External port (default: 8000) |
| `IN_PORT` | Internal container port (default: 8000) |
| `AUTH_LOGIN` | HTTP Basic Auth username |
| `AUTH_PASSWORD` | HTTP Basic Auth password |
| `CUSTOM_SAVES` | Enable local saves (set to `1`) |
| `VCSKY_LOCAL` | Serve vcsky from local directory (set to `1`) |
| `VCBR_LOCAL` | Serve vcbr from local directory (set to `1`) |
| `VCSKY_URL` | Custom vcsky proxy URL |
| `VCBR_URL` | Custom vcbr proxy URL |
| `VCSKY_CACHE` | Cache vcsky files locally while proxying (set to `1`) |
| `VCBR_CACHE` | Cache vcbr files locally while proxying (set to `1`) |

### Option 3: Local Installation (Manual)

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Start the server:
```bash
python server.py --vcsky_local --vcbr_local --custom_saves
```

Server starts at `http://localhost:8000`

### Option 4: Shared Hosting on PHP (No installation)

If you want to run the game from a hosted environment with `PHP 8.0` or above, just copy the contents of this repo to your desired hosting.
By default the `index.php` and `.htaccess` will get the job done.

## Server Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port` | int | 8000 | Server port |
| `--custom_saves` | flag | disabled | Enable local save files (saves router) |
| `--login` | string | none | HTTP Basic Auth username |
| `--password` | string | none | HTTP Basic Auth password |
| `--vcsky_local` | flag | disabled | Serve vcsky from local `vcsky/` directory (Strict Local) |
| `--vcbr_local` | flag | disabled | Serve vcbr from local `vcbr/` directory (Strict Local) |
| `--vcsky_url` | string | `https://cdn.dos.zone/vcsky/` | Custom vcsky proxy URL |
| `--vcbr_url` | string | `https://br.cdn.dos.zone/vcsky/` | Custom vcbr proxy URL |
| `--vcsky_cache` | flag | enabled | Cache vcsky files locally while proxying (Smart Cache) |
| `--vcbr_cache` | flag | enabled | Cache vcbr files locally while proxying (Smart Cache) |
| `--cheats` | flag | disabled | Enable cheats in URL |
| `--open` | flag | disabled | Open browser on start |

**Examples:**
```bash
# Start on custom port
python server.py --port 3000

# Enable local saves (Recommended)
python server.py --custom_saves

# Enable HTTP Basic Authentication
python server.py --login admin --password secret123

# Use local vcsky and vcbr files (fully offline mode)
python server.py --vcsky_local --vcbr_local

# Cache files locally while proxying (hybrid mode)
python server.py --vcsky_cache --vcbr_cache
```

## URL Parameters

| Parameter | Values | Description |
|-----------|--------|-------------|
| `lang` | `en`, `ru` | Game language |
| `cheats` | `1` | Enable cheat menu (F3) |

**Examples:**
- `http://localhost:8000/?lang=ru` — Russian version
- `http://localhost:8000/?lang=en&cheats=1` — English + cheats

## Project Structure

```
├── server.py           # FastAPI proxy/caching server
├── pixi.toml           # Pixi project configuration
├── requirements.txt    # Python dependencies
├── additions/          # Server extensions
│   ├── auth.py         # HTTP Basic Auth middleware
│   ├── cache.py        # Proxy caching and brotli decompression
│   └── saves.py        # Local saves router
├── dist/               # Game client files
│   ├── index.html      # Main page
│   ├── game.js         # Game loader (updated with ownership check)
│   ├── index.js        # Module loader
│   ├── GamepadEmulator.js  # Touch controls
│   ├── idbfs.js        # IndexedDB filesystem
│   ├── jsdos-cloud-sdk.js  # Cloud saves (DOS Zone)
│   ├── jsdos-cloud-sdk-local.js  # Local saves (--custom_saves)
│   └── modules/        # WASM modules
├── vcbr/               # Core game data (REQUIRED LOCALLY)
│   ├── vc-sky-en-v6.data
│   ├── vc-sky-en-v6.wasm
│   ├── vc-sky-ru-v6.data
│   └── vc-sky-ru-v6.wasm
└── vcsky/              # Additional assets (Cached locally on demand)
    ├── sha256sums.txt
    └── fetched/        # Downloaded assets
        ├── data/
        ├── audio/
        ├── models/
        └── anim/
```

## Features

- 🎮 Gamepad emulation for touch devices
- ☁️ Cloud saves via js-dos key
- 💾 Local saves (with `--custom_saves` flag)
- 🌍 English/Russian language support
- 🔧 Built-in cheat engine (memory scanner, cheats)
- 📱 Mobile touch controls
- 🔒 **Original Game Verification**: You must provide an original game file to verify ownership and play the full version.

## Local Saves

When local saves are enabled (`--custom_saves` flag), enter any 5-character identifier in the "js-dos key" input field on the start page. This identifier will be used to store your saves in the `saves/` directory on the server.

Example: Enter `mykey` or `12345` — saves will be stored as `mykey_vcsky.saves` or `12345_vcsky.saves`.

## Controls (Touch)

Touch controls appear automatically on mobile devices. Virtual joysticks for movement and camera, context-sensitive action buttons.

## Cheats

Enable with `?cheats=1`, press **F3** to open menu:
- Memory scanner (find/edit values)
- All classic GTA VC cheats
- AirBreak (noclip mode)

## License

Do what you want. Not affiliated with Rockstar Games.

---

**Authors:** DOS Zone ([@specialist003](https://github.com/okhmanyuk-ev), [@caiiiycuk](https://www.youtube.com/caiiiycuk), [@SerGen](https://t.me/ser_var))

**Deobfuscated by**: [@Lolendor](https://github.com/Lolendor)

**Russian translation:** [GamesVoice](https://www.gamesvoice.ru/)

**Some more files were required for being Fully Local:** [Th3w33knd](https://github.com/Th3w33knd)

## Support me

If you find this project useful:

- **TON / USDT (TON)**  `UQAyBchGEKi9NnNQ3AKMQMuO-SGEhMIAKFAbkwwrsiOPj9Gy`
