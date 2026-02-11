import random

class PeripheralNervousSystem:
    def __init__(self):
        """
        Initialize the sensory inputs for the peripheral nervous system.
        Each input simulates a type of stimulus detected by peripheral receptors.
        """
        self.sensory_inputs = [
            "touch: soft fabric",
            "touch: sharp object",
            "temperature: warm breeze",
            "temperature: cold surface",
            "pain: stubbed toe",
            "pressure: gentle hug",
            "pressure: hard push"
        ]

    def detect_environment(self):
        """
        Simulate the detection of environmental stimuli by selecting a random sensory input.
        """
        stimulus = random.choice(self.sensory_inputs)
        return stimulus