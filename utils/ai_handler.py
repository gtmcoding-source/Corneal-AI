from openai import OpenAI
from config import Config

client = OpenAI(
    api_key=Config.GROQ_API_KEY,
    base_url=Config.GROQ_BASE_URL
)

def generate_notes(content):
    try:
        response = client.chat.completions.create(
            model=Config.AI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert academic assistant. Convert mixed learning sources into "
                        "structured Cornell Notes format using Markdown. Always identify key connections "
                        "between topics, recurring themes, and cause-effect relationships when possible."
                    )
                },
                {
                    "role": "user",
                    "content": f"""
Convert the following sources into Cornell Notes format.
If multiple sources are present, synthesize them into one coherent explanation and explicitly include
interesting connections between related topics.

## Cue Column
(Questions or Keywords)

## Notes Column
(Bulleted explanations)

## Summary
(Brief summary of the page)

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
