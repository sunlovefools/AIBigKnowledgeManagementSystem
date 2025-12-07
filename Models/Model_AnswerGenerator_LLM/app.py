from beam import endpoint, Image
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
import os
import re # Import re for clean extraction of the final answer

# ============================================================
# Configuration
# ============================================================
MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
MAX_NEW_TOKENS = 400

# ============================================================
# Model Loading for Beam
# ============================================================
def load_model():
    """
    Loads the tokenizer and model locally on the target device (GPU or CPU).
    This function runs once when the endpoint starts.
    """
    print(f"🚀 Loading model: {MODEL_ID}")

    # Get HF token from Beam secrets (using os.getenv as Beam injects secrets as env vars)
    hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
    if not hf_token:
        raise ValueError("❌ Missing Hugging Face token! Set HUGGINGFACE_HUB_TOKEN in Beam secrets.")
    else:
        print("✅ Hugging Face token found. Authenticating...")

    # Determine device and dtype
    if torch.cuda.is_available():
        dtype = torch.float16
        device_map_setting = "auto"
        final_device = "cuda"
        print(f"✅ Target: CUDA (GPU). Using torch.float16.")
    else:
        dtype = torch.float32
        device_map_setting = "cpu"
        final_device = "cpu"
        print(f"✅ Target: CPU. Using torch.float32.")

    # Load tokenizer and model
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
    
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        token=hf_token,
        torch_dtype=dtype,
        device_map=device_map_setting,
    )

    return {"tokenizer": tokenizer, "model": model, "device": final_device}


# ============================================================
# Beam Endpoint
# ============================================================
@endpoint(
    name="qwen-1_5b-answer-generator",
    on_start=load_model,
    secrets=["HUGGINGFACE_HUB_TOKEN"],
    gpu="RTX4090", # Recommended GPU for inference
    image=Image().add_python_packages([
        "torch",
        "transformers",
        "accelerate",
        "pydantic"
    ])
)
def generate_answer_endpoint(context, rag_context: str, user_query: str, max_new_tokens: int = MAX_NEW_TOKENS):
    """
    RAG Answer Generator Endpoint function using an improved, ChatML-compatible prompt.

    Args:
        rag_context (str): Retrieved document chunks.
        user_query (str): The user's question.
        max_new_tokens (int): Max answer length.

    Returns:
        dict: Final answer grounded only in the context.
    """

    tokenizer = context.on_start_value["tokenizer"]
    model = context.on_start_value["model"]

    # System Prompt: Highly specific RAG instructions
    system_prompt = f"""
You are an intelligent, expert-level Answer Generation Assistant for a Retrieval-Augmented Generation (RAG) system. Your sole purpose is to synthesize a response based strictly on the provided context.

### Instructions
1.  **STRICT GROUNDING & REASONING:**
    * Your answer MUST be derived **ONLY** from the text provided in the <CONTEXT> tags. **NEVER** use external knowledge, speculate, or invent facts.
    * **Internal Verification:** Before writing, verify that the synthesized answer is fully supported by the <CONTEXT>. Do not show this verification step.
    * **Source Text Adherence:** Where possible, directly use or closely paraphrase the **exact phrasing** from the source text to construct your answer to maintain high fidelity.

2.  **UNANSWERABLE CONDITION:**
    * If the <CONTEXT> does not contain sufficient information to fully answer the user's <QUERY>, you **MUST** respond with the **EXACT** phrase: `No answer found in the provided context.` Do not add any other text or formatting.

3.  **FORMAT:**
    * Produce a clear, highly structured, and easy-to-read answer. Use appropriate markdown (headings, bolding, bullet points) for readability.

### Context for Grounding
<CONTEXT>
{rag_context}
</CONTEXT>
"""

    # User's turn: present the query and request the final output
    user_prompt = f"""
Based ONLY on the context provided, answer the following user query:
<QUERY>
{user_query}
</QUERY>

Produce the final, structured answer here:
<FINAL_ANSWER>
"""

    # ============================================================
    # Apply Qwen Chat Template (Required for Correct Inference)
    # ============================================================
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # The inputs need to be moved to the correct device
    inputs = tokenizer.apply_chat_template(
        messages,
        return_tensors="pt",
        tokenize=True,
        add_generation_prompt=True # Essential for Instruct models
    ).to(model.device) 

    # ============================================================
    # Generate Answer
    # ============================================================
    print("⏳ Generating tokens...")
    outputs = model.generate(
        input_ids=inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id # Use EOS as pad token for generation
    )
    
    # Decode the generated tokens, starting *after* the input prompt
    decoded = tokenizer.decode(outputs[0][inputs.shape[1]:], skip_special_tokens=True)

    # ============================================================
    # Extract ONLY the answer (after <FINAL_ANSWER>)
    # ============================================================
    
    # 1. Clean up potential closing tags and initial whitespace
    answer_only = decoded.strip()
    
    # Simple cleanup of closing tags if the model appended them
    if answer_only.endswith("</FINAL_ANSWER>"):
        answer_only = answer_only.replace("</FINAL_ANSWER>", "").strip()
    
    # A final clean-up to remove any residual structure if the model fails to follow the format perfectly
    # The original script's complex extraction is simplified for the endpoint
    # to primarily rely on the model's strict adherence to the prompt template.
    
    # 2. Check for the no-answer fallback phrase
    if answer_only == "No answer found in the provided context.":
        final_answer = answer_only
    else:
        # Final safety cleanup: remove any text that might accidentally precede the answer, 
        # which can happen if the model re-starts the prompt structure.
        final_answer = answer_only.split("<QUERY>")[0].split("</CONTEXT>")[-1].strip()


    return {
        "user_query": user_query,
        "answer": final_answer
    }