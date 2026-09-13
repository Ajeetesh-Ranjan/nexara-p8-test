#!/usr/bin/env python3
"""
ENTITY EXTRACTION — turns raw text into candidate world entities.

Deliberately NOT an LLM. This is lexicon + pattern matching, which means:
  - high precision on known technologies/companies (the curated lexicon)
  - real precision on structured identifiers (github owner/repo, arxiv ids)
  - deliberately conservative on free text — capitalised-phrase candidates are
    emitted with `method: "heuristic"` and lower weight so downstream scoring
    can discount them.

Every extracted entity carries the method that produced it. Nothing pretends to
be semantic understanding.
"""
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- lexicon ---
# name -> (type, aliases). Aliases are matched case-insensitively as whole words.
LEXICON: dict[str, tuple[str, list[str]]] = {
    # protocols / standards
    "Model Context Protocol": ("protocol", ["MCP", "model context protocol"]),
    "Agent2Agent Protocol": ("protocol", ["A2A", "agent2agent", "agent-to-agent"]),
    "OpenAPI": ("protocol", ["openapi", "swagger"]),
    "gRPC": ("protocol", ["grpc"]),
    "WebRTC": ("protocol", ["webrtc"]),
    "OAuth": ("protocol", ["oauth", "oauth2"]),

    # model families / labs
    "GPT": ("model", ["gpt-4", "gpt-5", "gpt4", "chatgpt", "gpt-4o"]),
    "Claude": ("model", ["claude", "claude-3", "claude-4", "opus", "sonnet"]),
    "Gemini": ("model", ["gemini", "bard"]),
    "Llama": ("model", ["llama", "llama-3", "llama3", "llama-4"]),
    "DeepSeek": ("model", ["deepseek", "deepseek-v3", "deepseek-r1"]),
    "Qwen": ("model", ["qwen", "qwen2", "qwen3"]),
    "Mistral": ("model", ["mistral", "mixtral"]),
    "Whisper": ("model", ["whisper"]),
    "Stable Diffusion": ("model", ["stable diffusion", "sdxl"]),

    # companies / labs
    "OpenAI": ("company", ["openai", "open ai"]),
    "Anthropic": ("company", ["anthropic"]),
    "Google DeepMind": ("company", ["deepmind", "google deepmind"]),
    "Meta AI": ("company", ["meta ai", "fair", "facebook ai"]),
    "Microsoft": ("company", ["microsoft", "msft"]),
    "NVIDIA": ("company", ["nvidia", "nvda"]),
    "Hugging Face": ("company", ["hugging face", "huggingface", "hf"]),
    "Mistral AI": ("company", ["mistral ai"]),
    "Cohere": ("company", ["cohere"]),
    "Perplexity": ("company", ["perplexity"]),
    "Cloudflare": ("company", ["cloudflare"]),
    "Vercel": ("company", ["vercel"]),
    "Supabase": ("company", ["supabase"]),
    "Databricks": ("company", ["databricks"]),
    "Snowflake": ("company", ["snowflake"]),
    "Stripe": ("company", ["stripe"]),
    "Shopify": ("company", ["shopify"]),
    "Apple": ("company", ["apple", "aapl"]),
    "Amazon": ("company", ["amazon", "aws"]),
    "Oracle": ("company", ["oracle"]),
    "Hostinger": ("company", ["hostinger"]),

    # agent / orchestration tooling
    "LangChain": ("framework", ["langchain"]),
    "LlamaIndex": ("framework", ["llamaindex", "llama index", "llama-index"]),
    "CrewAI": ("framework", ["crewai", "crew ai"]),
    "AutoGen": ("framework", ["autogen"]),
    "LangGraph": ("framework", ["langgraph"]),
    "Semantic Kernel": ("framework", ["semantic kernel"]),
    "Haystack": ("framework", ["haystack"]),
    "DSPy": ("framework", ["dspy"]),
    "vLLM": ("framework", ["vllm"]),
    "Ollama": ("framework", ["ollama"]),
    "llama.cpp": ("framework", ["llama.cpp", "llamacpp", "ggml", "gguf"]),
    "LM Studio": ("framework", ["lm studio", "lmstudio"]),
    "Ray": ("framework", ["ray serve", "rayserve"]),
    "Temporal": ("framework", ["temporal.io"]),
    "n8n": ("framework", ["n8n"]),
    "PyTorch": ("framework", ["pytorch", "torch"]),
    "TensorFlow": ("framework", ["tensorflow"]),
    "JAX": ("framework", ["jax", "flax"]),
    "Transformers": ("framework", ["huggingface transformers", "transformers library"]),
    "scikit-learn": ("framework", ["scikit-learn", "sklearn"]),
    "FastAPI": ("framework", ["fastapi"]),
    "Next.js": ("framework", ["next.js", "nextjs"]),

    # infrastructure
    "Kubernetes": ("infrastructure", ["kubernetes", "k8s", "k3s"]),
    "Docker": ("infrastructure", ["docker", "containerd", "oci image"]),
    "Proxmox": ("infrastructure", ["proxmox"]),
    "Terraform": ("infrastructure", ["terraform", "opentofu"]),
    "NixOS": ("infrastructure", ["nixos", "nix flake"]),
    "systemd": ("infrastructure", ["systemd"]),
    "Traefik": ("infrastructure", ["traefik"]),
    "NATS": ("infrastructure", ["nats.io", "nats jetstream"]),
    "Redis": ("infrastructure", ["redis", "valkey"]),
    "PostgreSQL": ("infrastructure", ["postgresql", "postgres", "pgvector"]),
    "SQLite": ("infrastructure", ["sqlite", "libsql", "turso"]),
    "DuckDB": ("infrastructure", ["duckdb"]),
    "Neo4j": ("infrastructure", ["neo4j", "cypher query"]),
    "Qdrant": ("infrastructure", ["qdrant"]),
    "ClickHouse": ("infrastructure", ["clickhouse"]),

    # techniques / concepts
    "Retrieval Augmented Generation": ("technique", ["rag", "retrieval augmented", "retrieval-augmented"]),
    "Fine-tuning": ("technique", ["fine-tuning", "fine tuning", "finetuning", "lora", "qlora"]),
    "Quantization": ("technique", ["quantization", "quantisation", "int4", "int8", "gptq", "awq"]),
    "Mixture of Experts": ("technique", ["mixture of experts", "moe"]),
    "Reinforcement Learning": ("technique", ["reinforcement learning", "rlhf", "rlaif", "grpo", "dpo"]),
    "Chain of Thought": ("technique", ["chain of thought", "chain-of-thought", "cot"]),
    "Agentic AI": ("technique", ["agentic ai", "agentic", "ai agents", "autonomous agents"]),
    "Multi-agent Systems": ("technique", ["multi-agent", "multi agent", "agent orchestration"]),
    "Local Inference": ("technique", ["local inference", "on-device", "edge inference", "self-hosted llm"]),
    "Knowledge Graph": ("technique", ["knowledge graph", "knowledge graphs"]),
    "Vector Search": ("technique", ["vector search", "vector database", "embeddings", "semantic search"]),
    "Speculative Decoding": ("technique", ["speculative decoding"]),
    "Context Engineering": ("technique", ["context engineering", "context window", "long context"]),
    "Observability": ("technique", ["observability", "opentelemetry", "tracing"]),
}

