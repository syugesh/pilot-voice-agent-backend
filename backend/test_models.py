
import asyncio
import logging
import os
import sys
import time

import numpy as np

"""
PILOT Machine Learning Model Diagnostic & Verification Suite.
Loads and isolates every single ML pipeline model, testing them under synthetic fixtures.
"""
# Configure basic console logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("model_check")

# Initialize and import services


sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def print_banner(title: str):
    print("\n" + "=" * 60)
    print(f" TESTING SUB-SYSTEM: {title}")
    print("=" * 60)


async def test_stt_whisper():
    print_banner("1. FAST-WHISPER ASR (Speech-To-Text)")
    from backend.services.stt import whisper_provider

    t0 = time.time()
    logger.info("Loading Whisper model into memory...")
    whisper_provider.load()
    logger.info(f"Whisper model loaded successfully in {time.time() - t0:.2f} seconds.")

    # Generate 1.5 seconds of synthetic audio (16kHz, mono, Int16) containing simple sine wave
    duration = 1.5
    sample_rate = 16000
    t_space = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # 440 Hz tone
    samples = np.sin(2 * np.pi * 440.0 * t_space) * 10000
    pcm_bytes = samples.astype(np.int16).tobytes()

    t0 = time.time()
    logger.info("Transcribing synthetic audio segment...")
    result_text = await whisper_provider.transcribe(pcm_bytes)
    latency = (time.time() - t0) * 1000

    print("\n[ASR STATUS] -> SUCCESS ✓")
    print(f" - Latency: {latency:.1f}ms")
    print(
        f" - Transcription result: {result_text!r} (expected to be blank/filtered due to no speech harmonics)"
    )


async def test_wespeaker_embedding():
    print_banner("2. WESPEAKER VOICE EMBEDDING EXTRACTOR")
    from backend.services.enrollment import embed_provider

    # Generate mock speech PCM data
    duration = 2.0
    sample_rate = 16000
    t_space = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    # A vocal frequency range (e.g. 150 Hz voice fundamental)
    samples = np.sin(2 * np.pi * 150.0 * t_space) * 8000
    pcm_bytes = samples.astype(np.int16).tobytes()

    t0 = time.time()
    logger.info("Extracting voice biometric embedding...")
    vector = embed_provider.extract(pcm_bytes)
    latency = (time.time() - t0) * 1000

    print("\n[BIOMETRIC EMBEDDING STATUS] -> SUCCESS ✓")
    print(f" - Latency: {latency:.1f}ms")
    print(f" - Vector Dimensions: {vector.shape}")
    print(f" - Embedding Type: {vector.dtype}")
    print(f" - L2 Normalized Check (should be ~1.0): {np.linalg.norm(vector):.4f}")

    # Test different audio to verify different embeddings
    samples2 = np.sin(2 * np.pi * 320.0 * t_space) * 8000
    pcm_bytes2 = samples2.astype(np.int16).tobytes()
    vector2 = embed_provider.extract(pcm_bytes2)

    from backend.services.enrollment import cosine_similarity

    sim = cosine_similarity(vector, vector2)
    print(f" - Cosine Similarity across different pitches (expected to be low/different): {sim:.3f}")


async def test_front_llm_gemini():
    print_banner("3. FRONT LLM ROUTER (Gemini API)")
    from backend.services.front_llm import front_llm_provider

    t0 = time.time()
    logger.info("Loading Gemini FrontLLM router client...")
    front_llm_provider.load()

    test_utterance = "please go to the next slide"
    logger.info(f'Classifying utterance: "{test_utterance}"')

    t0 = time.time()
    result = await front_llm_provider.classify(
        text=test_utterance, speaker_id="pawan", role="developer", context=[]
    )
    latency = (time.time() - t0) * 1000

    print("\n[FRONT LLM INTENT STATUS] -> SUCCESS ✓")
    print(f" - Latency: {latency:.1f}ms")
    print(f" - Routed Action: {result.get('action')!r}")
    print(f" - Spoken Preamble: {result.get('preamble')!r}")
    print(f" - Target Tool: {result.get('tool')!r}")
    print(f" - Extracted Arguments: {result.get('args')}")


async def test_background_agent_gemini():
    print_banner("4. BACKGROUND AGENT (Gemini API)")
    from backend.services.bg_agent import generate_reply

    mock_tool = "flight_search"
    mock_result = {
        "status": "ok",
        "flights": [{"airline": "Delta", "price": "$380", "dep": "10:15 AM", "arr": "1:45 PM"}],
    }

    logger.info("Requesting background tool summarization reply from Gemini API...")
    t0 = time.time()
    reply = await generate_reply(mock_tool, mock_result)
    latency = (time.time() - t0) * 1000

    print("\n[BACKGROUND AGENT SUMMARY STATUS] -> SUCCESS ✓")
    print(f" - Latency: {latency:.1f}ms")
    print(f' - Spoken summary text: "{reply}"')


async def main():
    print("\n" + "#" * 70)
    print("      PILOT PIPELINE MACHINE LEARNING MODEL HEALTH INTEGRITY CHECK")
    print("#" * 70)

    try:
        await test_stt_whisper()
    except Exception as e:
        logger.error(f"STT Whisper failed: {e}", exc_info=True)

    try:
        await test_wespeaker_embedding()
    except Exception as e:
        logger.error(f"Embedding failed: {e}", exc_info=True)

    try:
        await test_front_llm_gemini()
    except Exception as e:
        logger.error(f"FrontLLM failed: {e}", exc_info=True)

    try:
        await test_background_agent_gemini()
    except Exception as e:
        logger.error(f"Background Agent failed: {e}", exc_info=True)

    print("\n" + "#" * 70)
    print("               MODEL DIAGNOSTIC CHECK COMPLETED")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
