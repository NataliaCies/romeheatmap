"""Rome district definitions — single source of truth."""
from dataclasses import dataclass

@dataclass(frozen=True)
class District:
    id: str; label: str; lat: float; lon: float
    bbox: tuple[float, float, float, float]
    uhi_modifier: float; humidity_modifier: float
    ndvi_baseline: float; heat_baseline: float

DISTRICT_REGISTRY: dict[str, District] = {d.id: d for d in [
    District("centro","Centro Storico",41.8986,12.4768,(12.460,41.888,12.495,41.910),2.8,-8,0.12,0.82),
    District("trastevere","Trastevere",41.8896,12.4700,(12.452,41.878,12.488,41.903),1.8,-4,0.28,0.62),
    District("parioli","Parioli",41.9188,12.4886,(12.472,41.908,12.508,41.930),-0.5,5,0.58,0.38),
    District("testaccio","Testaccio",41.8780,12.4762,(12.466,41.868,12.490,41.890),2.0,-5,0.24,0.65),
    District("eur","EUR",41.8308,12.4622,(12.440,41.815,12.490,41.848),0.5,2,0.45,0.48),
    District("garbatella","Garbatella",41.8563,12.4806,(12.465,41.846,12.498,41.868),0.9,-2,0.38,0.52),
    District("pigneto","Pigneto",41.8872,12.5292,(12.515,41.877,12.545,41.899),1.5,-3,0.30,0.60),
    District("villa_borghese","Villa Borghese",41.9139,12.4921,(12.476,41.904,12.512,41.926),-3.2,10,0.82,0.18),
    District("tuscolano","Tuscolano",41.8653,12.5192,(12.503,41.853,12.540,41.878),1.1,-2,0.35,0.55),
    District("monte_mario","Monte Mario",41.9272,12.4517,(12.436,41.917,12.468,41.940),-2.0,8,0.68,0.28),
]}
