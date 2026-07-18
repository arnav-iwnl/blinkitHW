# 🛒 BlinkitHW — Blinkit Product Watcher & Auto-Purchaser

An automated tool that monitors [Blinkit](https://blinkit.com) product pages for availability and instantly purchases them the moment they go live. Built for snagging limited-edition drops (like Hot Wheels exclusives) before they sell out.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Real-time Monitoring** | Polls product pages at configurable intervals, intercepting Blinkit's internal inventory API for instant stock detection |
| **Auto-Purchase** | Automatically adds to cart, handles checkout, and selects payment method (Cash / UPI / MobiKwik) |
| **Telegram Notifications** | Sends rich notifications with product details, inventory count, and inline action buttons (Retry / Cancel) |
| **UPI Deep-Link Payments** | Generates UPI payment links and sends them via Telegram for one-tap mobile payment |
| **Multi-Account Support** | Switch between saved authentication sessions via an interactive CLI menu |
| **Cart Cleanup** | Automatically removes mismatched items from cart using fuzzy name matching (Jaccard similarity) |
| **Headless Mode** | Run the browser invisibly in the background with `--headless` |
| **Session Persistence** | Saves browser sessions to disk so you don't need to re-login every time |
| **Sitemap Scraper** | Bulk-scan Blinkit's sitemap to discover product IDs matching a keyword |

---

## 📁 Project Structure

```
blinkitHW/
├── main.py                         # Entrypoint — run this
├── requirements.txt                # Python dependencies
├── watcher_config.json             # Product URLs, addresses, auth, defaults
├── blinkit.cmd                     # Windows shortcut to launch the watcher
├── .env.example                    # Template for environment variables
│
├── src/
│   ├── cli.py                      # CLI argument parsing & interactive menus
│   ├── config.py                   # Config & .env loader
│   ├── constants.py                # Shared constants (paths, colors, banner)
│   │
│   ├── watcher/
│   │   └── product_watcher.py      # Core ProductWatcher class
│   │
│   ├── auth/
│   │   └── service.py              # BlinkitAuth — browser session & login
│   │
│   ├── order/
│   │   ├── blinkit_order.py        # BlinkitOrder — facade for all order services
│   │   └── services/
│   │       ├── base.py             # BaseService with store-closed check
│   │       ├── cart.py             # Add/remove/list cart items
│   │       ├── checkout.py         # Proceed to pay, payment selection, QR decode
│   │       ├── location.py         # Address search & selection
│   │       ├── search.py           # Product search
│   │       └── auto_purchase.py    # Status-file-driven auto-purchase service
│   │
│   ├── telegram/
│   │   └── service.py              # Telegram bot — notifications & callback polling
│   │
│   ├── utils/
│   │   ├── geo.py                  # IP-based geolocation
│   │   ├── logging.py              # Colored ordinal-date log formatter
│   │   ├── product.py              # Product name normalization & alert sounds
│   │   └── terminal.py             # Arrow-key interactive menus
│   │
│   └── scraper/
│       ├── scrapling_scraper.py    # Bulk product ID scanner (Scrapling)
│       └── sitemap_filter.py       # Sitemap XML filter by product ID & keyword
│
├── data/                           # Runtime data (status files, scrape output)
├── logs/                           # Log files
└── legacy/                         # Old single-file watcher (preserved for reference)
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Firefox** (used by Playwright)

### 1. Clone & Install

```bash
git clone https://github.com/your-username/blinkitHW.git
cd blinkitHW

# Create virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (one-time)
playwright install firefox
```

### 2. Configure Environment

```bash
copy .env.example .env
```

Edit `.env` with your Telegram bot credentials:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHANNEL_ID=your_channel_id_here
UPI_REDIRECT_BASE=https://your-upi-redirect-url.github.io/
```

### 3. Configure Products & Addresses

Edit `watcher_config.json` to add your target products, delivery addresses, and authentication accounts:

```json
{
  "products": [
    {
      "name": "Hot Wheels Dodge Charger",
      "url": "https://blinkit.com/prn/hot-wheels-dodge-charger/prid/796786"
    }
  ],
  "addresses": [
    { "label": "Home" }
  ],
  "auth": [
    { "name": "Main", "phone": "9XXXXXXXXX" }
  ],
  "defaults": {
    "check_interval": 5,
    "quantity": 1,
    "continue_on_oos": true,
    "automate_checkout": true,
    "preferred_payment": "mobi"
  }
}
```

### 4. Run

```bash
python main.py
```

Or with headless browser:

```bash
python main.py --headless
```

On Windows, you can also use the shortcut:

```
blinkit.cmd
```

---

## ⚙️ Configuration Reference

### Environment Variables (`.env`)

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHANNEL_ID` | Channel/group ID for notifications (e.g. `-100123456789`) |
| `UPI_REDIRECT_BASE` | Base URL for the UPI redirect page (for deep-link payments) |

### Watcher Config (`watcher_config.json`)

| Section | Description |
|---|---|
| `products` | Array of `{name, url}` objects — product URLs to monitor |
| `addresses` | Array of `{label}` objects — saved Blinkit address labels |
| `auth` | Array of `{name, phone}` objects — login credentials |
| `defaults` | Default values for check interval, quantity, payment method, etc. |

---

## 🔧 CLI Options

```
python main.py [OPTIONS]

Options:
  --headless    Run browser in headless (invisible) mode

Special modes:
  python main.py test    Run the built-in cart cleanup test
```

During startup, the interactive CLI will prompt you to select:

1. **Product** — from saved list or enter URL manually
2. **Address** — from saved address labels
3. **Auth account** — from saved phone numbers
4. **Check interval** — seconds between availability checks
5. **Quantity** — number of items to purchase
6. **Continue on OOS** — keep monitoring if product goes out of stock
7. **Automate checkout** — auto-complete payment or stop after cart
8. **Payment method** — Cash / UPI / MobiKwik

---

## 📡 Telegram Integration

When configured, the bot sends rich notifications:

- **🎉 Product Available** — with product name, location, inventory count, and link
- **💳 UPI Payment Ready** — with amount, quantity, and a "Pay Now" deep-link button
- **Inline Buttons** — Retry (restart watch) and Cancel (stop watch)

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Add the bot to your channel/group as admin
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHANNEL_ID` in `.env`

---

## 🕷️ Scraper Tools

### Sitemap Filter

Filter Blinkit's product sitemap XML by product ID range and keyword:

```bash
python -m src.scraper.sitemap_filter --input product.xml --keyword hot-wheels --min-prid 770000
```

### Bulk Product Scanner (Scrapling)

Scan a range of product IDs to find matches:

```bash
python -m src.scraper.scrapling_scraper --start 770000 --end 800000 --workers 5
```

---

## 📋 Requirements

```
aiohttp==3.13.2
openpyxl==3.1.5
pandas==3.0.1
playwright==1.56.0
python-dotenv==1.2.2
scrapling==0.4.2
```

---

## 📄 License

This project is for personal/educational use.
