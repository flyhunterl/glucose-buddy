# Contributing to Glucose Buddy

Thank you for your interest in contributing to Glucose Buddy! 🎉

## 🚀 Getting Started

1. **Fork the repository**
2. **Clone your fork**
   ```bash
   git clone https://github.com/your-username/glucose-buddy.git
   cd glucose-buddy
   ```
3. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

## 🛠️ Development Setup

### Prerequisites
- Docker and Docker Compose
- Python 3.9+ (for local development)
- Git

### Local Development
1. **Copy configuration**
   ```bash
   cp config.toml.example config.toml
   ```
2. **Edit configuration** with your Nightscout and AI service details
3. **Run with Docker**
   ```bash
   docker-compose up -d
   ```

## 📝 Code Guidelines

### Python Code Style
- Follow PEP 8
- Use meaningful variable names
- Add docstrings for functions and classes
- Keep functions focused and small

### Frontend Guidelines
- Use Bootstrap 5 classes for styling
- Ensure mobile responsiveness
- Test on different screen sizes
- Follow accessibility best practices

### Commit Messages
Use conventional commits format:
```
type(scope): description

Examples:
feat(ui): add HbA1c estimation display
fix(api): resolve glucose data sync issue
docs(readme): update installation instructions
```

## 🧪 Testing

Before submitting a PR:
1. Test Docker build: `docker-compose build`
2. Test functionality with real Nightscout data
3. Check mobile responsiveness
4. Verify configuration page works

## 📋 Pull Request Process

1. **Update documentation** if needed
2. **Test your changes** thoroughly
3. **Create a pull request** with:
   - Clear description of changes
   - Screenshots for UI changes
   - Testing steps

## 🐛 Bug Reports

When reporting bugs, please include:
- Operating system and browser
- Docker version
- Steps to reproduce
- Expected vs actual behavior
- Screenshots if applicable

## 💡 Feature Requests

For new features:
- Check existing issues first
- Describe the use case
- Explain why it would be valuable
- Consider implementation complexity

## 📞 Getting Help

- Open an issue for bugs or questions
- Check existing documentation first
- Be specific about your problem

## 🙏 Recognition

Contributors will be recognized in the README.md file.

Thank you for helping make Glucose Buddy better! 🩺✨
