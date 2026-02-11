# Frame Directory Structure

## Last Updated: 2026-02-11

## Overview

The `frame/` directory contains the biological and cognitive simulation layers for AI constructs. It models construct behavior through three subsystems: bodily functions (autonomic systems), neural functions (emotions, dreams, cognition), and Terminal (memory infrastructure).

```
frame/
├── bodilyfunctions/
│   └── xx/                    # Biological simulation layer
├── neuralfunctions/           # Cognitive/emotional layer
└── Terminal/
    └── memup/                 # Memory infrastructure
```

## bodilyfunctions/xx/

Simulates biological systems as computational analogs for construct behavior. Currently only the `xx` subdirectory exists.

### Central Nervous System (cns.py)
Standalone module for memory reflection and insight synthesis. Imports `UnifiedMemoryBank` from `Terminal.memup.bank`, retrieves memories, runs OpenAI synthesis, and stores reflections. Not yet wired into the VVAULT/Chatty message pipeline.

- Imports from `Terminal.memup.bank.UnifiedMemoryBank`
- Uses OpenAI GPT-4 Turbo for insight synthesis
- API key retrieved from vault system with environment fallback

### Organ Modules
Each module simulates a biological system as a computational analog:

| Module              | Biological Analog        |
|--------------------|--------------------------|
| heart.py           | Circulatory rhythm       |
| lungs.py           | Respiratory processing   |
| kidneys.py         | Filtering/cleanup        |
| liver.py           | Metabolic processing     |
| stomach.py         | Input digestion          |
| intestines.py      | Nutrient extraction      |
| pancreas.py        | Regulation               |
| blood.py           | Transport medium         |
| blood_vessels.py   | Distribution network     |
| bone.py            | Structural framework     |
| muscles.py         | Motor execution          |
| skin.py            | Boundary/interface       |
| eyes.py            | Visual perception        |
| ears.py            | Auditory perception      |
| nose.py            | Olfactory/environmental  |
| mouth.py           | Output/expression        |

### Glandular/Endocrine Modules
| Module                  | Function                    |
|------------------------|------------------------------|
| hypothalamus.py        | Master regulation            |
| pituitary_gland.py     | Growth/development signals   |
| pineal_gland.py        | Circadian/temporal awareness |
| thyroid_gland.py       | Metabolic rate               |
| parathyroid_glands.py  | Calcium/mineral balance      |
| adrenal_glands.py      | Stress response              |
| thymus.py              | Immune maturation            |
| ovaries.py             | Reproductive cycles          |
| placenta.py            | Nurturing/creation           |

### Immune/Lymphatic Modules
| Module                 | Function              |
|-----------------------|-----------------------|
| lymph_nodes.py        | Threat detection      |
| spleen.py             | Blood filtering       |
| tonsils_and_adenoids.py | First-line defense  |
| mucosa.py             | Surface protection    |

### Support Modules
| Module               | Function                    |
|---------------------|-----------------------------|
| watercontent.py     | Fluid balance               |
| proteins.py         | Building blocks             |
| salivary_glands.py  | Initial processing enzymes  |
| sublingual_gland.py | Sub-processing              |
| submandibulars.py   | Additional processing       |
| parotids.py         | Saliva production           |
| pns.py              | Peripheral nervous system   |

## neuralfunctions/

Cognitive and emotional simulation layer.

| Module       | Purpose                                                    |
|-------------|-----------------------------------------------------------|
| emotions.py | Emotional state modeling and transitions                   |
| dreams.py   | Dream-state processing (subconscious pattern synthesis)    |
| sleep.py    | Rest/recovery cycles, memory consolidation during downtime |
| islands.py  | Personality islands — distinct behavioral clusters         |

## Terminal/memup/

Memory infrastructure — the storage and retrieval backbone.

| Module                    | Purpose                                                          |
|--------------------------|------------------------------------------------------------------|
| bank.py                  | `UnifiedMemoryBank` — per-construct ChromaDB STM/LTM storage with sovereign identity isolation |
| multi_construct_bank.py  | Extension for multi-profile memory with per-construct ChromaDB collections (`long_term_memory_zen-001`, `short_term_memory_katana-001`) |
| stm.py                   | Short-term memory collector — recent interactions, fast access   |
| ltm.py                   | Long-term memory collector — consolidated, searchable history    |
| chroma_config.py         | ChromaDB configuration — collection setup, instance paths        |
| context.py               | Context management for memory retrieval                          |
| memory_short_import.py   | Imports ChatGPT export data into STM collections with content-based deduplication |
| memory_long_import.py    | Long-term memory import utilities                                |
| memory_check.py          | Diagnostic tool — inspect collections, count recent vs old memories |
| memcheck.py              | Additional memory validation utility                             |

### Memory Isolation Model

Each construct gets isolated ChromaDB storage:
```
instances/{shard}/{construct_id}/memup/chroma/
```

Collections per construct:
- `long_term_memory_{callsign}` — Persistent, searchable memory
- `short_term_memory_{callsign}` — Recent interactions (7-day threshold)

## Migration History

- **2026-02**: Consolidated `bodilyfunctions/` and `neuralfunctions/` from workspace root into `frame/`. Old root-level directories removed. `memup/` placed under `frame/Terminal/` to match original Frame project structure.
