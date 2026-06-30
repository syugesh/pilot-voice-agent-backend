"""VAD provider interface — DS-C owns this."""
from abc import ABC, abstractmethod


class VADProvider(ABC):
    @abstractmethod
    async def is_speech(self, pcm_frame: bytes) -> bool: ...

    @abstractmethod
    async def is_turn_complete(self, pcm: bytes) -> bool: ...


class SileroVADProvider(VADProvider):
    """Energy-gate VAD. Replace body with real silero-vad model."""

    def __init__(self, energy_threshold: float = 300.0):
        self.threshold = energy_threshold

    async def is_speech(self, pcm_frame: bytes) -> bool:
        import struct
        if len(pcm_frame) < 2:
            return False
        samples = struct.unpack(f"{len(pcm_frame) // 2}h", pcm_frame)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return rms > self.threshold

    async def is_turn_complete(self, pcm: bytes) -> bool:
        # Delegated to SmartTurnProvider
        return True


silero_vad_provider = SileroVADProvider()
