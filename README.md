# GTA Vice City — HTML5 Port (DOS Zone)

Web-based port of GTA: Vice City running in browser via WebAssembly.

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

3.  **Start the game**:
    *   **Standard Mode**:
        ```bash
        pixi run start
        ```
    *   **Cheat Mode** (Opens browser with cheats enabled):
        ```bash
        pixi run cheat
        ```

4.  **Play**: Open the link shown in the terminal (usually `http://localhost:8000`). If you used `pixi run cheat`, the browser should open automatically.

## Requirements

- Python 3.8+
- Dependencies from `requirements.txt`
- **Pixi** (Recommended for package management)

## Setup & Running

### Option 1: Using Pixi (Recommended)

This project uses [Pixi](https://pixi.sh/) for dependency management and task running.

1.  **Install Pixi**: Follow the instructions at [pixi.sh](https://pixi.sh/).
2.  **Start the server**:
    ```bash
    pixi run start
    ```
    This command automatically installs dependencies and starts the server with the correct configuration.

### Option 2: Using Docker
The easiest way to get started is using Docker Compose:

```bash
docker compose up -d --build
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

### Option 3: Local Installation (Manual)

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Start the server:
```bash
python server.py --custom_saves
```

Server starts at `http://localhost:8000`

## Server Behavior & Caching

The server (`server.py`) has been updated with a smart caching strategy to optimize performance and bandwidth.

*   **`vcbr` Resources (Core Game Data)**:
    *   These files **MUST** be present locally in the `vcbr/` directory.
    *   The server will *only* look for them locally. It does not download them from a CDN.
    *   Ensure you have the correct `.data` and `.wasm` files in `vcbr/`.

*   **`vcsky` Resources (Additional Assets)**:
    *   The server uses a **Cache-First** strategy.
    *   **Check Local**: It first checks if the requested file exists in the local `vcsky/` directory.
    *   **Download & Cache**: If the file is missing locally, it automatically downloads it from the CDN (`https://cdn.dos.zone/vcsky/`), saves it to the local `vcsky/` directory, and then serves it.
    *   **Serve**: Subsequent requests for the same file are served directly from the local disk.

## Server Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--port` | int | 8000 | Server port |
| `--custom_saves` | flag | disabled | Enable local save files (saves router) |
| `--login` | string | none | HTTP Basic Auth username |
| `--password` | string | none | HTTP Basic Auth password |
| `--vcsky_local` | flag | enabled | (Legacy) Prioritize local `vcsky` files (now default behavior) |
| `--vcbr_local` | flag | enabled | (Legacy) Prioritize local `vcbr` files (now default behavior) |

**Examples:**
```bash
# Start on custom port
python server.py --port 3000

# Enable local saves (Recommended)
python server.py --custom_saves

# Enable HTTP Basic Authentication
python server.py --login admin --password secret123
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
├── server.py           # FastAPI caching server
├── pixi.toml           # Pixi project configuration
├── requirements.txt    # Python dependencies
├── additions/          # Server extensions
│   ├── auth.py         # HTTP Basic Auth middleware
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

## Support me

If you find this project useful:

- **TON / USDT (TON)**  `UQAyBchGEKi9NnNQ3AKMQMuO-SGEhMIAKFAbkwwrsiOPj9Gy`
