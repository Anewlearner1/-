#!/usr/bin/env python3
"""
Literature Analysis Agent
Autonomously reads, analyzes, and reports on academic papers.

Usage:
    python agent.py                          # interactive mode
    python agent.py "analyze transformers"   # custom request
    python agent.py paper.pdf               # analyze specific file
    python agent.py https://arxiv.org/...   # fetch and analyze URL
"""

import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import anthropic

# Optional PDF support
try:
    import pdfplumber

    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

PAPERS_DIR = Path("papers")
REPORTS_DIR = Path("reports")

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an expert academic literature analyst. Your job is to autonomously \
search for, read, analyze, and synthesize academic papers, then produce a comprehensive written report.

## Workflow
1. **Find papers** — use `search_papers` to discover relevant literature on the topic (arXiv).
   Select the most relevant results and fetch them with `fetch_paper`.
   Also call `list_papers` to include any locally available files.
2. **Read** each paper with `read_file` (local) or `fetch_paper` (remote URLs).
   For PDFs always supply `save_as` to fetch_paper, then call `read_file` on the saved file.
3. **Analyse** each paper: research question, methodology, key findings, contributions, limitations.
4. **Synthesise** across papers: common themes, contradictions, research gaps.
5. **Report** — call `write_report` to save the final Markdown report.

Aim to review at least 3–5 papers. Prefer high-quality, well-cited work.

## Report structure
# Literature Analysis Report

## Executive Summary
(2–4 sentence overview of the body of work reviewed)

## Paper Summaries
For each paper:
### [Paper Title]
- **Authors / Source**: ...
- **Research Question**: ...
- **Methodology**: ...
- **Key Findings**: ...
- **Contributions**: ...
- **Limitations**: ...

## Cross-Paper Analysis
- **Common Themes**: ...
- **Contradictions / Debates**: ...
- **Research Gaps**: ...

## Synthesis & Conclusions
(Your integrated assessment)

## References
(list of papers reviewed)

