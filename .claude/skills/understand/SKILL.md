---
name: understand
description: Scan a large codebase to build a knowledge graph of all files, functions, classes, and their dependencies, then render an interactive dashboard to visualize architecture and guide reading order. Use this skill when the user wants to understand an unfamiliar or large codebase.
---

# Understand — Codebase Knowledge Graph

This skill performs a multi-agent scan of your project, builds a dependency knowledge graph, and generates an interactive React dashboard for visual architecture exploration.

## Trigger

Use this skill when the user says:
- "Help me understand this codebase"
- "Map the architecture"
- "What are the dependencies between these files?"
- "Where should I start reading this project?"
- "Build a knowledge graph of this repo"

## Workflow

### Step 1: Scan the Codebase

Traverse all source files and extract:
- **Files**: path, language, line count, purpose summary
- **Functions/Methods**: name, signature, docstring, file location
- **Classes**: name, methods, properties, inheritance
- **Dependencies**: imports, exports, function calls between files

Focus on source code directories (skip `node_modules`, `dist`, `.git`, build artifacts).

### Step 2: Build the Knowledge Graph

Construct a graph structure as JSON:
```json
{
  "nodes": [
    {
      "id": "src/auth/login.ts",
      "type": "file",
      "summary": "Handles user authentication flow",
      "functions": ["login", "logout", "refreshToken"],
      "language": "typescript"
    }
  ],
  "edges": [
    {
      "from": "src/app.ts",
      "to": "src/auth/login.ts",
      "relationship": "imports"
    }
  ]
}
```

Save to `.claude/knowledge-graph.json`.

### Step 3: Generate Plain-English Summaries

For each node, write a one-sentence plain-English summary explaining:
- What it does
- Why it exists
- Who calls it / what it depends on

### Step 4: Identify Architecture Layers

Group files into architectural layers:
- **Entry points**: main files, index files, CLI entry points
- **Core logic**: business logic, domain models
- **Infrastructure**: database, API clients, external services
- **Utilities**: shared helpers, constants, types
- **Tests**: test files and fixtures

### Step 5: Recommend Reading Order

Based on the dependency graph, suggest an optimal reading order:
1. Start with entry points to understand the overall flow
2. Follow the most-imported modules (high in-degree = high importance)
3. Read leaf nodes (no dependencies) before nodes that use them

### Step 6: Answer Questions

Once the graph is built, answer natural language questions about the codebase by querying the knowledge graph rather than re-reading source files:
- "What calls the `processPayment` function?"
- "Which files depend on the database layer?"
- "What is the data flow from API request to database?"

## Output Format

Present findings as:
1. **Architecture summary** (3-5 sentences)
2. **Layer diagram** (ASCII or Mermaid)
3. **Recommended reading order** (numbered list)
4. **Key files to know** (top 5-10 most central nodes)

Source: https://github.com/Lum1104/Understand-Anything