# Build a fast alias -> canonical map
_ALIAS: dict[str, tuple[str, str]] = {}
for canon, (etype, aliases) in LEXICON.items():
    _ALIAS[canon.lower()] = (canon, etype)
    for a in aliases:
        _ALIAS[a.lower()] = (canon, etype)

# Longest aliases first so "model context protocol" wins over "protocol"
_ALIAS_SORTED = sorted(_ALIAS.keys(), key=len, reverse=True)
_ALIAS_PATTERNS = [
    (a, re.compile(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])", re.IGNORECASE))
    for a in _ALIAS_SORTED
]

# ------------------------------------------------------------- heuristics ---
_STOP = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "We", "I", "You",
    "How", "Why", "What", "When", "Where", "Who", "Show", "Ask", "Tell", "New",
    "Best", "Top", "First", "Last", "Next", "One", "Two", "Three", "My", "Our",
    "Your", "His", "Her", "Their", "Its", "Is", "Are", "Was", "Were", "Be",
    "Have", "Has", "Had", "Do", "Does", "Did", "Can", "Could", "Will", "Would",
    "Should", "May", "Might", "Must", "Not", "No", "Yes", "And", "But", "Or",
    "If", "Then", "Else", "For", "With", "From", "Into", "Over", "Under",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "HN", "PDF", "USA", "US",
    # observed leaking through the multi-source gate on live data:
    "Here", "There", "Language", "Model", "Models", "Data", "System", "Systems",
    "Using", "Use", "Learning", "Training", "Research", "Paper", "Papers",
    "Introducing", "Announcing", "Building", "Built", "Make", "Making", "Made",
    "Why", "Way", "Ways", "Thing", "Things", "Time", "Times", "Year", "Years",
    "Day", "Days", "Week", "Weeks", "Month", "Months", "People", "Team", "Teams",
    "Work", "Working", "Project", "Projects", "Code", "Software", "Computer",
    "Open", "Source", "Free", "Full", "Real", "More", "Most", "Some", "Many",
    "Every", "Each", "Only", "Just", "Now", "Today", "Tomorrow", "Yesterday",
    "After", "Before", "During", "Since", "Until", "While", "About", "Above",
    "Get", "Getting", "Got", "Let", "Lets", "Take", "Taking", "Give", "Giving",
    "Toward", "Towards", "Through", "Between", "Against", "Without", "Within",
    "Introduction", "Overview", "Guide", "Tutorial", "Series", "Part", "Note",
    "Notes", "Update", "Updates", "Release", "News", "Report", "Study", "Case",
}

