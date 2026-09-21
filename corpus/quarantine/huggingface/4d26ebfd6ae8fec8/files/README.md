---
language:
- en
license: apache-2.0
task_categories:
- text-generation
- conversational
tags:
- agent
- tool-use
- function-calling
- chatml
- langgraph
- crewai
- reasoning
- self-correction
size_categories:
- 10K<n<100K
dataset_info:
  features:
  - name: messages
    list: json
  splits:
  - name: train
    num_bytes: 6093969
    num_examples: 10000
  download_size: 6066483
  dataset_size: 6093969
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*
---
# Agent Tool Use Trajectories (10K) 🚀

## Dataset Description
This dataset contains 10,000 highly complex, multi-step dialogue trajectories designed to train open-source Large Language Models (LLMs) in advanced **Agent Tool Use, Function Calling, and Reasoning**. 

Curated with professional AI training and data annotation standards, this dataset moves beyond simple synthetic Q&A pairs. It strictly follows the **ChatML** format and focuses heavily on multi-tool orchestration, logical step-by-step reasoning (Chain of Thought), and self-correction mechanisms.

- **Total Rows:** 10,000
- **Format:** ChatML (`messages` list)
- **Supported Frameworks:** Optimized for LangGraph, Agno, CrewAI, and custom Agentic Workflows.

## Tool Scope & Capabilities
The trajectories simulate an autonomous agent interacting with a robust set of real-world tools, including but not limited to:
*   **Database Administration:** Text-to-SQL (PostgreSQL `execute_query`) for complex joins and debugging.
*   **Software Engineering (SWE):** Codebase vulnerability analysis and Git operations (`create_pull_request`, `analyze_code_vulnerabilities`).
*   **System & OS:** Bash execution and file system manipulation (`read_file`, `write_file`, `execute_bash_command`).
*   **Web & RAG:** Web scraping, search integration (e.g., Wikipedia/Tavily), and vector database retrieval.

## Key Features
1. **Strict Schema Adherence:** The agent responses strictly match the JSON schemas provided in the `system` prompt without hallucinating non-existent parameters.
2. **Self-Correction:** A significant portion of the dataset includes scenarios where a tool returns an error (e.g., `SyntaxError`, `FileNotFoundError`). The agent is trained to read the error, re-evaluate its reasoning, and call the tool again with corrected parameters.
3. **Sequential Reasoning:** Enforces step-by-step tool execution rather than overwhelming parallel tool calls, allowing for true dependent reasoning (e.g., read file -> analyze content).

## Data Structure
Each row in the dataset is a dictionary containing a single `messages` key, which holds the ChatML formatted conversation list:

    {
      "messages": [
        {
          "role": "system",
          "content": "You have access to the following tools: [{'name': 'execute_postgres_query', ...}]"
        },
        {
          "role": "user",
          "content": "Find the highest salary in the IT department."
        },
        {
          "role": "assistant",
          "content": "I need to query the database to find the maximum salary.",
          "tool_calls": [
            {
              "name": "execute_postgres_query",
              "arguments": {"query": "SELECT MAX(salary) FROM employees WHERE dept = 'IT';"}
            }
          ]
        },
        {
          "role": "tool",
          "content": "{\"max_salary\": 95000}"
        },
        {
          "role": "assistant",
          "content": "The highest salary in the IT department is 95,000."
        }
      ]
    }

## Intended Use
This dataset is highly recommended for fine-tuning smaller parameter models (e.g., Llama-3-8B, Mistral-7B, Qwen) to grant them reliable function-calling capabilities and transform them into competent core engines for autonomous agent frameworks.