"""One-time importer for the brand-icon library (ADR-018). Run: `make icons`.

Tech/brand logos as app-icon tiles (PNG with alpha), so the Director can show "ChatGPT",
"Claude", "Python"... the moment they are said. Sources (the same sets React apps use):

    Simple Icons  CC0-1.0  https://simpleicons.org  (react-icons ships them as `si`)
    Lobe Icons    MIT      https://github.com/lobehub/lobe-icons  (AI brands Simple Icons dropped)

The SVG *data* is CC0/MIT; the logos are trademarks of their owners. Tiles are recorded as
`commercial_use=false` (ADR-018) until the owner decides otherwise. SVGs are rasterised
locally with rsvg-convert; nothing at runtime touches the network.
"""

import json
import re
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter

SIMPLE = "https://cdn.jsdelivr.net/npm/simple-icons@16"
LOBE = "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1"
TILE = 512
GLYPH = 300
RSVG = shutil.which("rsvg-convert") or "rsvg-convert"

# (id, title, source, source name, extra aliases). Aliases are what people SAY.
ICONS: list[tuple[str, str, str, str, list[str]]] = [
    ("openai", "OpenAI ChatGPT", "lobe", "openai", ["chatgpt", "gpt", "openai", "gpt4", "sora"]),
    ("claude", "Claude", "lobe", "claude", ["claude", "sonnet", "opus"]),
    ("anthropic", "Anthropic", "lobe", "anthropic", ["anthropic"]),
    ("gemini", "Google Gemini", "lobe", "gemini", ["gemini", "bard"]),
    ("deepseek", "DeepSeek", "lobe", "deepseek", ["deepseek"]),
    ("grok", "Grok", "lobe", "grok", ["grok", "xai"]),
    ("perplexity", "Perplexity", "lobe", "perplexity", ["perplexity"]),
    ("mistral", "Mistral AI", "lobe", "mistral", ["mistral"]),
    ("meta-ai", "Meta Llama", "lobe", "meta", ["llama", "meta"]),
    ("copilot", "GitHub Copilot", "lobe", "githubcopilot", ["copilot"]),
    ("cursor", "Cursor", "lobe", "cursor", ["cursor"]),
    ("midjourney", "Midjourney", "lobe", "midjourney", ["midjourney"]),
    ("huggingface", "Hugging Face", "lobe", "huggingface", ["huggingface", "hugging face"]),
    ("ollama", "Ollama", "lobe", "ollama", ["ollama"]),
    ("qwen", "Qwen", "lobe", "qwen", ["qwen"]),
    ("microsoft", "Microsoft", "lobe", "microsoft", ["microsoft", "windows", "azure"]),
    ("nvidia", "NVIDIA", "simple", "nvidia", ["nvidia", "gpu", "gpus", "cuda"]),
    ("google", "Google", "simple", "google", ["google"]),
    ("apple", "Apple", "simple", "apple", ["apple", "iphone", "mac", "macbook"]),
    ("android", "Android", "simple", "android", ["android"]),
    ("meta", "Meta", "simple", "meta", ["facebook"]),
    ("tesla", "Tesla", "simple", "tesla", ["tesla"]),
    ("python", "Python", "simple", "python", ["python"]),
    ("javascript", "JavaScript", "simple", "javascript", ["javascript", "js"]),
    ("typescript", "TypeScript", "simple", "typescript", ["typescript", "ts"]),
    ("react", "React", "simple", "react", ["react", "reactjs"]),
    ("nodejs", "Node.js", "simple", "nodedotjs", ["node", "nodejs"]),
    ("nextjs", "Next.js", "simple", "nextdotjs", ["nextjs"]),
    ("rust", "Rust", "simple", "rust", ["rust"]),
    ("go", "Go", "simple", "go", ["golang"]),
    ("java", "Java", "simple", "openjdk", ["java"]),
    ("cplusplus", "C++", "simple", "cplusplus", ["cpp", "c++"]),
    ("swift", "Swift", "simple", "swift", ["swift"]),
    ("kotlin", "Kotlin", "simple", "kotlin", ["kotlin"]),
    ("flutter", "Flutter", "simple", "flutter", ["flutter"]),
    ("html5", "HTML5", "simple", "html5", ["html"]),
    ("tailwind", "Tailwind CSS", "simple", "tailwindcss", ["tailwind"]),
    ("docker", "Docker", "simple", "docker", ["docker", "container", "containers"]),
    ("kubernetes", "Kubernetes", "simple", "kubernetes", ["kubernetes", "k8s"]),
    ("linux", "Linux", "simple", "linux", ["linux"]),
    ("ubuntu", "Ubuntu", "simple", "ubuntu", ["ubuntu"]),
    ("git", "Git", "simple", "git", ["git"]),
    ("github", "GitHub", "simple", "github", ["github"]),
    ("gitlab", "GitLab", "simple", "gitlab", ["gitlab"]),
    ("stackoverflow", "Stack Overflow", "simple", "stackoverflow", ["stackoverflow"]),
    ("leetcode", "LeetCode", "simple", "leetcode", ["leetcode", "dsa"]),
    ("vim", "Vim", "simple", "vim", ["vim"]),
    ("neovim", "Neovim", "simple", "neovim", ["neovim"]),
    ("postgresql", "PostgreSQL", "simple", "postgresql", ["postgres", "postgresql", "sql"]),
    ("mongodb", "MongoDB", "simple", "mongodb", ["mongodb", "mongo"]),
    ("redis", "Redis", "simple", "redis", ["redis"]),
    ("supabase", "Supabase", "simple", "supabase", ["supabase"]),
    ("firebase", "Firebase", "simple", "firebase", ["firebase"]),
    ("googlecloud", "Google Cloud", "simple", "googlecloud", ["gcp"]),
    ("cloudflare", "Cloudflare", "simple", "cloudflare", ["cloudflare"]),
    ("vercel", "Vercel", "simple", "vercel", ["vercel"]),
    ("digitalocean", "DigitalOcean", "simple", "digitalocean", ["digitalocean"]),
    ("snowflake", "Snowflake", "simple", "snowflake", ["snowflake"]),
    ("tensorflow", "TensorFlow", "simple", "tensorflow", ["tensorflow"]),
    ("pytorch", "PyTorch", "simple", "pytorch", ["pytorch", "torch"]),
    ("langchain", "LangChain", "simple", "langchain", ["langchain"]),
    ("fastapi", "FastAPI", "simple", "fastapi", ["fastapi"]),
    ("django", "Django", "simple", "django", ["django"]),
    ("figma", "Figma", "simple", "figma", ["figma"]),
    ("notion", "Notion", "simple", "notion", ["notion"]),
    ("discord", "Discord", "simple", "discord", ["discord"]),
    ("youtube", "YouTube", "simple", "youtube", ["youtube"]),
    ("instagram", "Instagram", "simple", "instagram", ["instagram", "reels"]),
    ("tiktok", "TikTok", "simple", "tiktok", ["tiktok"]),
    ("x", "X (Twitter)", "simple", "x", ["twitter", "tweet"]),
    ("reddit", "Reddit", "simple", "reddit", ["reddit"]),
    ("whatsapp", "WhatsApp", "simple", "whatsapp", ["whatsapp"]),
    ("spotify", "Spotify", "simple", "spotify", ["spotify"]),
    ("netflix", "Netflix", "simple", "netflix", ["netflix"]),
    ("bitcoin", "Bitcoin", "simple", "bitcoin", ["bitcoin", "btc", "crypto"]),
    ("ethereum", "Ethereum", "simple", "ethereum", ["ethereum", "eth"]),
    ("gmail", "Gmail", "simple", "gmail", ["gmail", "email"]),
]  # fmt: skip

