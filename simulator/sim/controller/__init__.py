"""The controllers' logic (Track 3), written the way the firmware will be (contract 9).

hal.py     the only door to the hardware
checks.py  each safety check as a plain function, and the order they run in
zone.py    a zone controller: handle() a command, step() at a fixed rate

Code here uses the standard library and the HAL only: no MQTT, asyncio or farm model, and it never
waits. Rules, HAL names and decisions are in docs/controller-spec.md. The tests are data as well:
tests/rules plays the files in tests/vectors, which the C++ firmware will run later too.
"""
