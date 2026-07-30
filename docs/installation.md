# Installation

## Requirements

- Python 3.10 or higher
- kiro-cli installed and authenticated
- Windows (for tray icon features)

## From PyPI

```bash
pip install kirox
```

### With Service Features

```bash
pip install kirox[service]
```

This adds:
- `pystray` - System tray icon
- `Pillow` - Image processing
- `APScheduler` - Background scheduling

### With MCP Support

```bash
pip install kirox[mcp]
```

### All Features

```bash
pip install kirox[service,mcp]
```

## From Source

```bash
git clone https://github.com/idugeni/kirox.git
cd kirox
pip install -e ".[dev,service]"
```

## Development

```bash
git clone https://github.com/idugeni/kirox.git
cd kirox
pip install -e ".[dev]"
pytest
```

## Verify Installation

```bash
kirox --version
kirox status
```

## Troubleshooting

### Permission Error

If you get a permission error, try:

```bash
pip install --user kirox
```

Or use a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows
pip install kirox
```

### Import Error

If you get an import error, make sure you're using Python 3.10+:

```bash
python --version
```

### Token Error

If kirox can't find your credentials:

1. Make sure kiro-cli is installed: `kiro-cli --version`
2. Make sure you're logged in: `kiro-cli login`
3. Or set environment variables:
   ```bash
   export KURO_TOKEN="your-token"
   export KURO_PROFILE_ARN="your-profile-arn"
   ```