# Leading verbs in headline-case titles ("Build Systems", "Using Docker")
_LEAD_VERBS = {
    "build", "building", "built", "make", "making", "use", "using", "used",
    "create", "creating", "run", "running", "write", "writing", "learn",
    "learning", "understand", "understanding", "improve", "improving",
    "add", "adding", "remove", "removing", "fix", "fixing", "test", "testing",
    "deploy", "deploying", "scale", "scaling", "train", "training", "tune",
    "tuning", "ship", "shipping", "launch", "launching", "start", "starting",
    "stop", "stopping", "find", "finding", "show", "showing", "ask", "asking",
    "meet", "meeting", "introduce", "introducing", "announce", "announcing",
}

# Feed/RSS boilerplate and section headers — template text, never entities.
_BOILERPLATE = {
    "article url", "comments url", "read more", "continue reading", "click here",
    "market talk", "market talks", "telecom roundup", "press release",
    "sponsored content", "advertisement", "subscribe now", "sign up",
    "related articles", "related stories", "top stories", "breaking news",
    "full story", "source link", "original article", "view comments",
    "points by", "share this", "follow us", "privacy policy", "terms of service",
    "all rights reserved", "getty images", "associated press", "staff writer",
    "editor note", "editors note", "correction", "updated on", "published on",
    "photo credit", "image credit", "file photo", "live updates", "watch live",
    "listen live", "podcast episode", "newsletter signup", "email newsletter",
}

# Generic single words that are never a useful standalone entity even when capitalised
_GENERIC_SINGLE = {
    "language", "model", "data", "system", "network", "networks", "agent",
    "agents", "tool", "tools", "platform", "service", "services", "api",
    "framework", "library", "app", "apps", "web", "cloud", "server", "client",
    "user", "users", "developer", "developers", "company", "companies",
    "market", "markets", "product", "products", "business", "technology",
    "intelligence", "future", "world", "state", "problem", "solution",
    "question", "answer", "example", "version", "support", "feature",
}

