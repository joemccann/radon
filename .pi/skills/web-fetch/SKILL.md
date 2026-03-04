---
name: web-fetch
description: Web searching and content fetching. Use Exa MCP tools (web_search_exa, company_research_exa, get_code_context_exa) as the PRIMARY method for web search, company research, and content retrieval. Fall back to agent-browser only when Exa cannot handle the task (e.g., interactive forms, authentication, screenshots, JavaScript-rendered content). Triggers include "search the web", "find information about", "look up", "research this company", "fetch this page", "scrape data from a page", "open a website", or any task requiring web content.
allowed-tools: Bash(agent-browser:*),mcp__exa__*
---

# Web Search & Fetch

## Tool Priority

| Priority | Tool | When to Use |
|----------|------|-------------|
| **1st** | **Exa MCP** (`web_search_exa`, `company_research_exa`, `get_code_context_exa`) | Web search, company research, code/docs lookup. Fast, clean, structured results. |
| **2nd** | **agent-browser** | Interactive pages, form filling, authentication, screenshots, JS-rendered content, multi-step browser workflows. |

**Default to Exa for any search or content retrieval.** Only use agent-browser when you need browser interactivity.

---

## Exa MCP Tools

### `web_search_exa` — General Web Search
Search the web for any topic and get clean, ready-to-use content.

```
Use: Finding articles, news, documentation, market data, financial analysis
```

### `company_research_exa` — Company Research
Research any company to get business information, news, and insights.

```
Use: Company fundamentals, news, competitive analysis, earnings context
```

### `get_code_context_exa` — Code & Documentation
Find code examples, documentation, and programming solutions.

```
Use: API docs, code patterns, library usage, technical references
```

### Examples

```
# Search for recent news about a ticker
web_search_exa("NVDA dark pool activity March 2026")

# Research a company
company_research_exa("Rambus Inc semiconductor IP")

# Find API documentation
get_code_context_exa("ib_insync placeOrder clientId scope")
```

---

## agent-browser (Fallback)

Use agent-browser only when Exa cannot handle the task:

- **Interactive pages**: Forms, login flows, multi-step navigation
- **Screenshots**: Visual capture of charts, pages, UI
- **JavaScript-rendered content**: SPAs that don't serve content to crawlers
- **Authentication**: Sites requiring login
- **File downloads**: PDFs, CSVs, data exports

### Core Workflow

1. **Navigate**: `agent-browser open <url>`
2. **Snapshot**: `agent-browser snapshot -i` (get element refs like `@e1`, `@e2`)
3. **Interact**: Use refs to click, fill, select
4. **Re-snapshot**: After navigation or DOM changes, get fresh refs

```bash
agent-browser open https://example.com/form
agent-browser snapshot -i
# Output: @e1 [input type="email"], @e2 [input type="password"], @e3 [button] "Submit"

agent-browser fill @e1 "user@example.com"
agent-browser fill @e2 "password123"
agent-browser click @e3
agent-browser wait --load networkidle
agent-browser snapshot -i  # Check result
```

### Essential Commands

```bash
# Navigation
agent-browser open <url>              # Navigate (aliases: goto, navigate)
agent-browser close                   # Close browser

# Snapshot
agent-browser snapshot -i             # Interactive elements with refs (recommended)
agent-browser snapshot -i -C          # Include cursor-interactive elements

# Interaction (use @refs from snapshot)
agent-browser click @e1               # Click element
agent-browser fill @e2 "text"         # Clear and type text
agent-browser select @e1 "option"     # Select dropdown option
agent-browser press Enter             # Press key
agent-browser scroll down 500         # Scroll page

# Get information
agent-browser get text @e1            # Get element text
agent-browser get url                 # Get current URL

# Wait
agent-browser wait @e1                # Wait for element
agent-browser wait --load networkidle # Wait for network idle

# Capture
agent-browser screenshot              # Screenshot to temp dir
agent-browser screenshot --full       # Full page screenshot
agent-browser pdf output.pdf          # Save as PDF
```

### Trading-Specific Examples

```bash
# Fetch a stock page (when Exa doesn't have the specific data)
agent-browser open "https://finance.yahoo.com/quote/AAPL"
agent-browser wait --load networkidle
agent-browser snapshot -i -c

# Screenshot for documentation
agent-browser open "https://example.com/chart"
agent-browser wait --load networkidle
agent-browser screenshot --full reports/chart.png
```

### Ref Lifecycle (Important)

Refs (`@e1`, `@e2`, etc.) are invalidated when the page changes. Always re-snapshot after:
- Clicking links or buttons that navigate
- Form submissions
- Dynamic content loading (dropdowns, modals)
