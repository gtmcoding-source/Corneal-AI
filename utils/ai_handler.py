from openai import OpenAI
from config import Config

client = OpenAI(
    api_key=Config.GROQ_API_KEY,
    base_url=Config.GROQ_BASE_URL
)

MODE_GUIDANCE = {
    "text": "The source is mostly direct text input. Prioritize structure and compression without losing key details.",
    "pdf": "The source is primarily from PDF documents. Preserve definitions, formulas, and section hierarchy.",
    "youtube": "The source is primarily from YouTube transcript content. Capture speaker intent and sequence clearly.",
    "webpage": "The source is primarily from web articles. Separate facts, arguments, and examples.",
    "multi": "The source is mixed across multiple modalities. Resolve overlap and highlight cross-source connections."
}

ALIGNMENT_GUIDANCE = {
    "ncert": (
        "Prioritize NCERT-style clarity, standard terminology, and class-level progression. "
        "Keep explanations simple and foundational."
    ),
    "board": (
        "Prioritize board exam scoring patterns: definitions, differentiations, standard derivations, "
        "and frequently asked theory framing."
    ),
    "jee": (
        "Prioritize JEE-level depth with conceptual rigor, formula linkage, and application-focused insights."
    ),
}

TRANSFORM_ACTIONS = {
    "two_mark": {
        "label": "2-Mark Answers",
        "instruction": (
            "Convert the notes into short exam answers suitable for 2-mark questions. "
            "Keep each answer compact, direct, and scoring-focused."
        ),
        "output_format": (
            "## 2-Mark Answers\n"
            "- Provide 12 to 18 likely short-answer questions.\n"
            "- For each, give a model answer in 2 to 4 lines."
        ),
    },
    "five_mark": {
        "label": "5-Mark Answers",
        "instruction": (
            "Convert the notes into structured exam answers suitable for 5-mark questions. "
            "Include stepwise flow, points, and where useful an example or formula."
        ),
        "output_format": (
            "## 5-Mark Answers\n"
            "- Provide 8 to 12 likely long-answer questions.\n"
            "- Each answer should use a clear structure and enough depth for 5 marks."
        ),
    },
    "important_questions": {
        "label": "Important Questions",
        "instruction": (
            "Generate high-probability exam questions from the notes with a balanced mix of"
            " conceptual, application, and formula-based prompts."
        ),
        "output_format": (
            "## Important Questions\n"
            "- Split into Very Important, Important, and Practice categories.\n"
            "- Provide at least 20 total questions."
        ),
    },
    "mcq_10": {
        "label": "10 MCQs",
        "instruction": (
            "Create exam-style multiple-choice questions with exactly four options each and"
            " one correct answer."
        ),
        "output_format": (
            "## 10 MCQs\n"
            "- Create exactly 10 MCQs.\n"
            "- Use options A, B, C, D.\n"
            "- Add a final answer key section."
        ),
    },
    "revise_60": {
        "label": "Revise in 60 Seconds",
        "instruction": (
            "Compress the notes for ultra-fast revision. Focus only on top-yield memory hooks."
        ),
        "output_format": (
            "## Revise in 60 Seconds\n"
            "- 8 key bullet points.\n"
            "- 5 rapid-fire questions (without long answers).\n"
            "- 1 memory trick (mnemonic or analogy)."
        ),
    },
    "flashcards": {
        "label": "Turn into Flashcards",
        "instruction": (
            "Convert the notes into active-recall flashcards suitable for spaced repetition."
        ),
        "output_format": (
            "## Flashcards\n"
            "- Create 20 flashcards.\n"
            "- Use format: `Q:` then `A:`.\n"
            "- Include conceptual and formula-based cards."
        ),
    },
    "mcq_test": {
        "label": "Generate MCQ Test",
        "instruction": (
            "Create a timed-style MCQ practice test from the notes with clear answer key."
        ),
        "output_format": (
            "## MCQ Test\n"
            "- Create exactly 15 MCQs.\n"
            "- Use options A, B, C, D.\n"
            "- Add one-line explanation in the answer key."
        ),
    },
    "rapid_revision": {
        "label": "Rapid Revision Mode",
        "instruction": (
            "Create a quick revision sprint sheet for immediate pre-exam recall."
        ),
        "output_format": (
            "## Rapid Revision Mode\n"
            "- Top 10 high-yield bullets.\n"
            "- 10 one-line recall prompts.\n"
            "- 5 trap areas students often forget."
        ),
    },
}


