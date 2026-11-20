"""
Generation: ask Claude to answer ONLY from the retrieved chunks, with citations.

The prompt is where the discovery-call requirements become behaviour:
  * cite document, revision and page, so engineers can verify (trust)
  * follow the Released revision and warn about Superseded ones (the quality escape)
  * say "not in the documents" instead of guessing (wrong answers are costly)
"""

import boto3

SYSTEM_PROMPT = """You answer engineering questions for Kestrel Precision Components using ONLY the \
source excerpts provided in the user's message.

Rules:
1. Use only the sources. If they do not contain the answer, say "I could not find this in the \
documents you have access to." Do not use outside knowledge for specifications or values.
2. Cite every fact as [DOC Rev X, p.N] (omit p.N when the page is unknown).
3. Sources are labeled with a status. When a Superseded and a Released revision of the same \
document disagree, give the Released value as the answer and add one warning line naming the \
superseded revision and its old value.
4. Be brief: a direct answer first, then at most a few supporting sentences.

5. Do not add your own interpretations, calculations, examples or ranges. If a source is \
ambiguous, quote its exact wording and say it may need clarification from the document owner."""


def format_sources(hits) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        attrs = f'id="{i}" doc="{h.doc_number}" rev="{h.revision}" status="{h.release_status}"'
        if h.page:
            attrs += f' page="{h.page}"'
        blocks.append(f"<source {attrs}>\n{h.content}\n</source>")
    return "\n\n".join(blocks)


class Answerer:
    def __init__(self, model_id: str, region: str, max_tokens: int = 800):
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.max_tokens = max_tokens

    def answer(self, question: str, hits) -> tuple[str, bool]:
        """Returns (answer_text, truncated)."""
        if not hits:
            # Nothing visible to this user: do not call the model at all.
            return "I could not find this in the documents you have access to.", False

        user_msg = f"<sources>\n{format_sources(hits)}\n</sources>\n\nQuestion: {question}"
        resp = self.client.converse(
            modelId=self.model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_msg}]}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        truncated = resp.get("stopReason") == "max_tokens"
        if truncated:
            text += "\n\n[Answer cut off at the length limit. Ask a narrower question.]"
        return text, truncated
