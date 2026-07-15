"""VAD provider interface — DS-C owns this."""

from abc import ABC, abstractmethod


class VADProvider(ABC):
    @abstractmethod
    async def is_speech(self, pcm_frame: bytes) -> bool: ...

    @abstractmethod
    async def is_turn_complete(self, pcm: bytes) -> bool: ...


class SileroVADProvider(VADProvider):
    """Energy-gate VAD. Replace body with real silero-vad model."""
    #needs to be highly sensitive so it can capture the quiet beginning of a sentence or soft speech.
    def __init__(self, energy_threshold: float = 300.0):
        self.threshold = energy_threshold

    async def is_speech(self, pcm_frame: bytes) -> bool:
        import struct
        # Raw audio bytes are streamed as 16-bit signed integers (mono). 16 bits = 2 bytes per sample.
        # struct.unpack: Converts raw binary byte strings into a list of native Python integers.
        # f"{len(pcm_frame) // 2}h": Generates a dynamic formatting string.
        # h represents a 16-bit signed integer (short).
        # len(pcm_frame) // 2 calculates the total number of individual samples in the frame.

        if len(pcm_frame) < 2:
            return False
        samples = struct.unpack(f"{len(pcm_frame) // 2}h", pcm_frame)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return rms > self.threshold  # for neglecting the noise and silence.

    # Why it returns True: In the base vad.py class, this is a placeholder or interface. It is set to default-allow (return True) so that if advanced linguistic analysis is disabled, the pipeline still functions.
    # Delegation to SmartTurnProvider: In production, this call is intercepted and processed by SmartTurn (pipeline/vad/smart_turn.py).

    async def is_turn_complete(self, pcm: bytes) -> bool:
        # Delegated to SmartTurnProvider
        return True


silero_vad_provider = SileroVADProvider()
