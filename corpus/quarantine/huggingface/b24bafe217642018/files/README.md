---
language:
- en
task_categories:
- text-generation
tags:
- agent
- multi-turn
- tool-use
- reasoning
- interactive
size_categories:
- 1M<n<10M

configs:
- config_name: agenttuning_alfworld
  data_files:
  - split: raw
    path: agenttuning_alfworld/full_raw.jsonl
  - split: std
    path: agenttuning_alfworld/full_std.jsonl
  - split: sft_openhands
    path: agenttuning_alfworld/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: agenttuning_alfworld/full_sft/full_sft_sweagent.jsonl
- config_name: agenttuning_db
  data_files:
  - split: raw
    path: agenttuning_db/full_raw.jsonl
  - split: std
    path: agenttuning_db/full_std.jsonl
  - split: sft_openhands
    path: agenttuning_db/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: agenttuning_db/full_sft/full_sft_sweagent.jsonl
- config_name: agenttuning_kg
  data_files:
  - split: raw
    path: agenttuning_kg/full_raw.jsonl
  - split: std
    path: agenttuning_kg/full_std.jsonl
  - split: sft_openhands
    path: agenttuning_kg/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: agenttuning_kg/full_sft/full_sft_sweagent.jsonl
- config_name: agenttuning_mind2web
  data_files:
  - split: raw
    path: agenttuning_mind2web/full_raw.jsonl
  - split: std
    path: agenttuning_mind2web/full_std.jsonl
  - split: sft_openhands
    path: agenttuning_mind2web/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: agenttuning_mind2web/full_sft/full_sft_sweagent.jsonl
- config_name: agenttuning_os
  data_files:
  - split: raw
    path: agenttuning_os/full_raw.jsonl
  - split: std
    path: agenttuning_os/full_std.jsonl
  - split: sft_openhands
    path: agenttuning_os/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: agenttuning_os/full_sft/full_sft_sweagent.jsonl
- config_name: agenttuning_webshop
  data_files:
  - split: raw
    path: agenttuning_webshop/full_raw.jsonl
  - split: std
    path: agenttuning_webshop/full_std.jsonl
  - split: sft_openhands
    path: agenttuning_webshop/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: agenttuning_webshop/full_sft/full_sft_sweagent.jsonl
- config_name: code_feedback
  data_files:
  - split: raw
    path: code_feedback/full_raw.jsonl
  - split: std
    path: code_feedback/full_std.jsonl
  - split: sft_openhands
    path: code_feedback/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: code_feedback/full_sft/full_sft_sweagent.jsonl
- config_name: codeactinstruct
  data_files:
  - split: raw
    path: codeactinstruct/full_raw.jsonl
  - split: std
    path: codeactinstruct/full_std.jsonl
  - split: sft_openhands
    path: codeactinstruct/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: codeactinstruct/full_sft/full_sft_sweagent.jsonl
- config_name: go-browse-wa
  data_files:
  - split: raw
    path: go-browse-wa/full_raw.jsonl
  - split: std
    path: go-browse-wa/full_std.jsonl
  - split: sft_openhands
    path: go-browse-wa/full_sft/full_sft_openhands.jsonl
  - split: sft_agentlab
    path: go-browse-wa/full_sft/full_sft_agentlab.jsonl
- config_name: mind2web
  data_files:
  - split: raw
    path: mind2web/full_raw.jsonl
  - split: std
    path: mind2web/full_std.jsonl
  - split: sft_openhands
    path: mind2web/full_sft/full_sft_openhands.jsonl
  - split: sft_agentlab
    path: mind2web/full_sft/full_sft_agentlab.jsonl
- config_name: nebius_SWE-agent-trajectories
  data_files:
  - split: raw
    path: nebius_SWE-agent-trajectories/full_raw.jsonl
  - split: std
    path: nebius_SWE-agent-trajectories/full_std.jsonl
  - split: sft_openhands
    path: nebius_SWE-agent-trajectories/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: nebius_SWE-agent-trajectories/full_sft/full_sft_sweagent.jsonl
