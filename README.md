# novel-agent

Dual-mode AI novel writing agent — conversational + autonomous.

```bash
pip install -e .

echo 'ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic' > .env
echo 'ANTHROPIC_AUTH_TOKEN=sk-...' >> .env

novel-agent init ./my-novel --title "My Novel" --total-chapters 800 --chapter-words 3000
novel-agent write --project ./my-novel
novel-agent auto --project ./my-novel --count 5
```

## License

Apache 2.0