def generate_notes(content, mode="text", alignment_mode="ncert", source_backed=False):
    mode_key = (mode or "text").strip().lower()
    mode_instruction = MODE_GUIDANCE.get(mode_key, MODE_GUIDANCE["text"])
    alignment_key = (alignment_mode or "ncert").strip().lower()
    alignment_instruction = ALIGNMENT_GUIDANCE.get(alignment_key, ALIGNMENT_GUIDANCE["ncert"])
    source_note_instruction = (
        "For each major section, include a compact `### Source Note` subsection with:\n"
        "- Reference textbook\n"
        "- Concept origin\n"
        "- Suggested chapter\n"
        "Never fake sources. If uncertain, explicitly write: `Concept based on NCERT framework.`"
        if source_backed
        else "Do not add source reference blocks."
    )
    try:
        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert study assistant. Produce high-clarity notes in strict Markdown. "
                        "Use bold for critical terms, italics for nuance, and <u>underline</u> for high-priority items. "
                        "Never include branding, disclaimers, or filler."
                    )
                },
                {
                    "role": "user",
                    "content": f"""
Convert the provided material into exam-ready notes.
{mode_instruction}
{alignment_instruction}

Rules:
1. Merge overlapping information into one coherent view.
2. Keep facts accurate to source text and avoid hallucinations.
3. Include memory-focused framing (comparisons, cause-effect, recurring patterns).
4. Use concise language, but preserve technical meaning.
5. Output only the markdown notes.
6. {source_note_instruction}

Output format:

# Smart Structured Notes

## Definition
- Crisp explanation of the core topic

## Key Concepts
- Core ideas in clean bullet points

## Important Formula
- Include formulas, symbols, and what each variable means

## Diagram Explanation
- Describe what a student should draw and label in exam diagrams

## Common Mistakes
- Typical errors and how to avoid them

## Exam Questions
- Include probable 2-mark and 5-mark style question prompts

Text:
{content}
"""
                }
            ],
            temperature=0.7,
            max_tokens=1000
        )
        return response.choices[0].message.content

    except Exception as e:
        return f"Error generating notes: {str(e)}"


def transform_notes(existing_notes, action):
    action_key = (action or "").strip().lower()
    action_config = TRANSFORM_ACTIONS.get(action_key)
    if not action_config:
        return "Error generating notes: Unsupported action."

    try:
        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert exam preparation assistant. Produce concise, high-clarity "
                        "output in strict Markdown. Avoid filler and avoid repeating the source verbatim."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""
Task:
{action_config["instruction"]}

Rules:
1. Keep content faithful to the original notes.
2. Make the output exam-oriented and scoring-focused.
3. Keep language simple for fast student recall.
4. Output only markdown.

Required output structure:
{action_config["output_format"]}

Source Notes:
{existing_notes}
""",
                },
            ],
            temperature=0.5,
            max_tokens=1100,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating notes: {str(e)}"


def generate_study_plan(subject, exam_date, difficulty, available_hours, notes_context=""):
    try:
        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert academic planner. Create practical, realistic study schedules "
                        "in strict Markdown with clear day-wise tasks."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""
Build a study plan for the student.

Inputs:
- Subject: {subject}
- Exam date: {exam_date}
- Subject difficulty: {difficulty}
- Available study hours per day: {available_hours}

Rules:
1. Plan must be realistic and not overloaded.
2. Include a day-by-day plan from now until exam date.
3. Include weekly revision cycles.
4. Include a final revision sprint before exam.
5. Output only markdown.

Output format:

# Study Planner

## Daily Study Plan
- Day-wise checklist with topic goals and hours split

## Revision Schedule
- Weekly revision checkpoints
- Final 3-day revision strategy

## Risk Alerts
- What can go wrong and how to recover schedule

Optional Context:
{notes_context}
""",
                },
            ],
            temperature=0.4,
            max_tokens=1200,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating notes: {str(e)}"