- config_name: nnetnav-live
  data_files:
  - split: raw
    path: nnetnav-live/full_raw.jsonl
  - split: std
    path: nnetnav-live/full_std.jsonl
  - split: sft_openhands
    path: nnetnav-live/full_sft/full_sft_openhands.jsonl
  - split: sft_agentlab
    path: nnetnav-live/full_sft/full_sft_agentlab.jsonl
- config_name: nnetnav-wa
  data_files:
  - split: raw
    path: nnetnav-wa/full_raw.jsonl
  - split: std
    path: nnetnav-wa/full_std.jsonl
  - split: sft_openhands
    path: nnetnav-wa/full_sft/full_sft_openhands.jsonl
  - split: sft_agentlab
    path: nnetnav-wa/full_sft/full_sft_agentlab.jsonl
- config_name: openhands
  data_files:
  - split: raw
    path: openhands/full_raw.jsonl
  - split: std
    path: openhands/full_std.jsonl
  - split: sft_openhands
    path: openhands/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: openhands/full_sft/full_sft_sweagent.jsonl
- config_name: orca_agentinstruct
  data_files:
  - split: raw
    path: orca_agentinstruct/full_raw.jsonl
  - split: std
    path: orca_agentinstruct/full_std.jsonl
  - split: sft_openhands
    path: orca_agentinstruct/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: orca_agentinstruct/full_sft/full_sft_sweagent.jsonl
- config_name: swe-gym_openhands_sampled_trajectories
  data_files:
  - split: raw
    path: swe-gym_openhands_sampled_trajectories/full_raw.jsonl
  - split: std
    path: swe-gym_openhands_sampled_trajectories/full_std.jsonl
  - split: sft_openhands
    path: swe-gym_openhands_sampled_trajectories/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: swe-gym_openhands_sampled_trajectories/full_sft/full_sft_sweagent.jsonl
- config_name: swe-smith
  data_files:
  - split: raw
    path: swe-smith/full_raw.jsonl
  - split: std
    path: swe-smith/full_std.jsonl
  - split: sft_openhands
    path: swe-smith/full_sft/full_sft_openhands.jsonl
  - split: sft_sweagent
    path: swe-smith/full_sft/full_sft_sweagent.jsonl
- config_name: synatra
  data_files:
  - split: raw
    path: synatra/full_raw.jsonl
  - split: std
    path: synatra/full_std.jsonl
  - split: sft_openhands
    path: synatra/full_sft/full_sft_openhands.jsonl
  - split: sft_agentlab
    path: synatra/full_sft/full_sft_agentlab.jsonl
viewer: true
---

# Agent Data Collection