LICENSES = {
    "simple": ("simple-icons", "CC0-1.0", "https://github.com/simple-icons/simple-icons/blob/develop/LICENSE.md"),
    "lobe": ("lobe-icons", "MIT", "https://github.com/lobehub/lobe-icons/blob/master/LICENSE"),
}  # fmt: skip


def _get(client: httpx.Client, url: str) -> bytes | None:
    resp = client.get(url)
    return resp.content if resp.status_code == 200 else None


def _svg(client: httpx.Client, source: str, name: str, colors: dict[str, str]) -> bytes | None:
    """SVG with a visible fill: brand colour for Simple Icons, the colour variant for Lobe."""
    if source == "lobe":
        return _get(client, f"{LOBE}/icons/{name}-color.svg") or _get(
            client, f"{LOBE}/icons/{name}.svg"
        )
    svg = _get(client, f"{SIMPLE}/icons/{name}.svg")
    if svg is None:
        return None
    color = colors.get(name, "000000")
    return re.sub(rb"<svg ", f'<svg fill="#{color}" '.encode(), svg, count=1)


def _tile(png: bytes, dest: Path) -> None:
    """Glyph centred on a white rounded tile with a soft shadow (reads on any background)."""
    glyph = Image.open(BytesIO(png)).convert("RGBA")
    glyph.thumbnail((GLYPH, GLYPH), Image.Resampling.LANCZOS)
    raw = glyph.tobytes()
    pixels = [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4) if raw[i + 3] > 40]
    light = (
        pixels
        and sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b, _ in pixels) / len(pixels) > 225
    )
    face = (24, 24, 28, 255) if light else (255, 255, 255, 255)
    margin, radius = 40, 96
    tile = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [margin, margin + 10, TILE - margin, TILE - margin + 10], radius, fill=(0, 0, 0, 110)
    )
    tile.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
    ImageDraw.Draw(tile).rounded_rectangle(
        [margin, margin, TILE - margin, TILE - margin], radius, fill=face
    )
    tile.alpha_composite(glyph, ((TILE - glyph.width) // 2, (TILE - glyph.height) // 2))
    tile.save(dest, "PNG")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "assets") / "icons"
    root.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        data = json.loads(_get(client, f"{SIMPLE}/data/simple-icons.json") or b"[]")
        colors = {i["slug"]: i["hex"] for i in data if "slug" in i}
        for icon_id, title, source, name, aliases in ICONS:
            svg = _svg(client, source, name, colors)
            if svg is None:
                print(f"skip {icon_id}: {source}/{name} not found")
                continue
            png = subprocess.run(  # noqa: S603 - fixed argv; the SVG goes in on stdin
                [RSVG, "-w", str(GLYPH * 2), "-h", str(GLYPH * 2), "-a"],
                input=svg,
                capture_output=True,
                check=True,
            ).stdout
            _tile(png, root / f"{icon_id}.png")
            pkg, lic, lic_url = LICENSES[source]
            page = (
                f"https://simpleicons.org/?q={name}"
                if source == "simple"
                else f"https://lobehub.com/icons/{name}"
            )
            words = {w for a in [title, *aliases] for w in re.findall(r"[a-z0-9+]+", a.lower())}
            manifest.append(
                {
                    "id": f"icons:{icon_id}",
                    "file": f"{icon_id}.png",
                    "title": title,
                    "tags": sorted(words | {"logo", "icon", "brand"}),
                    "aliases": aliases,
                    "source": pkg,
                    "source_url": page,
                    "license": lic,
                    "license_url": lic_url,
                    "attribution_required": False,
                    "attribution_text": "",
                    "commercial_use": False,  # trademark: owner decision pending (ADR-018)
                    "width": TILE,
                    "height": TILE,
                    "alpha": True,
                    "category": "brand",
                }
            )
            print(f"ok {icon_id}")
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{len(manifest)} icons -> {root}")


if __name__ == "__main__":
    main()