_GITHUB_REPO = re.compile(r"(?<![\w/])([A-Za-z0-9][\w.-]{0,38})/([A-Za-z0-9][\w.-]{0,99})(?![\w/])")
_CAP_PHRASE = re.compile(r"\b([A-Z][a-zA-Z0-9]{2,}(?:\s+[A-Z][a-zA-Z0-9]{2,}){0,3})\b")
_URL = re.compile(r"https?://\S+")
# Search qualifiers from collector queries (topic:ai, stars:>50, cat:cs.AI)
_QUALIFIER = re.compile(r"\b\w+:[<>=]?[\w.:>-]+")


@dataclass
class ExtractedEntity:
    name: str
    type: str
    method: str           # "lexicon" | "identifier" | "heuristic"
    weight: float         # confidence in the extraction itself, not the claim
    contexts: list[str] = field(default_factory=list)


def extract(text: str, url: str = "", max_heuristic: int = 4) -> list[ExtractedEntity]:
    """Extract entities from one document. Order: lexicon, identifiers, heuristics."""
    if not text:
        return []

    found: dict[str, ExtractedEntity] = {}
    clean = _QUALIFIER.sub(" ", _URL.sub(" ", text))

    # 1. lexicon — highest precision
    for alias, pat in _ALIAS_PATTERNS:
        if pat.search(clean):
            canon, etype = _ALIAS[alias]
            if canon not in found:
                found[canon] = ExtractedEntity(canon, etype, "lexicon", 1.0)

    # 2. structured identifiers — github owner/repo out of the URL only.
    #    Free-text slashes produce too much garbage to be worth it.
    if "github.com" in url:
        m = _GITHUB_REPO.search(url.split("github.com/", 1)[-1])
        if m:
            repo = f"{m.group(1)}/{m.group(2)}"
            if repo.count("/") == 1 and repo not in found:
                found[repo] = ExtractedEntity(repo, "open-source-project", "identifier", 0.9)

    # 3. capitalised phrases — candidates only, capped and down-weighted
    heur = 0
    for m in _CAP_PHRASE.finditer(clean):
        if heur >= max_heuristic:
            break
        phrase = m.group(1).strip()
        words = phrase.split()
        head = words[0]
        if head in _STOP or phrase.lower() in _ALIAS:
            continue
        if len(phrase) < 4 or phrase in found:
            continue
        if phrase.isupper() and len(phrase) <= 3:
            continue
        # every word being a stopword-ish generic makes it noise, not an entity
        if all(w in _STOP for w in words):
            continue
        # headline-case verb phrases ("Build Systems", "Using Docker") are not entities
        if head.lower() in _LEAD_VERBS:
            continue
        # feed boilerplate ("Article URL", "Market Talk") is template text, not news
        if phrase.lower() in _BOILERPLATE:
            continue
        # trailing generic noun with a generic head -> noise ("Many Ways", "Some Things")
        if len(words) == 2 and words[1].lower() in _GENERIC_SINGLE and words[0] in _STOP:
            continue
        # a single generic word ("Language", "Model") is never an entity on its own
        if len(words) == 1 and phrase.lower() in _GENERIC_SINGLE:
            continue
        # single capitalised words are too weak unless they look like a proper name
        # (contain an internal capital/digit, e.g. "PyTorch", "GPT4") — otherwise
        # require at least two words.
        if len(words) == 1 and not re.search(r"[A-Z0-9].*[A-Z0-9]", phrase[1:] + "X"):
            if not re.search(r"[a-z][A-Z]|\d", phrase):
                continue
        found[phrase] = ExtractedEntity(phrase, "unclassified", "heuristic", 0.35)
        heur += 1

    return list(found.values())


def lexicon_size() -> dict:
    types: dict[str, int] = {}
    for _canon, (etype, _a) in LEXICON.items():
        types[etype] = types.get(etype, 0) + 1
    return {"canonical_entities": len(LEXICON), "aliases": len(_ALIAS), "by_type": types}


if __name__ == "__main__":
    sample = ("Show HN: I built an agent orchestration layer on MCP and A2A. "
              "It runs Llama locally with vLLM and stores embeddings in Qdrant. "
              "DeepSeek released a new MoE model today.")
    for e in extract(sample, "https://github.com/foo/bar"):
        print(f"  {e.method:11} {e.weight:.2f}  {e.type:20} {e.name}")
    print(lexicon_size())
