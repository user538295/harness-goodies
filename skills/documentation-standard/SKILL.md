---
name: documentation-standard
description: Comprehensive documentation management for software projects. Use when creating, updating, organizing, or refactoring project documentation, including architecture docs, ADRs, user manuals, and development guides. Covers structure, naming conventions, content standards, and maintenance workflows.
---

# Documentation Standard

## Purpose

This skill provides standards and workflows for creating, organizing, and maintaining world-class project documentation that serves medior-level developers without over- or under-documenting.

## Core Principles

### 1. Principles Over Details
Start with key rules before diving into examples. For instance, in error handling docs: state "Fail fast, propagate context, centralize recovery" before showing code.

### 2. Avoid Duplication
Information lives in exactly one place. Cross-link related sections rather than repeating content.

### 3. Right-Sized Documentation
Balance between helpful and overwhelming. Documentation should answer "why" and "how" without excessive detail that ages poorly.

### 4. Cross-Linking Over Silos
Link related documents contextually (e.g., from services_architecture.md to integration_architecture.md and error_handling_strategy.md).

### 5. Mermaid for All Diagrams
Use Mermaid format for diagrams unless the type is unsupported and another format would be significantly clearer.

## Directory Structure

All documentation lives in `/Documentation/` except four root-level files:
- `readme.md` - Project overview, must have
- `contributing.md` - Contribution guidelines, must have
- `CLAUDE.md` - AI assistant instructions - must have, if missing init the project
- `constitution.md` - Project values/principles - optional

### Standard Subdirectories

```
/Documentation/
├── Architecture/          # System design and technical specs
├── ADRs/                 # Architecture Decision Records
├── Backlog/              # Planned features and user stories
├── Completed/            # Implementation documentation
├── UserManual/           # End-user guides
├── roadmap.md            # Product roadmap
└── quick_start.md        # Developer onboarding
```

## Architecture Documentation Structure

Files in `/Documentation/Architecture/` follow numeric prefixes for ordering:

### Foundation (000-099)
- `000_introduction_and_guiding_principles.md` - Vision, philosophy, goals, non-goals
- `010_engineering_principles_and_constraints.md` - Technical constraints and standards

### System Design (100-199)
- `100_system_architecture_overview.md` - C4 diagrams (Context + Container + Component), architecture patterns
- `110_component_catalog_and_layer_breakdown.md` - Component inventory and layering
- `120_services_and_integration_architecture.md` - Sync (API) and async (message/event) integrations
- `130_data_architecture_and_persistence.md` - ERDs, data flow, retention, backup strategy
- `140_error_handling_strategy.md` - Error handling patterns and practices
- `150_security_and_privacy_architecture.md` - Security controls and privacy measures
- `160_operational_readiness_monitoring_and_reliability.md` - Observability, metrics, alerting, runbooks, SRE pillars (SLOs, SLIs, SLAs)

### Quality & Testing (200-299)
- `200_testing_strategy.md` - Test pyramid (unit → integration → system → exploratory), automation
- `210_performance_and_scalability.md` - Performance targets, load testing, profiling
- `220_accessibility_and_internationalization.md` - A11y and i18n standards

### Frontend Architecture (300-399)
- `300_ui_ux_architecture.md` - Design tokens, theming, design system alignment
- `310_navigation_architecture.md` - Navigation patterns and routing
- `320_state_management.md` - State ownership boundaries (local vs global vs derived)

### Development & Operations (500-599)
- `500_development_workflows_and_conventions.md` - Coding standards and workflows
- `510_release_and_environment_strategy.md` - CI/CD, environment config, versioning
- `520_api_design_and_contracts.md` - API design principles and governance
- `530_technical_debt_refactoring_roadmap.md` - Debt register, prioritization matrix, planned refactoring

### Reference (600-699)
- `600_api_reference_or_public_interface.md` - API inventory (endpoints, methods, fields)

### Meta (900-999)
- `990_documentation_index_and_contribution_guide.md` - Navigation and contribution instructions

## Naming Conventions

### Files
- **Architecture docs**: `NNN_snake_case_name.md` (e.g., `100_system_architecture_overview.md`)
- **ADRs**: `NN_descriptive_name.md` (e.g., `01_use_postgresql_for_persistence.md`)
- **Backlog/Completed**: `NN_descriptive_name.md`
- **All other files**: `snake_case.md`

**Validation**: If files don't follow these conventions, warn the user and suggest renames.

## Document Metadata

Every document must include a 4-line header:

