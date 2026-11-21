# Learning Lab

A Codex skill that serves as a learning hub: understand real material, explore a mechanism, practise applying it, and return to explanations when review exposes a gap. It works across subjects and uses visuals or small experiments when they help.

The hub connects two independently usable skills:

| Skill | Purpose | Repository |
| --- | --- | --- |
| `learning-lab` | Explanation, visualisation, experiments and practice | [Learning Lab](https://github.com/ZheYi101/Learning-Lab.skill) |
| `ai-learning-review` | Searchable knowledge notes and selected concept or method cards | [AI Learning Review](https://github.com/ZheYi101/AI-Learning-Review.skill) |
| `anki-context-card-maker` | Vocabulary and expression cards preserving real sentences and available media | [Anki Context Card Maker](https://github.com/ZheYi101/Anki-Context-Card-Maker.skill) |

## Install all three on macOS

Paste this into Codex on your Mac:

```text
Use $skill-installer to install these three repo-root skills from the main branch into ~/.agents/skills:
- ZheYi101/Learning-Lab.skill: --path . --name learning-lab
- ZheYi101/AI-Learning-Review.skill: --path . --name ai-learning-review
- ZheYi101/Anki-Context-Card-Maker.skill: --path . --name anki-context-card-maker
Each repository has SKILL.md at its root. Preserve any existing local settings and dictionaries if updating an existing installation.
```

Alternatively, for a fresh installation in macOS Terminal:

```bash
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/ZheYi101/Learning-Lab.skill.git "$HOME/.agents/skills/learning-lab"
git clone https://github.com/ZheYi101/AI-Learning-Review.skill.git "$HOME/.agents/skills/ai-learning-review"
git clone https://github.com/ZheYi101/Anki-Context-Card-Maker.skill.git "$HOME/.agents/skills/anki-context-card-maker"
```

Codex discovers installed skills automatically. Use them on your next turn; restart Codex if they do not appear. For Git-cloned installations, update each with `git -C <skill-folder> pull --ff-only`. Installer-created copies should be updated through Codex while preserving local files.

On Windows, use the same installer prompt with your personal skills directory, or clone into `$env:USERPROFILE\.agents\skills\<skill-name>` in PowerShell.

## Requirements and platform support

Explanations and practice work with the core skill on its own. The optional local knowledge base uses Python 3.10+ and the standard library.

Both companion exporters use Python 3.10+ and the standard library. On macOS, invoke them with `python3`. Anki import requires desktop Anki and AnkiConnect on that machine. Dictionaries, ffmpeg-compatible media tools, downloads and local transcription are optional capabilities configured on the Mac; they are not bundled or copied from Windows. The card maker's built-in automatic TTS uses Windows System.Speech; on macOS supply pronunciation audio through `word_audio` or leave that optional capability unavailable. See each companion README for details.

## Optional local knowledge base

To reuse prior learning, create a directory outside the skill checkout with two subdirectories named `经历` (experience) and `能力` (ability). The skill writes preferences to `学习偏好.md` in the knowledge base.

Create `knowledge-base.local.json` beside the installed `SKILL.md`. Set `root` to the absolute path of your knowledge base. On Windows, you can use forward slashes, as in `C:/Users/your-name/Documents/learning-lab-knowledge`.

```json
{"root": "/Users/your-name/Documents/learning-lab-knowledge"}
```

From the installed skill folder, run `python3 -X utf8 scripts/knowledge.py stats` to verify the setup. Use `python` on Windows. Git ignores the local settings file. For the record fields and update rules, see the [writeback guide](references/knowledge-writeback.md).

## Use

```text
Use $learning-lab to help me understand this paper. Start with the mechanism I am missing.
```

```text
用 $learning-lab 帮我理解这份材料；需要时用例子、可视化或练习。
```

Ask to organise, remember or make cards when you want those outputs. The hub reuses your source and context, selects the appropriate companion, and preserves its preview and import rules.

The installed skill names remain `learning-lab`, `ai-learning-review` and `anki-context-card-maker`; `.skill` is part of the GitHub repository names, not the installed folder names.