A comprehensive collection of agent interaction datasets for training and evaluating AI agents across diverse domains and tasks. 
This dataset aggregates high-quality agent trajectories from various environments including web browsing, code generation, household tasks, knowledge base querying, and software engineering.
The dataset is collected through methods described in [Agent Data Protocol](https://arxiv.org/abs/2510.24702).

## Dataset Splits

Each dataset configuration provides up to different splits depending on availability:

### Split Types

| Split | Description | File Path |
|-------|-------------|-----------|
| **`raw`** | Original unprocessed agent trajectories | `{dataset}/full_raw.jsonl` |
| **`std`** | Standardized format with consistent structure | `{dataset}/full_std.jsonl` |
| **`sft_openhands`** | Converted to OpenHands agent finetuning format | `{dataset}/full_sft/full_sft_openhands.jsonl` |
| **`sft_sweagent`** | Converted to SWE-agent finetuning format | `{dataset}/full_sft/full_sft_sweagent.jsonl` |
| **`sft_agentlab`** | Converted to AgentLab finetuning format | `{dataset}/full_sft/full_sft_agentlab.jsonl` |


## Repository Structure

Each dataset in the collection follows a consistent structure:

```
dataset_name/
├── README.md              # Dataset-specific documentation
├── LICENSE               # Dataset-specific license information
├── full_raw.jsonl       # Original raw data format
├── full_std.jsonl       # ADP standardized format
└── full_sft/            # Agent-specific SFT formats
    ├── full_sft_openhands.jsonl    # OpenHands agent format
    ├── full_sft_sweagent.jsonl     # SWE-agent format
    └── full_sft_agentlab.jsonl     # AgentLab format
```

### File Descriptions

- **`full_raw.jsonl`**: Contains the original dataset in its native format before any processing
- **`full_std.jsonl`**: Standardized format following ADP schema with unified action/observation structure
- **`full_sft/`**: Directory containing agent-specific training formats:
  - **`full_sft_openhands.jsonl`**: Formatted for [OpenHands](https://github.com/OpenHands/OpenHands) agent training
  - **`full_sft_sweagent.jsonl`**: Formatted for [SWE-agent](https://github.com/SWE-agent/SWE-agent) training
  - **`full_sft_agentlab.jsonl`**: Formatted for [AgentLab](https://github.com/ServiceNow/AgentLab) training

### Standardized Format (ADP Schema)

The standardized format (`full_std.jsonl`) follows the Agent Data Protocol schema. Each example contains:

```json
{
  "id": "unique_identifier",
  "content": [
    {
      "class_": "text_observation",
      "content": "observation_text",
      "name": null,
      "source": "user"
    },
    {
      "class_": "message_action", 
      "content": "agent_message",
      "description": "optional_reasoning"
    },
    {
      "class_": "api_action",
      "function": "function_name",
      "kwargs": {"param": "value"},
      "description": "reasoning_for_action"
    },
    ......
  ],
  "details": {}
}
```

**Key Components:**
- **`id`**: Unique identifier for the interaction session
- **`content`**: Sequential list of actions and observations in the agent trajectory
- **`details`**: Additional metadata (typically empty)

Please check out the paper for more details.

### SFT Format

The SFT (Supervised Fine-Tuning) format (`full_sft/*.jsonl`) is optimized for training and follows a conversational structure:

```json
{
  "id": "unique_identifier",
  "system": "system_prompt_defining_agent_behavior_and_available_functions",
  "conversations": [
    {
      "from": "human",
      "value": "user_request_or_environment_observation"
    },
    {
      "from": "gpt", 
      "value": "agent_response_with_function_calls_in_xml_format"
    },
    ......
  ]
}
```

**Key Components:**
- **`id`**: Same identifier as in standardized format
- **`system`**: Comprehensive system prompt
- **`conversations`**: Alternating human/gpt turns representing the full interaction

### Real Examples

**Standardized Format Example:**
```json
{
  "id": "alfworld_0",
  "content": [
    {
      "class_": "text_observation",
      "content": "Here is your task. You are in the middle of a room. Looking quickly around you, you see a armchair 2, a armchair 1, a bed 1, a countertop 1, a diningtable 1, a drawer 2, a drawer 1, a dresser 1, a garbagecan 1, a laundryhamper 1, and a sidetable 1.\nYour task is to: find two laptop and put them in bed.",
      "name": null,
      "source": "user"
    },
    {
      "class_": "api_action",
      "function": "go",
      "kwargs": {"location": "\"diningtable 1\""},
      "description": "I need to find two laptops and put them in bed. I'll begin by searching the areas that are most likely to have laptops."
    },
    {
      "class_": "text_observation",
      "content": "On the diningtable 1, you see a alarmclock 2, a bowl 2, a cd 2, a creditcard 2, a creditcard 1, a laptop 1, a pen 1, a pencil 2, and a remotecontrol 1.",
      "name": null,
      "source": "user"
    }
  ],
  "details": {}
}
```

**SFT Format Example:**
```json
{
  "id": "alfworld_0",
  "system": "You are a helpful assistant that can interact with a computer to solve tasks...",
  "conversations": [
    {
      "from": "human",
      "value": "Here is your task. You are in the middle of a room. Looking quickly around you, you see a armchair 2, a armchair 1, a bed 1, a countertop 1, a diningtable 1, a drawer 2, a drawer 1, a dresser 1, a garbagecan 1, a laundryhamper 1, and a sidetable 1.\nYour task is to: find two laptop and put them in bed."
    },
    {
      "from": "gpt",
      "value": "<think>\nI need to find two laptops and put them in bed. I'll begin by searching the areas that are most likely to have laptops.\n</think>\n\n<function=bash>\n<parameter=command>\ngo(\"diningtable 1\")\n</parameter>\n</function>"
    },
    {
      "from": "human", 
      "value": "OBSERVATION:\nOn the diningtable 1, you see a alarmclock 2, a bowl 2, a cd 2, a creditcard 2, a creditcard 1, a laptop 1, a pen 1, a pencil 2, and a remotecontrol 1."
    }
  ]
}
```

## Usage Examples

### Loading Supervised Finetuning (SFT) Files with `data_files`

Use the `data_files` parameter to load individual SFT files efficiently (downloads only the specified file):

```python
from datasets import load_dataset

# Load $agent specific SFT format for $dataset
dataset = load_dataset(
    "neulab/agent-data-collection", 
    data_files="{dataset}/full_sft/full_sft_{agent}.jsonl"
)

# e.g. Load OpenHands SFT format for $dataset
dataset = load_dataset(
    "neulab/agent-data-collection", 
    data_files="{dataset}/full_sft/full_sft_openhands.jsonl"
)

# e.g. Load SWE-Agent SFT format for $dataset
dataset = load_dataset(
    "neulab/agent-data-collection", 
    data_files="{dataset}/full_sft/full_sft_sweagent.jsonl"
)

# e.g. Load AgentLab SFT format for $dataset
dataset = load_dataset(
    "neulab/agent-data-collection", 
    data_files="{dataset}/full_sft/full_sft_agentlab.jsonl"
)
```

#### Loading Multiple SFT Files

You can also load multiple files at once:

```python
# Load all SFT files for $agent
dataset = load_dataset(
    "neulab/agent-data-collection", 
    data_files="*/full_sft/full_sft_{agent}.jsonl"  # Glob pattern
)

```

### Downloading and Loading RAW / STD / SFT Files

```python
import json
from huggingface_hub import hf_hub_download

def download(dataset, local_dir=None):
    """Manually download raw + std + sft files for $dataset."""
    for f in ["full_raw.jsonl", "full_std.jsonl", "full_sft/full_sft_openhands.jsonl", "full_sft/full_sft_sweagent.jsonl", "full_sft/full_sft_agentlab.jsonl"]:
        try: hf_hub_download("neulab/agent-data-collection", filename=f"{dataset}/{f}", repo_type="dataset", local_dir=local_dir)
        except: continue

def load(file_path):
    with open(file_path) as f: 
      return [json.loads(line) for line in f.readlines()]

## Example Usage
download("swe-smith", local_dir=".")
print(load("./swe-smith/full_std.jsonl")[0])
```

## Data Curation

The datasets in this collection were curated through a systematic three-stage pipeline:

1. **Raw Data Extraction**: Original datasets from various sources (research papers, existing repositories, synthetic generation), these are extracted and saved in `{dataset}/full_raw.jsonl`.
2. **Standardization**: Conversion to ADP's unified schema with standardized actions and observations, these are saved in `{dataset}/full_std.jsonl`.
3. **Agent-Specific Formatting**: Transformation into training-ready formats for specific agent frameworks, these are saved in `{dataset}/full_sft/*`.


## Licensing & Attribution

This dataset collection aggregates data from multiple sources. Each subdataset retains its original license.

Please refer to the `LICENSE` file in each dataset directory for specific licensing information.

The sources of the datasets are documented in `README.md` under each dataset's directory.


## Contact and Support

For questions, issues, or contributions:

- **GitHub Issues**: [agent-data-protocol/issues](https://github.com/neulab/agent-data-protocol/issues)
- **GitHub Discussions**: [agent-data-protocol/discussions](https://github.com/neulab/agent-data-protocol/discussions)
- **Paper Authors**: Contact information available in the paper

### Contributing

We welcome contributions to expand this collection! If you have high-quality agent interaction data that follows our format, please:

1. Ensure data quality and privacy compliance
2. Follow the standardized format
3. Include proper documentation and licensing
4. Submit a pull request with your dataset

## Citation

If you use this dataset collection in your research, please cite:

```bibtex
@article{song2025agent,
  title={Agent Data Protocol: Unifying Datasets for Diverse, Effective Fine-tuning of LLM Agents},
  author={Song, Yueqi and Ramaneti, Ketan and Sheikh, Zaid and Chen, Ziru and Gou, Boyu and Xie, Tianbao and Xu, Yiheng and Zhang, Danyang and Gandhi, Apurva and Yang, Fan and others},
  journal={arXiv preprint arXiv:2510.24702},
  year={2025}
}
```
---