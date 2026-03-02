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


def generate_notes(content, mode="text"):
    mode_key = (mode or "text").strip().lower()
    mode_instruction = MODE_GUIDANCE.get(mode_key, MODE_GUIDANCE["text"])
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

Rules:
1. Merge overlapping information into one coherent view.
2. Keep facts accurate to source text and avoid hallucinations.
3. Include memory-focused framing (comparisons, cause-effect, recurring patterns).
4. Use concise language, but preserve technical meaning.
5. Output only the markdown notes.

Output format:

# Cornell Notes

## Cue Column
- Question or keyword prompts for recall

## Notes Column
- Detailed bullet explanations
- Use **bold**, *italics*, and <u>underlined priority points</u> where useful

## Key Connections
- Explicit links between concepts/sources

## Quick Revision Checklist
- 5 to 8 high-value checkpoints

## Summary
- 4 to 6 sentence summary optimized for revision

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
