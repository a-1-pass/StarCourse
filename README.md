# StarCourse

A desktop automation tool for progressing through online course content on the Chaoxing (超星学习通) education platform. Built with PyQt6 and a pure-HTTP request model — no browser or Selenium required.

## Features

- **Multi-account management** — add, remove, and monitor multiple accounts from a single interface
- **Automated video completion** — instant finish or timed playback modes
- **AI-assisted quiz answering** — multi-provider architecture (DeepSeek, OpenAI GPT, Claude, Baidu Wenxin, StepFun) with priority-based failover and unified timeout/retry handling
- **Document and reading tasks** — auto-complete reading nodes, documents, and book chapters
- **Task queue** — chain multiple courses into a queue with pause / stop controls and real-time progress
- **Chapter selection** — optionally target specific chapters instead of processing an entire course
- **Multi-threaded video processing** — configurable worker count for parallel video completion
- **Anti-detection** — rotating User-Agent pool with modern browser fingerprints, Client Hints headers, and request jittering

## Quick Start

### Prerequisites

- Python 3.10+
- Windows / macOS / Linux

### Installation

```bash
git clone https://github.com/a-1-pass/StarCourse.git
cd StarCourse
pip install -r requirements.txt
```

### Configuration (optional — AI answering)

StarCourse supports multiple LLM providers — DeepSeek, OpenAI GPT, Anthropic
Claude, Baidu Wenxin, and StepFun — with per-provider keys and a priority-based
failover chain.

```bash
cp config.example.json config.json
```

Edit `config.json` and fill in the API key(s) of the provider(s) you want to
use. The same configuration is available in the GUI under **Advanced settings
→ AI model settings**. Full field reference, code examples, and the provider
extension guide: [docs/ai_providers.md](docs/ai_providers.md).

Without AI configuration, quiz answering defaults to question-bank lookups or
local strategies (first option / random).

### Running

```bash
python main.py
```

The GUI will launch. Click **Add** to create an account profile, then click **Manage** to open the course workspace.

### CLI Mode

The engine also ships with an interactive command-line interface:

```bash
cd engine
python cli_entry.py
```

## Project Structure

```
StarCourse/
├── main.py                    # Application entry point (PyQt6)
├── requirements.txt           # Python dependencies
├── theme.qss                  # Dark theme stylesheet
├── config.example.json        # AI configuration template
│
├── src/                       # Core logic layer
│   ├── log_manager.py         #   Logging (rotation + auto-cleanup)
│   ├── network_utils.py       #   Anti-detection UA pool + helpers
│   ├── ai_assistant.py        #   DeepSeek AI client
│   ├── stepfun_ai.py          #   StepFun AI client
│   ├── engine_adapter.py      #   High-level engine façade
│   ├── account_store.py       #   Profile persistence (JSON)
│   └── utils/
│       └── atomic_io.py       #   Atomic file read/write
│
├── engine/                    # Task engine package
│   ├── cli_entry.py           #   CLI interface
│   ├── config.yml             #   Engine configuration
│   ├── config.py              #   Config loader
│   ├── utils.py               #   HTTP request helpers
│   ├── card_decode.py         #   Task-point decoder
│   ├── cache_dao.py           #   Answer cache
│   ├── tiku.py                #   Question bank interface
│   ├── deepseek_ai_enhanced.py # Enhanced AI answering
│   ├── stepfun_ai.py         #   StepFun AI client
│   ├── ai_providers/         #   Multi-provider AI architecture (GPT/Claude/Wenxin/…)
│   ├── classis/               #   Domain objects
│   │   ├── User/              #     Authentication + course data
│   │   ├── Course/            #     Course model
│   │   ├── Media/             #     Content handlers (Video/Quiz/Work/Document/Read/Book/Live/BBS)
│   │   ├── Sign/              #     Check-in
│   │   ├── SelfException/     #     Custom exceptions
│   │   └── UserLogger/        #     Log wrapper
│   └── functions/             #   Pluggable operations
│       ├── deal_mission/      #     One-click automation
│       ├── media_download/    #     Resource download
│       ├── set_log/           #     Activity counter
│       └── set_time/          #     Timed video playback
│
└── gui/                       # PyQt6 GUI layer
    ├── account_panel.py       #   Account list + toolbar
    └── course_workshop.py     #   Course workspace (login → queue → progress)

docs/
└── ai_providers.md            # Multi-model API reference & examples
```

## Configuration

### AI Answering (`config.json`)

| Field | Description | Default |
|-------|-------------|---------|
| `default_provider` | Active provider | `deepseek` |
| `priority` | Provider failover chain (tried in order) | `["deepseek"]` |
| `timeout` | Unified request timeout (seconds) | `30` |
| `max_retries` | Retries for timeout / rate-limit / 5xx | `0` |
| `<provider>.api_key` | Per-provider API key (`wenxin` also needs `secret_key`) | — |
| `<provider>.base_url` / `model` | Endpoint root / model name | provider defaults |

Full reference, code examples, and extension guide:
[docs/ai_providers.md](docs/ai_providers.md).

### Engine Config (`engine/config.yml`)

| Key | Description | Values |
|-----|-------------|--------|
| `FunConfig.deal-mission.video-mode` | Video completion strategy | `0` = instant, `1` = timed |
| `FunConfig.deal-mission.enable-multi-thread` | Enable multi-threading | `true` / `false` |
| `FunConfig.deal-mission.max-concurrent-threads` | Max concurrent workers | 1–15 |
| `TikuConfig` | Question bank and AI settings | — |
| `SignConfig` | Auto check-in settings | — |

## Architecture

StarCourse uses a **pure HTTP request model** — all interactions with the platform are performed via `requests.Session`, without spawning a browser or Selenium WebDriver. This makes the tool lightweight, fast, and suitable for long-running background operation.

The anti-detection layer (`src/network_utils.py`) maintains a pool of real browser User-Agent strings (Chrome 130–142, Edge, Firefox) matched to the host OS, injects Client Hints (`sec-ch-ua`) headers, and adds randomized jitter between requests to mimic human browsing patterns.

## Disclaimer

This software is provided for **educational and research purposes only**.

- The authors of this project are not affiliated with, endorsed by, or sponsored by Chaoxing / 超星 / XueXiTong or any of their subsidiaries.
- Users are solely responsible for complying with the Terms of Service of the platform they interact with.
- Automated access to educational platforms may violate their acceptable use policies. Use at your own risk.
- The developers do not store, transmit, or process any user credentials through third-party servers — all authentication happens locally on the user's machine.
- This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## License

MIT License — see [LICENSE](LICENSE).

## Contributing

Pull requests are welcome. Please open an issue first to discuss significant changes.
