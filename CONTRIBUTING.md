# Contributing to Kirox

Thank you for your interest in contributing! This document provides guidelines for contributing to kirox.

## Getting Started

1. Fork the repository
2. Clone your fork
3. Create a feature branch
4. Make your changes
5. Submit a pull request

## Development Setup

```bash
# Clone your fork
git clone https://github.com/your-username/kirox.git
cd kirox

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest
```

## Code Style

- Follow PEP 8
- Use type hints
- Keep functions small and focused
- Write docstrings for public APIs

## Testing

- Write tests for new features
- Ensure all tests pass before submitting
- Aim for high test coverage

```bash
pytest                    # Run all tests
pytest tests/unit/        # Run unit tests
pytest --cov=kirox        # Run with coverage
```

## Pull Request Process

1. Update documentation if needed
2. Add tests for new features
3. Ensure all tests pass
4. Update CHANGELOG.md
5. Submit pull request with clear description

## Reporting Issues

- Use GitHub Issues
- Include steps to reproduce
- Include error messages
- Include Python version and OS

## Code of Conduct

Be respectful and inclusive. We're all here to build something great together.

## Questions?

Feel free to open an issue for questions or discussions.