```markdown
**Purpose**: [One sentence describing what this document covers]  
**Audience**: [Who should read this - e.g., Backend engineers, All developers]  
**Status**: [Draft | Stable | Deprecated]  
**Last reviewed**: YYYY-MM-DD  
**Next review**: YYYY-MM-DD
```

**Review cycle**: Architecture docs reviewed quarterly, technical specs bi-annually, process docs annually.

## Content Standards

### Structure
1. **Start with principles** - State 3-5 key rules before examples
2. **Use clear headings** - Short, descriptive, sentence-case
3. **Progressive detail** - Overview → specifics → edge cases
4. **Examples after theory** - Code examples follow explanations
5. **Cross-references** - Link to related docs contextually

### Writing Style
- **Target audience**: Medior developers (2-5 years experience)
- **Tone**: Professional but approachable
- **Sentence length**: Vary between 10-25 words
- **Active voice**: "The system validates input" not "Input is validated"
- **Present tense**: "The API returns JSON" not "The API will return JSON"

### Diagrams
Use Mermaid for:
- Flowcharts (`graph`, `flowchart`)
- Sequence diagrams (`sequenceDiagram`)
- Class diagrams (`classDiagram`)
- Entity relationships (`erDiagram`)
- State machines (`stateDiagram`)
- C4 diagrams (using `graph` with appropriate styling)

Exception: Use alternative only if Mermaid doesn't support the diagram type AND the alternative is significantly clearer.

