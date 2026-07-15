set -a; source .env; set +a 
uv run orchestrate seed demo --provider openrouter --model nvidia/nemotron-3-ultra-550b-a55b:free --api-key-env OPENROUTER_API_KEY