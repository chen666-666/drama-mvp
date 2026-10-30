import json
from html import escape

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from jinja2 import Environment
from sqlalchemy import select

from database import SessionLocal, init_db
from models import Drama


app = FastAPI(title="短剧灵感库")


@app.on_event("startup")
def create_database_tables() -> None:
    """Ensure a first-time visitor can start the application safely."""
    init_db()


def parse_tags(tags: str) -> list[str]:
    """Read the JSON tag list stored in SQLite, tolerating legacy values."""
    try:
        parsed_tags = json.loads(tags)
        if isinstance(parsed_tags, list):
            return [str(tag) for tag in parsed_tags]
    except json.JSONDecodeError:
        pass
    return []


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    session = SessionLocal()
    try:
        dramas = session.scalars(select(Drama).order_by(Drama.id)).all()
    finally:
        session.close()

    drama_items = [(drama, parse_tags(drama.tags)) for drama in dramas]
    categories = sorted(
        {drama.category for drama in dramas if drama.category}, key=str.casefold
    )
    category_options = Environment(autoescape=True).from_string(
        '{% for category in categories %}<option value="{{ category }}">'
        '{{ category }}</option>{% endfor %}'
    ).render(categories=categories)

    cards = "".join(
        f'''
        <a href="{escape(drama.official_url, quote=True)}" target="_blank" rel="noopener noreferrer"
           data-title="{escape(drama.title, quote=True)}" data-category="{escape(drama.category or '', quote=True)}"
           class="drama-card group block break-inside-avoid overflow-hidden rounded-3xl border border-white/10 bg-slate-900/70 shadow-xl shadow-indigo-950/20 transition duration-300 hover:-translate-y-1 hover:border-fuchsia-300/60 hover:shadow-fuchsia-500/20">
          <div class="relative overflow-hidden">
            <img src="{escape(drama.cover_url, quote=True)}" alt="{escape(drama.title)}封面" class="h-56 w-full object-cover transition duration-500 group-hover:scale-105" />
            <div class="absolute inset-0 bg-gradient-to-t from-slate-950/70 via-transparent to-transparent"></div>
            <span class="absolute bottom-4 right-4 rounded-full bg-amber-300 px-3 py-1 text-sm font-bold text-slate-950">AI {drama.ai_score:.1f}</span>
          </div>
          <div class="p-5">
            <h2 class="text-lg font-bold text-white">{escape(drama.title)}</h2>
            <p class="mt-2 text-sm leading-6 text-slate-300">{escape(drama.ai_summary)}</p>
            <div class="mt-4 flex flex-wrap gap-2">
              {"".join(f'<span class="rounded-full bg-indigo-400/15 px-2.5 py-1 text-xs text-indigo-200 ring-1 ring-inset ring-indigo-300/20">#{escape(tag)}</span>' for tag in tags)}
            </div>
          </div>
        </a>
        '''
        for drama, tags in drama_items
    )
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>短剧灵感库</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-slate-950 text-slate-100">
  <div class="pointer-events-none fixed inset-0 overflow-hidden">
    <div class="absolute -left-24 -top-24 h-80 w-80 rounded-full bg-fuchsia-600/25 blur-3xl"></div>
    <div class="absolute right-0 top-1/3 h-96 w-96 rounded-full bg-indigo-600/20 blur-3xl"></div>
  </div>
  <main class="relative mx-auto max-w-6xl px-5 py-12 sm:px-8 sm:py-16">
    <header class="mb-10 max-w-2xl">
      <p class="mb-3 inline-flex rounded-full border border-fuchsia-300/30 bg-fuchsia-400/10 px-3 py-1 text-sm text-fuchsia-200">AI 精选 · 本周短剧</p>
      <h1 class="text-4xl font-black tracking-tight sm:text-5xl">发现下一部<br /><span class="bg-gradient-to-r from-fuchsia-300 to-amber-200 bg-clip-text text-transparent">让人上头的短剧</span></h1>
      <p class="mt-5 leading-7 text-slate-300">由 AI 整理的高分短剧清单。点击卡片，即可前往官方平台观看。</p>
    </header>

    <div class="mb-8 flex flex-col gap-3 sm:flex-row">
      <label class="sr-only" for="drama-search">搜索短剧</label>
      <input id="drama-search" type="search" placeholder="搜索短剧标题…"
             class="w-full rounded-xl border border-white/10 bg-slate-900/70 px-4 py-3 text-sm text-white outline-none placeholder:text-slate-400 focus:border-fuchsia-300/60 sm:flex-1" />
      <label class="sr-only" for="category-filter">按分类筛选</label>
      <select id="category-filter"
              class="rounded-xl border border-white/10 bg-slate-900/70 px-4 py-3 text-sm text-white outline-none focus:border-fuchsia-300/60 sm:w-52">
        <option value="">全部分类</option>
        {category_options}
      </select>
    </div>

    <p id="no-results" class="hidden mb-6 text-sm text-slate-400">没有找到匹配的短剧。</p>
    <section id="drama-list" class="columns-1 gap-6 sm:columns-2 lg:columns-3">{cards}</section>
  </main>

  <script>
    const searchInput = document.getElementById("drama-search");
    const categoryFilter = document.getElementById("category-filter");
    const cards = Array.from(document.querySelectorAll(".drama-card"));
    const noResults = document.getElementById("no-results");

    function filterDramas() {{
      const query = searchInput.value.trim().toLocaleLowerCase();
      const selectedCategory = categoryFilter.value;
      let visibleCount = 0;

      cards.forEach((card) => {{
        const titleMatches = card.dataset.title.toLocaleLowerCase().includes(query);
        const categoryMatches = !selectedCategory || card.dataset.category === selectedCategory;
        const isVisible = titleMatches && categoryMatches;

        card.style.display = isVisible ? "" : "none";
        if (isVisible) visibleCount += 1;
      }});

      noResults.classList.toggle("hidden", visibleCount !== 0);
    }}

    searchInput.addEventListener("input", filterDramas);
    categoryFilter.addEventListener("change", filterDramas);
  </script>
</body>
</html>'''


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