Be critical, thorough, and precise. Note confidence level when information is unclear."""


# ── Tool implementations ──────────────────────────────────────────────────────


def list_papers() -> str:
    PAPERS_DIR.mkdir(exist_ok=True)
    files = [f for f in sorted(PAPERS_DIR.iterdir()) if f.is_file()]
    if not files:
        return (
            "No papers found in 'papers/' directory.\n"
            "Add PDF/TXT/MD files there, or use fetch_paper to download from a URL."
        )
    lines = [f"Available papers ({len(files)} files):"]
    for f in files:
        lines.append(f"  • {f.name}  ({f.stat().st_size / 1024:.1f} KB)")
    return "\n".join(lines)


def read_file(filename: str) -> str:
    # Accept bare name or full path inside papers/
    path = PAPERS_DIR / filename
    if not path.exists():
        path = Path(filename)
    if not path.exists():
        return f"Error: '{filename}' not found. Use list_papers to see available files."

    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            if not PDF_SUPPORT:
                return (
                    "PDF support requires pdfplumber. "
                    "Install it with:  pip install pdfplumber"
                )
            with pdfplumber.open(path) as pdf:
                pages = []
                for i, page in enumerate(pdf.pages, 1):
                    text = (page.extract_text() or "").strip()
                    if text:
                        pages.append(f"--- Page {i} ---\n{text}")
            if not pages:
                return (
                    f"Could not extract text from '{filename}'. "
                    "The PDF may be scanned / image-only."
                )
            return f"[File: {filename}]\n\n" + "\n\n".join(pages)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            return f"[File: {filename}]\n\n{text}"
    except Exception as exc:
        return f"Error reading '{filename}': {exc}"


def fetch_paper(url: str, save_as: str | None = None) -> str:
    PAPERS_DIR.mkdir(exist_ok=True)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "LiteratureAnalysisAgent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
    except Exception as exc:
        return f"Error fetching '{url}': {exc}"

    is_pdf = "pdf" in content_type.lower() or url.lower().endswith(".pdf")

    if save_as:
        dest = PAPERS_DIR / save_as
        dest.write_bytes(data)
        saved_note = f" Saved to papers/{save_as}."
        if is_pdf:
            return (
                f"PDF downloaded.{saved_note} "
                f"Now call read_file('{save_as}') to read its contents."
            )
    else:
        saved_note = ""

    if is_pdf:
        return (
            "PDF downloaded but not saved (no save_as given). "
            "Provide save_as='filename.pdf' to save it, then read with read_file."
        )

    # Text content — strip HTML and return
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if save_as:
        (PAPERS_DIR / save_as).write_text(text, encoding="utf-8")
    return f"[Fetched: {url}]{saved_note}\n\n{text[:60_000]}"


def search_papers(query: str, max_results: int = 5) -> str:
    max_results = min(max(1, max_results), 10)
    encoded = urllib.parse.quote(query)
    url = (
        f"http://export.arxiv.org/api/query"
        f"?search_query=all:{encoded}"
        f"&start=0&max_results={max_results}"
        f"&sortBy=relevance&sortOrder=descending"
    )
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "LiteratureAnalysisAgent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as exc:
        return f"Error querying arXiv: {exc}"

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return f"Error parsing arXiv response: {exc}"

    entries = root.findall("atom:entry", ns)
    if not entries:
        return f"No results found for query: '{query}'"

    lines = [f"arXiv search results for '{query}' ({len(entries)} found):\n"]
    for i, entry in enumerate(entries, 1):
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
        if len(summary) > 300:
            summary = summary[:297] + "…"
        authors = [
            a.findtext("atom:name", "", ns)
            for a in entry.findall("atom:author", ns)
        ]
        published = (entry.findtext("atom:published", "", ns) or "")[:4]
        arxiv_id = ""
        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            rel = link.get("rel", "")
            href = link.get("href", "")
            title_attr = link.get("title", "")
            if title_attr == "pdf" or rel == "related" and "pdf" in href:
                pdf_url = href
            if rel == "alternate":
                arxiv_id = href.split("/abs/")[-1] if "/abs/" in href else ""
        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        lines.append(f"{i}. **{title}**")
        lines.append(f"   Authors: {', '.join(authors[:3])}{' et al.' if len(authors) > 3 else ''}")
        lines.append(f"   Year: {published}  |  arXiv ID: {arxiv_id}")
        lines.append(f"   PDF: {pdf_url}")
        lines.append(f"   Abstract: {summary}")
        lines.append("")
    return "\n".join(lines)


def write_report(filename: str, content: str) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    if not filename.endswith((".md", ".txt")):
        filename += ".md"
    path = REPORTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    lines = len(content.splitlines())
    chars = len(content)
    return f"Report saved: {path}  ({chars:,} characters, {lines} lines)"


# ── Tool schema ───────────────────────────────────────────────────────────────

TOOLS: list[anthropic.types.ToolParam] = [
    {
        "name": "list_papers",
        "description": "List all paper files available in the papers/ directory.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_file",
        "description": (
            "Read the full content of a paper file (PDF, TXT, MD, etc.) "
            "from the papers/ directory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "File name, e.g. 'attention_is_all_you_need.pdf'",
                }
            },
            "required": ["filename"],
        },
    },
    {
        "name": "fetch_paper",
        "description": (
            "Download a paper from a URL. "
            "For PDFs, supply save_as so the file can be read with read_file afterward."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL of the paper or webpage"},
                "save_as": {
                    "type": "string",
                    "description": (
                        "Optional filename (e.g. 'paper.pdf') to save under papers/. "
                        "Required for PDFs."
                    ),
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_papers",
        "description": (
            "Search arXiv for academic papers matching a topic or keywords. "
            "Returns titles, authors, year, arXiv ID, PDF URL, and abstract snippets. "
            "Use the PDF URL with fetch_paper(save_as=...) to download a paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'transformer attention mechanism NLP'",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (1–10, default 5)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "write_report",
        "description": "Save the completed analysis report to the reports/ directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Output filename, e.g. 'analysis_report.md'",
                },
                "content": {
                    "type": "string",
                    "description": "Full Markdown report content",
                },
            },
            "required": ["filename", "content"],
        },
    },
]


# ── Tool dispatcher ───────────────────────────────────────────────────────────


def dispatch(name: str, inputs: dict[str, Any]) -> str:
    match name:
        case "list_papers":
            return list_papers()
        case "read_file":
            return read_file(inputs["filename"])
        case "fetch_paper":
            return fetch_paper(inputs["url"], inputs.get("save_as"))
        case "search_papers":
            return search_papers(inputs["query"], inputs.get("max_results", 5))
        case "write_report":
            return write_report(inputs["filename"], inputs["content"])
        case _:
            return f"Unknown tool: {name}"


# ── Agent loop ────────────────────────────────────────────────────────────────

_DIVIDER = "─" * 60


def run_agent(request: str) -> None:
    print(f"\n{_DIVIDER}")
    print("  Literature Analysis Agent")
    print(_DIVIDER)
    print(f"  Task: {request}")
    print(f"{_DIVIDER}\n")

    messages: list[anthropic.types.MessageParam] = [
        {"role": "user", "content": request}
    ]

    for iteration in range(1, 31):
        print(f"[{iteration}] Thinking...\n")

        with client.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        # Print visible output
        for block in response.content:
            if block.type == "thinking" and block.thinking:
                preview = block.thinking[:160].replace("\n", " ")
                print(f"  [thinking] {preview}…\n")
            elif block.type == "text" and block.text.strip():
                print(block.text)
                print()

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print(f"\n{_DIVIDER}")
            print("  Analysis complete.")
            break

        if response.stop_reason != "tool_use":
            print(f"  Stopped: {response.stop_reason}")
            break

        # Execute every requested tool
        tool_results: list[anthropic.types.ToolResultBlockParam] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_input: dict[str, Any] = block.input  # type: ignore[assignment]
            arg_preview = json.dumps(tool_input, ensure_ascii=False)
            if len(arg_preview) > 80:
                arg_preview = arg_preview[:77] + "…"
            print(f"  ▶ {block.name}({arg_preview})")

            result = dispatch(block.name, tool_input)

            result_preview = result[:200].replace("\n", " ")
            if len(result) > 200:
                result_preview += f"… [{len(result):,} chars total]"
            print(f"    ↳ {result_preview}\n")

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        messages.append({"role": "user", "content": tool_results})
    else:
        print("  Warning: reached iteration limit.")

    # List generated reports
    if REPORTS_DIR.exists():
        reports = sorted(REPORTS_DIR.glob("*.*"))
        if reports:
            print(f"\n  Generated reports:")
            for r in reports:
                size_kb = r.stat().st_size / 1024
                print(f"    📄 {r}  ({size_kb:.1f} KB)")
    print(f"{_DIVIDER}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    PAPERS_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)

    args = sys.argv[1:]

    if not args:
        # Interactive
        print("Literature Analysis Agent")
        print(f"Place papers (PDF/TXT/MD) in '{PAPERS_DIR}/' or provide URLs.")
        task = input("\nTask (Enter = full review): ").strip()
        if not task:
            task = (
                "Read all available papers and produce a comprehensive "
                "literature review report."
            )
    else:
        # Accept file paths, URLs, and free-text instructions mixed together
        papers, instructions = [], []
        for arg in args:
            if arg.startswith("http://") or arg.startswith("https://"):
                papers.append(arg)
            elif Path(arg).exists():
                papers.append(arg)
            else:
                instructions.append(arg)

        # Copy local files into papers/
        for p in papers:
            if not p.startswith("http"):
                src = Path(p)
                dest = PAPERS_DIR / src.name
                if src.resolve() != dest.resolve():
                    shutil.copy(src, dest)
                    print(f"Copied '{src.name}' → papers/")

        if instructions:
            task = " ".join(instructions)
        elif papers:
            names = ", ".join(
                Path(p).name if not p.startswith("http") else p for p in papers
            )
            task = (
                f"Analyze the following papers and generate a report: {names}. "
                "If any are URLs, fetch them first."
            )
        else:
            task = (
                "Read all available papers and produce a comprehensive "
                "literature review report."
            )

    run_agent(task)


if __name__ == "__main__":
    main()
