"""Farm model (Track 3): the physics of the simulated polyhouse.

water.py      reservoir volume, dissolved nutrients, pH, temperature; step and dose
nutrients.py  EC from nutrient mass, pH response to dosing (provisional)
crops.py      plant water and nutrient uptake by crop, stage and heat
climate.py    solution temperature following the air
mixing.py     volume-weighted mixing and exact first-order relaxation
params.py     typed parameters; every number comes from the config pack

No module here imports the network, the clock or the controllers. The
controllers never see this package; they read sensors through the HAL.
"""