### Code Examples
- Use syntax highlighting (```language)
- Keep examples under 30 lines
- Include comments explaining non-obvious logic
- Show both correct and incorrect patterns when clarifying

## Maintenance Workflows

### Creating New Documents
1. Determine correct directory and naming convention
2. Add metadata header (purpose, audience, status, review dates)
3. Start with principles/overview section
4. Add detailed sections with cross-references
5. Include diagrams in Mermaid format
6. Validate Markdown linting (use markdownlint)
7. Update `990_documentation_index_and_contribution_guide.md`

### Updating Existing Documents
1. Check last reviewed date
2. Update content sections
3. Refresh cross-references
4. Update "Last reviewed" date
5. Set "Next review" date based on content type
6. Check for broken links

### Refactoring Documentation
1. **Audit structure**: Compare actual files against standard structure
2. **Rename files**: Apply naming conventions consistently
3. **Add missing metadata**: Ensure all docs have 4-line header
4. **Fix cross-references**: Update links after renames
5. **Validate consistency**: Check for duplicated content
6. **Run Markdown linting**: Fix formatting issues
7. **Update index**: Reflect changes in meta-documentation

### Consistency Checks
Run these checks periodically:
- [ ] All files follow naming conventions
- [ ] All docs have metadata headers
- [ ] Cross-references are valid (no broken links)
- [ ] No content duplication
- [ ] Diagrams use Mermaid format
- [ ] Review dates are current
- [ ] Index document is up-to-date

### Automated Validation

The bundled `validate_docs.py` checks a documentation tree against these conventions: required directory structure (Architecture, ADRs, Backlog, Completed, UserManual), the required metadata headers per doc (Purpose/Audience/Status/Last reviewed/Next review), file-naming, and stale review dates. Run it in addition to the manual checks above — it does not replace `markdownlint`.

First locate the skill directory (`$BASE`). Use the path the harness reported when this skill loaded — `Base directory for this skill: <path>` (Claude Code/OpenCode), `[Skill directory: <path>]` (omp), or, in Cursor and Codex, the directory of this SKILL.md — verbatim. Only if none was reported, run this one-liner and capture the printed path as `$BASE`:

```bash
BASE=""; for d in "${DOCUMENTATION_STANDARD_HOME:-}" .agents/skills/documentation-standard .claude/skills/documentation-standard .cursor/skills/documentation-standard .opencode/skills/documentation-standard .codex/skills/documentation-standard ~/.agents/skills/documentation-standard ~/.claude/skills/documentation-standard ~/.cursor/skills/documentation-standard ~/.config/opencode/skills/documentation-standard ~/.omp/agent/skills/documentation-standard ~/.codex/skills/documentation-standard "$(ls -d ~/.claude/plugins/cache/*/claude-goodies/*/skills/documentation-standard 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.omp/plugins/cache/plugins/*___omp-goodies___*/skills/documentation-standard 2>/dev/null | sort -V | tail -1)" "$(ls -d ~/.codex/plugins/cache/*/codex-goodies/*/skills/documentation-standard 2>/dev/null | sort -V | tail -1)" "$(ls -dt ~/.cursor/plugins/cache/*/cursor-goodies/*/skills/documentation-standard 2>/dev/null | head -1)" "$(ls -dt ~/.cache/opencode/npm/*/*/node_modules/opencode-goodies/skills/documentation-standard 2>/dev/null | head -1)"; do [ -n "$d" ] && [ -f "$d/scripts/validate_docs.py" ] && { BASE="$d"; break; }; done; [ -n "$BASE" ] && echo "$BASE" || { echo "ERROR: documentation-standard not found in any known skills root — set DOCUMENTATION_STANDARD_HOME=<skill dir>" >&2; false; }
```

Then run the validator against the project's documentation directory:

```bash
python3 "$BASE/scripts/validate_docs.py" --path <docs dir> [--strict]
```

- `--path` — the documentation directory to validate.
- `--strict` — treat warnings as errors.
- Exit code `0` = pass, `1` = failure (with `--strict`, any warning also fails).

## Architecture Decision Records (ADRs)

ADRs document significant architectural decisions. Structure:

```markdown
# [Number]. [Decision Title]

**Status**: [Proposed | Accepted | Deprecated | Superseded]  
**Date**: YYYY-MM-DD  
**Deciders**: [Names or roles]

## Context
[What problem are we solving? What constraints exist?]

## Decision
[What did we decide? Be specific.]

## Consequences
### Positive
- [Benefit 1]
- [Benefit 2]

### Negative
- [Tradeoff 1]
- [Tradeoff 2]

## Alternatives Considered
- **Option 1**: [Brief description] - Rejected because [reason]
- **Option 2**: [Brief description] - Rejected because [reason]
```

## User Stories & Backlog Items

Structure for backlog items:

```markdown
# [Number]. [Feature Name]

**Status**: [Planned | In Progress | Blocked]  
**Priority**: [Critical | High | Medium | Low]  
**Estimated effort**: [T-shirt size: XS, S, M, L, XL]

## User Story
As a [user type],
I want [goal],
So that [benefit].

## Acceptance Criteria
- [ ] [Criterion 1]
- [ ] [Criterion 2]

## Technical Notes
[Implementation considerations, dependencies, risks]

## Related Documents
- [Link to architecture doc]
- [Link to ADR]
```

## Quality Checklist

Before marking documentation complete, verify:

- [ ] Follows directory structure and naming conventions
- [ ] Contains required metadata header
- [ ] Starts with principles before details
- [ ] Uses Mermaid for diagrams (unless exception applies)
- [ ] Cross-references related documents
- [ ] No duplicated content
- [ ] Written for medior developer audience
- [ ] Passes Markdown linting
- [ ] Review dates are set appropriately
- [ ] Listed in documentation index

## Common Pitfalls to Avoid

1. **Over-documentation**: Don't document obvious code or standard patterns
2. **Under-documentation**: Do explain "why" behind non-obvious decisions
3. **Stale docs**: Set realistic review cycles and honor them
4. **Broken links**: Update cross-references when moving/renaming files
5. **Inconsistent formatting**: Use linting to maintain consistency
6. **Missing context**: Always explain the problem before the solution
7. **Screenshot diagrams**: Use text-based Mermaid instead for maintainability

## Integration with Development Workflow

- **Pre-commit**: Validate Markdown linting
- **PR reviews**: Check documentation updates for code changes
- **Sprint planning**: Schedule documentation reviews
- **Definition of Done**: Include documentation updates
- **Onboarding**: Use quick_start.md as entry point

## Example: Creating a New Architecture Document

```bash
# 1. Create file with correct naming
touch Documentation/Architecture/170_caching_strategy.md

# 2. Add metadata header
cat >> Documentation/Architecture/170_caching_strategy.md << 'EOF'
**Purpose**: Defines caching layers and invalidation strategies  
**Audience**: Backend and infrastructure engineers  
**Status**: Draft  
**Last reviewed**: 2025-10-31  
**Next review**: 2026-01-31

# Caching Strategy

## Principles
1. Cache close to the data source
2. Invalidate explicitly, not by TTL
3. Monitor cache hit rates

## Overview
[Content follows...]
EOF

# 3. Update documentation index
# Add link to 990_documentation_index_and_contribution_guide.md
```

## When to Use This Skill

Trigger this skill when:
- Creating new documentation from scratch
- Refactoring existing documentation structure
- Reviewing documentation for consistency
- Onboarding new projects (setting up doc structure)
- Auditing documentation quality
- Fixing naming convention violations
- Updating cross-references after restructuring
